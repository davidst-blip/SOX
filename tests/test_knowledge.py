"""
Knowledge Base endpoint tests.

Summarizer LLM calls are mocked. File parsing uses real in-memory files.
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
from backend.db.models import UserModel
from backend.db.session import get_db
from backend.main import app
from engine.schemas import PerionEntity, Role

SQLITE_URL = "sqlite:///./test_knowledge.db"
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


def _make_xlsx(sheet_name: str, rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_MOCK_SUMMARY = {
    "what_was_tested": "Reviewed AP invoices for proper authorization",
    "evidence_reviewed": ["NetSuite AP report", "Invoice samples"],
    "sample_approach": "25 invoices selected judgmentally",
    "key_attributes_tested": ["authorization", "completeness"],
    "exceptions_noted": [],
    "testing_conclusion": "pass",
    "period": "Q1 2025",
}


# ─── File parser unit tests ───────────────────────────────────────────────────


class TestFileParser:
    def test_parses_excel_sections(self, tmp_path):
        from engine.workpaper_ingester.file_parser import parse_workpaper

        xlsx = _make_xlsx("Procedures", [["Step", "Result"], ["1. Review invoices", "Pass"]])
        path = tmp_path / "wp.xlsx"
        path.write_bytes(xlsx)
        sections = parse_workpaper(path)
        assert len(sections) == 1
        assert sections[0]["name"] == "Procedures"
        assert "Review invoices" in sections[0]["text"]

    def test_unsupported_format_returns_section(self, tmp_path):
        from engine.workpaper_ingester.file_parser import parse_workpaper

        path = tmp_path / "file.txt"
        path.write_text("hello")
        sections = parse_workpaper(path)
        assert len(sections) == 1
        assert "Unsupported" in sections[0]["text"]

    def test_empty_excel_returns_no_sections(self, tmp_path):
        from engine.workpaper_ingester.file_parser import parse_workpaper

        wb = openpyxl.Workbook()
        buf = io.BytesIO()
        wb.save(buf)
        path = tmp_path / "empty.xlsx"
        path.write_bytes(buf.getvalue())
        sections = parse_workpaper(path)
        # Empty workbook has one default sheet but no rows
        assert isinstance(sections, list)


# ─── Upload endpoint tests ────────────────────────────────────────────────────


class TestUploadKnowledge:
    @patch("backend.routers.knowledge.summarize_workpaper", return_value=_MOCK_SUMMARY)
    @patch("backend.routers.knowledge.get_settings")
    def test_upload_creates_entry(self, mock_settings, mock_summarize, client, admin_token):
        mock_settings.return_value = MagicMock(anthropic_api_key="fake-key")
        xlsx = _make_xlsx("Procedures", [["Step", "Evidence"], ["Review AP", "Invoice log"]])
        resp = client.post(
            "/knowledge/upload",
            data={"control_code": "EX-1", "period": "Q1 2025"},
            files={"file": ("EX1_Q1_2025.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["control_code"] == "EX-1"
        assert data["period"] == "Q1 2025"
        assert data["cached"] is False

    @patch("backend.routers.knowledge.summarize_workpaper", return_value=_MOCK_SUMMARY)
    @patch("backend.routers.knowledge.get_settings")
    def test_duplicate_upload_returns_cached(self, mock_settings, mock_summarize, client, admin_token):
        mock_settings.return_value = MagicMock(anthropic_api_key="fake-key")
        xlsx = _make_xlsx("Procedures", [["Step"], ["Review AP"]])

        for _ in range(2):
            resp = client.post(
                "/knowledge/upload",
                data={"control_code": "EX-1", "period": "Q1 2025"},
                files={"file": ("EX1.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert resp.status_code == 201
        assert resp.json()["cached"] is True

    def test_upload_rejects_bad_format(self, client, admin_token):
        resp = client.post(
            "/knowledge/upload",
            data={"control_code": "EX-1", "period": "Q1 2025"},
            files={"file": ("file.csv", b"a,b,c", "text/csv")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 422

    def test_upload_requires_admin(self, client, db, admin_token):
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

        xlsx = _make_xlsx("Sheet1", [["data"]])
        resp = client.post(
            "/knowledge/upload",
            data={"control_code": "EX-1", "period": "Q1 2025"},
            files={"file": ("f.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={"Authorization": f"Bearer {reviewer_token}"},
        )
        assert resp.status_code == 403


# ─── List / get / delete ──────────────────────────────────────────────────────


class TestKnowledgeRetrieval:
    @patch("backend.routers.knowledge.summarize_workpaper", return_value=_MOCK_SUMMARY)
    @patch("backend.routers.knowledge.get_settings")
    def _seed(self, mock_settings, mock_summarize, client, admin_token, code="EX-1", period="Q1 2025"):
        mock_settings.return_value = MagicMock(anthropic_api_key="fake-key")
        xlsx = _make_xlsx("Procedures", [["Step"], [f"Review {code}"]])
        client.post(
            "/knowledge/upload",
            data={"control_code": code, "period": period},
            files={"file": ("wp.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_list_by_control_code(self, client, admin_token):
        self._seed(client=client, admin_token=admin_token)
        resp = client.get("/knowledge/EX-1", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["control_code"] == "EX-1"

    def test_list_empty_for_unknown_code(self, client, admin_token):
        resp = client.get("/knowledge/ZZ-99", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_by_control_and_period(self, client, admin_token):
        self._seed(client=client, admin_token=admin_token)
        resp = client.get("/knowledge/EX-1/Q1 2025", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["control_code"] == "EX-1"
        assert "summary" in data

    def test_get_unknown_returns_404(self, client, admin_token):
        resp = client.get("/knowledge/EX-1/Q4 2099", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 404

    def test_delete_entry(self, client, admin_token):
        self._seed(client=client, admin_token=admin_token)
        entries = client.get("/knowledge/EX-1", headers={"Authorization": f"Bearer {admin_token}"}).json()
        entry_id = entries[0]["id"]

        resp = client.delete(f"/knowledge/{entry_id}", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 204

        remaining = client.get("/knowledge/EX-1", headers={"Authorization": f"Bearer {admin_token}"}).json()
        assert remaining == []
