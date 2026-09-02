"""
Controls router.

POST /controls/upload-rcm   — upload RCM Excel, parse + normalize, store controls
GET  /controls              — list controls (filterable by entity, process, risk_level)
GET  /controls/{id}         — single control detail
PATCH /controls/{id}/assign — assign owner_id / reviewer_id
"""

import hashlib
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.dependencies import get_current_user, require_role
from backend.db.models import ControlModel
from backend.db.session import get_db
from backend.settings import get_settings
from engine.rcm_parser.excel_parser import parse_rcm_excel
from engine.rcm_parser.llm_normalizer import normalize_row
from engine.schemas import PerionEntity, Role

router = APIRouter(prefix="/controls", tags=["controls"])

UPLOAD_DIR = Path("uploads/rcm")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}


# ─── Response models ──────────────────────────────────────────────────────────


class ControlSummary(BaseModel):
    id: str
    code: str
    name: str
    entity: str
    process: str | None
    frequency: str
    risk_level: str
    needs_human_review: bool


class ControlDetail(ControlSummary):
    description: str
    control_type: str
    nature: str
    coso_component: str | None
    assertions: list
    owner: str | None
    reviewer: str | None
    owner_id: str | None
    reviewer_id: str | None
    attributes: list
    parsed_at: str
    parser_version: str
    review_reasons: list
    raw_rcm_row: dict


class UploadResult(BaseModel):
    total_rows: int
    controls_created: int
    controls_skipped: int   # already in DB by code+entity
    needs_review: int
    file_hash: str


class AssignRequest(BaseModel):
    owner_id: str | None = None
    reviewer_id: str | None = None


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _model_to_summary(c: ControlModel) -> ControlSummary:
    return ControlSummary(
        id=str(c.id),
        code=c.code,
        name=c.name,
        entity=c.entity,
        process=c.process,
        frequency=c.frequency,
        risk_level=c.risk_level,
        needs_human_review=c.needs_human_review,
    )


def _model_to_detail(c: ControlModel) -> ControlDetail:
    return ControlDetail(
        id=str(c.id),
        code=c.code,
        name=c.name,
        entity=c.entity,
        process=c.process,
        frequency=c.frequency,
        risk_level=c.risk_level,
        needs_human_review=c.needs_human_review,
        description=c.description,
        control_type=c.control_type,
        nature=c.nature,
        coso_component=c.coso_component,
        assertions=c.assertions or [],
        owner=c.owner_name,
        reviewer=c.reviewer_name,
        owner_id=str(c.owner_id) if c.owner_id else None,
        reviewer_id=str(c.reviewer_id) if c.reviewer_id else None,
        attributes=c.attributes or [],
        parsed_at=c.parsed_at.isoformat() if c.parsed_at else "",
        parser_version=c.parser_version,
        review_reasons=c.review_reasons or [],
        raw_rcm_row=c.raw_rcm_row or {},
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/upload-rcm", response_model=UploadResult, status_code=status.HTTP_201_CREATED)
async def upload_rcm(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(Role.ADMIN)),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File must be .xlsx or .xlsm, got {suffix!r}",
        )

    # Save to disk
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    file_hash = _sha256(dest)

    # Parse Excel → raw rows
    try:
        raw_rows = parse_rcm_excel(dest)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Parse error: {exc}")

    if not raw_rows:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No data rows found in file")

    settings = get_settings()
    if not settings.anthropic_api_key:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Anthropic API key not configured")

    anthropic = Anthropic(api_key=settings.anthropic_api_key)

    created = 0
    skipped = 0
    needs_review = 0
    batch_cache: dict = {}

    for raw_row in raw_rows:
        try:
            control = normalize_row(raw_row, anthropic, _cache=batch_cache)
        except Exception:
            # If LLM call fails for a row, flag it and continue
            skipped += 1
            continue

        # Skip if this code+entity already exists
        exists = (
            db.query(ControlModel)
            .filter(ControlModel.code == control.code, ControlModel.entity == str(control.entity))
            .first()
        )
        if exists:
            skipped += 1
            continue

        assertions = [str(a) for a in control.assertions]
        attributes = [a.model_dump() for a in control.attributes]

        m = ControlModel(
            code=control.code,
            name=control.name,
            description=control.description,
            entity=str(control.entity),
            control_type=str(control.control_type),
            nature=str(control.nature),
            frequency=str(control.frequency),
            risk_level=str(control.risk_level),
            process=control.process,
            sub_process=control.sub_process,
            coso_component=str(control.coso_component) if control.coso_component else None,
            assertions=assertions,
            attributes=attributes,
            owner_name=control.owner,
            reviewer_name=control.reviewer,
            raw_rcm_row=control.raw_rcm_row,
            parsed_at=control.parsed_at,
            parser_version=control.parser_version,
            needs_human_review=control.needs_human_review,
            review_reasons=control.review_reasons,
        )
        db.add(m)
        created += 1
        if control.needs_human_review:
            needs_review += 1

    db.commit()

    return UploadResult(
        total_rows=len(raw_rows),
        controls_created=created,
        controls_skipped=skipped,
        needs_review=needs_review,
        file_hash=file_hash,
    )


@router.get("", response_model=list[ControlSummary])
def list_controls(
    entity: str | None = Query(default=None),
    process: str | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    needs_review: bool | None = Query(default=None),
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    q = db.query(ControlModel)
    if entity:
        q = q.filter(ControlModel.entity == entity)
    if process:
        q = q.filter(ControlModel.process == process)
    if risk_level:
        q = q.filter(ControlModel.risk_level == risk_level)
    if needs_review is not None:
        q = q.filter(ControlModel.needs_human_review == needs_review)
    return [_model_to_summary(c) for c in q.order_by(ControlModel.code).all()]


@router.get("/{control_id}", response_model=ControlDetail)
def get_control(
    control_id: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    try:
        uid = uuid.UUID(control_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid UUID")

    c = db.query(ControlModel).filter(ControlModel.id == uid).first()
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")
    return _model_to_detail(c)


@router.patch("/{control_id}/assign", response_model=ControlDetail)
def assign_control(
    control_id: str,
    body: AssignRequest,
    db: Session = Depends(get_db),
    _admin=Depends(require_role(Role.ADMIN)),
):
    try:
        uid = uuid.UUID(control_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid UUID")

    c = db.query(ControlModel).filter(ControlModel.id == uid).first()
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Control not found")

    if body.owner_id is not None:
        c.owner_id = uuid.UUID(body.owner_id)
    if body.reviewer_id is not None:
        c.reviewer_id = uuid.UUID(body.reviewer_id)

    db.commit()
    db.refresh(c)
    return _model_to_detail(c)
