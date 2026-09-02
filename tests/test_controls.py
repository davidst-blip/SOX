"""
Controls endpoint tests.

LLM calls are mocked — no Anthropic key needed.
Upload uses an in-memory Excel file built with openpyxl.
"""

import io
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.auth import hash_password
from backend.db.base import Base
from backend.db.models import ControlModel, UserModel
from backend.db.session import get_db
from backend.main import app
from engine.schemas import PerionEntity, Role

SQLITE_URL = "sqlite:///./test_controls.db"
engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    app.dependency_overrides[get_db] = override_get_db
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def db():
    db = TestingSession()
    yield db
    db.close()


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin_user(db):
    user = UserModel(
        email="davidst@perion.com",
        full_name="David Stavisky",
        hashed_password=hash_password("testpass123"),
        role=Role.ADMIN.value,
        entity=PerionEntity.PERION.value,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def admin_token(client, admin_user):
    resp = client.post("/auth/login", data={"username": "davidst@perion.com", "password": "testpass123"})
    return resp.json()["access_token"]


def _make_rcm_xlsx(rows: list[dict]) -> bytes:
    """Build a minimal in-memory RCM Excel file."""
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = ["Control No.", "Process", "Control Description", "Frequency", "Type", "Nature", "Risk Level", "Owner", "Entity"]
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, "") for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _mock_control_from_row(raw_row, client, _cache=None):
    """Fake normalizer that returns a deterministic Control without calling LLM."""
    from datetime import datetime, timezone
    from engine.schemas import Control, ControlNature, ControlType, Frequency, PerionEntity, RiskLevel

    code = raw_row.get("Control No.", "EX-99")
    return Control(
        code=code,
        name=f"Test control {code}",
        description="Sample description long enough to pass validation threshold for review",
        entity=PerionEntity.PERION,
        control_type=ControlType.PREVENTIVE,
        nature=ControlNature.MANUAL,
        frequency=Frequency.MONTHLY,
        risk_level=RiskLevel.KEY,
        parsed_at=datetime.now(timezone.utc),
        parser_version="0.2.0",
        needs_human_review=False,
        review_reasons=[],
    )


# ─── Excel parser unit tests ──────────────────────────────────────────────────


class TestExcelParser:
    def test_parses_header_and_rows(self, tmp_path):
        from engine.rcm_parser.excel_parser import parse_rcm_excel

        xlsx_bytes = _make_rcm_xlsx([
            {"Control No.": "EX-1", "Process": "Expenses", "Control Description": "AP review", "Frequency": "Monthly"},
            {"Control No.": "EX-4", "Process": "Expenses", "Control Description": "PO matching", "Frequency": "Daily"},
        ])
        path = tmp_path / "test.xlsx"
        path.write_bytes(xlsx_bytes)
        rows = parse_rcm_excel(path)
        assert len(rows) == 2
        assert rows[0]["Control No."] == "EX-1"
        assert rows[1]["Control No."] == "EX-4"

    def test_empty_file_returns_empty(self, tmp_path):
        from engine.rcm_parser.excel_parser import parse_rcm_excel

        wb = openpyxl.Workbook()
        buf = io.BytesIO()
        wb.save(buf)
        path = tmp_path / "empty.xlsx"
        path.write_bytes(buf.getvalue())
        rows = parse_rcm_excel(path)
        assert rows == []

    def test_skips_blank_rows(self, tmp_path):
        from engine.rcm_parser.excel_parser import parse_rcm_excel

        xlsx_bytes = _make_rcm_xlsx([
            {"Control No.": "EX-1", "Control Description": "real row"},
            {},  # blank
            {"Control No.": "EX-2", "Control Description": "another real row"},
        ])
        path = tmp_path / "blanks.xlsx"
        path.write_bytes(xlsx_bytes)
        rows = parse_rcm_excel(path)
        non_blank = [r for r in rows if any(v for v in r.values())]
        assert len(non_blank) == 2


# ─── Upload endpoint tests ─────────────────────────────────────────────────────


