"""
Knowledge Base router.

POST /knowledge/upload     — upload a historical workpaper, parse + summarize, store
GET  /knowledge/{control_code} — list knowledge entries for a control (used by test plan generator)
GET  /knowledge/{control_code}/{period} — single entry detail
DELETE /knowledge/{entry_id}   — remove an entry (admin only)
"""

import hashlib
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.db.models import KnowledgeEntryModel
from backend.db.session import get_db
from backend.dependencies import get_current_user, require_role
from backend.settings import get_settings
from engine.schemas import Role
from engine.workpaper_ingester.file_parser import parse_workpaper
from engine.workpaper_ingester.summarizer import summarize_workpaper

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

UPLOAD_DIR = Path("uploads/knowledge")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".docx", ".pdf"}


# ─── Response models ──────────────────────────────────────────────────────────


class KnowledgeSummary(BaseModel):
    id: str
    control_code: str
    period: str
    source_filename: str
    file_hash: str
    testing_conclusion: str | None
    ingested_at: str


class KnowledgeDetail(KnowledgeSummary):
    summary: dict
    section_count: int


class UploadKnowledgeResult(BaseModel):
    id: str
    control_code: str
    period: str
    cached: bool   # True if this file_hash already existed — skipped re-parsing


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_summary(e: KnowledgeEntryModel) -> KnowledgeSummary:
    conclusion = (e.summary or {}).get("testing_conclusion") if e.summary else None
    return KnowledgeSummary(
        id=str(e.id),
        control_code=e.control_code,
        period=e.period,
        source_filename=e.source_filename,
        file_hash=e.file_hash,
        testing_conclusion=conclusion,
        ingested_at=e.ingested_at.isoformat(),
    )


def _to_detail(e: KnowledgeEntryModel) -> KnowledgeDetail:
    conclusion = (e.summary or {}).get("testing_conclusion") if e.summary else None
    return KnowledgeDetail(
        id=str(e.id),
        control_code=e.control_code,
        period=e.period,
        source_filename=e.source_filename,
        file_hash=e.file_hash,
        testing_conclusion=conclusion,
        ingested_at=e.ingested_at.isoformat(),
        summary=e.summary or {},
        section_count=len(e.raw_sections or []),
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/upload", response_model=UploadKnowledgeResult, status_code=status.HTTP_201_CREATED)
async def upload_knowledge(
    file: UploadFile = File(...),
    control_code: str = Form(...),
    period: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(require_role(Role.ADMIN)),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported format {suffix!r}. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    # Save to disk
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    file_hash = _sha256(dest)

    # Check cache — same file already ingested for same control+period
    existing = (
        db.query(KnowledgeEntryModel)
        .filter(
            KnowledgeEntryModel.file_hash == file_hash,
            KnowledgeEntryModel.control_code == control_code.upper(),
            KnowledgeEntryModel.period == period,
        )
        .first()
    )
    if existing:
        dest.unlink(missing_ok=True)
        return UploadKnowledgeResult(
            id=str(existing.id),
            control_code=existing.control_code,
            period=existing.period,
            cached=True,
        )

    # Parse file
    try:
        sections = parse_workpaper(dest)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Parse error: {exc}")

    # Summarize with Haiku
    settings = get_settings()
    summary: dict = {}
    if settings.anthropic_api_key:
        try:
            anthropic = Anthropic(api_key=settings.anthropic_api_key)
            summary = summarize_workpaper(sections, control_code.upper(), anthropic)
        except Exception:
            summary = {"error": "summarization failed — review manually"}

    # Strip table data from raw_sections before storing (keep text only to save space)
    lean_sections = [{"name": s["name"], "text": s["text"]} for s in sections]

    entry = KnowledgeEntryModel(
        control_code=control_code.upper(),
        period=period,
        source_filename=file.filename or dest.name,
        file_hash=file_hash,
        file_format=suffix.lstrip("."),
        raw_sections=lean_sections,
        summary=summary,
        ingested_at=datetime.now(timezone.utc),
        ingested_by_id=current_user.id if hasattr(current_user, "id") else None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return UploadKnowledgeResult(
        id=str(entry.id),
        control_code=entry.control_code,
        period=entry.period,
        cached=False,
    )


@router.get("/{control_code}", response_model=list[KnowledgeSummary])
def list_knowledge(
    control_code: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    entries = (
        db.query(KnowledgeEntryModel)
        .filter(KnowledgeEntryModel.control_code == control_code.upper())
        .order_by(KnowledgeEntryModel.period.desc())
        .all()
    )
    return [_to_summary(e) for e in entries]


@router.get("/{control_code}/{period}", response_model=KnowledgeDetail)
def get_knowledge(
    control_code: str,
    period: str,
    db: Session = Depends(get_db),
    _user=Depends(get_current_user),
):
    entry = (
        db.query(KnowledgeEntryModel)
        .filter(
            KnowledgeEntryModel.control_code == control_code.upper(),
            KnowledgeEntryModel.period == period,
        )
        .order_by(KnowledgeEntryModel.ingested_at.desc())
        .first()
    )
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No knowledge entry found")
    return _to_detail(entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge(
    entry_id: str,
    db: Session = Depends(get_db),
    _admin=Depends(require_role(Role.ADMIN)),
):
    try:
        uid = uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid UUID")

    entry = db.query(KnowledgeEntryModel).filter(KnowledgeEntryModel.id == uid).first()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    db.delete(entry)
    db.commit()