class TestUploadRCM:
    @patch("backend.routers.controls.normalize_row", side_effect=_mock_control_from_row)
    @patch("backend.routers.controls.get_settings")
    def test_upload_creates_controls(self, mock_settings, mock_normalize, client, admin_token):
        mock_settings.return_value = MagicMock(anthropic_api_key="fake-key")
        xlsx_bytes = _make_rcm_xlsx([
            {"Control No.": "EX-1", "Process": "Expenses", "Control Description": "AP review", "Frequency": "Monthly"},
            {"Control No.": "EX-4", "Process": "Expenses", "Control Description": "PO matching", "Frequency": "Daily"},
        ])
        resp = client.post(
            "/controls/upload-rcm",
            files={"file": ("rcm.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["controls_created"] == 2
        assert data["total_rows"] == 2
        assert data["controls_skipped"] == 0

    @patch("backend.routers.controls.normalize_row", side_effect=_mock_control_from_row)
    @patch("backend.routers.controls.get_settings")
    def test_upload_skips_duplicate_codes(self, mock_settings, mock_normalize, client, admin_token, db):
        mock_settings.return_value = MagicMock(anthropic_api_key="fake-key")
        # Pre-insert EX-1
        db.add(ControlModel(
            code="EX-1",
            name="Existing",
            description="already here",
            entity=PerionEntity.PERION.value,
            control_type="preventive",
            nature="manual",
            frequency="monthly",
            risk_level="key",
            parsed_at=datetime.now(timezone.utc),
            parser_version="0.1.0",
        ))
        db.commit()

        xlsx_bytes = _make_rcm_xlsx([
            {"Control No.": "EX-1", "Control Description": "duplicate"},
            {"Control No.": "EX-99", "Control Description": "new one"},
        ])
        resp = client.post(
            "/controls/upload-rcm",
            files={"file": ("rcm.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["controls_created"] == 1
        assert data["controls_skipped"] == 1

    def test_upload_rejects_non_excel(self, client, admin_token):
        resp = client.post(
            "/controls/upload-rcm",
            files={"file": ("rcm.csv", b"a,b,c", "text/csv")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    def test_upload_requires_admin(self, client, db, admin_token):
        # Create a reviewer
        reviewer = UserModel(
            email="eldar@perion.com",
            full_name="Eldar",
            hashed_password=hash_password("pass123"),
            role=Role.REVIEWER.value,
            entity=PerionEntity.PERION.value,
            created_at=datetime.now(timezone.utc),
        )
        db.add(reviewer)
        db.commit()
        reviewer_token = TestClient(app).post("/auth/login", data={"username": "eldar@perion.com", "password": "pass123"}).json()["access_token"]

        xlsx_bytes = _make_rcm_xlsx([{"Control No.": "EX-1"}])
        resp = client.post(
            "/controls/upload-rcm",
            files={"file": ("rcm.xlsx", xlsx_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert resp.status_code == 403


# ─── List / get endpoints ─────────────────────────────────────────────────────


class TestListControls:
    def _seed(self, db, code="EX-1", entity=None):
        m = ControlModel(
            code=code,
            name=f"Control {code}",
            description="desc",
            entity=entity or PerionEntity.PERION.value,
            control_type="preventive",
            nature="manual",
            frequency="monthly",
            risk_level="key",
            parsed_at=datetime.now(timezone.utc),
            parser_version="0.2.0",
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        return m

    def test_list_returns_all(self, client, admin_token, db):
        self._seed(db, "EX-1")
        self._seed(db, "EX-4")
        resp = client.get("/controls", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_filter_by_risk_level(self, client, admin_token, db):
        self._seed(db, "EX-1")
        m = self._seed(db, "EX-4")
        m.risk_level = "non_key"
        db.commit()
        resp = client.get("/controls?risk_level=non_key", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["code"] == "EX-4"

    def test_get_by_id(self, client, admin_token, db):
        m = self._seed(db)
        resp = client.get(f"/controls/{m.id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert resp.json()["code"] == "EX-1"

    def test_get_unknown_id_returns_404(self, client, admin_token):
        import uuid
        resp = client.get(f"/controls/{uuid.uuid4()}", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 404

    def test_assign_owner(self, client, admin_token, admin_user, db):
        m = self._seed(db)
        resp = client.patch(
            f"/controls/{m.id}/assign",
            json={"owner_id": str(admin_user.id)},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["owner_id"] == str(admin_user.id)
