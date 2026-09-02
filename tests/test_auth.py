"""
Auth endpoint tests.
Uses an in-memory SQLite DB so no Postgres needed to run these tests.
"""

from datetime import datetime, timezone

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

SQLITE_URL = "sqlite:///./test.db"
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
def client():
    return TestClient(app)


@pytest.fixture
def admin_token(client, admin_user):
    resp = client.post("/auth/login", data={"username": "davidst@perion.com", "password": "testpass123"})
    return resp.json()["access_token"]


class TestLogin:
    def test_login_success(self, client, admin_user):
        resp = client.post("/auth/login", data={"username": "davidst@perion.com", "password": "testpass123"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()
        assert resp.json()["token_type"] == "bearer"

    def test_login_wrong_password(self, client, admin_user):
        resp = client.post("/auth/login", data={"username": "davidst@perion.com", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_unknown_email(self, client):
        resp = client.post("/auth/login", data={"username": "nobody@perion.com", "password": "testpass123"})
        assert resp.status_code == 401


class TestMe:
    def test_me_returns_user(self, client, admin_token):
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "davidst@perion.com"
        assert data["role"] == "admin"

    def test_me_rejects_no_token(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_me_rejects_bad_token(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401


class TestCreateUser:
    def test_admin_can_create_user(self, client, admin_token):
        resp = client.post(
            "/users",
            json={
                "email": "eldar@perion.com",
                "full_name": "Eldar",
                "password": "pass123",
                "role": "reviewer",
                "entity": "Perion Network Ltd.",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        assert resp.json()["email"] == "eldar@perion.com"
        assert resp.json()["role"] == "reviewer"

    def test_duplicate_email_rejected(self, client, admin_token, admin_user):
        resp = client.post(
            "/users",
            json={
                "email": "davidst@perion.com",
                "full_name": "Duplicate",
                "password": "pass123",
                "role": "control_owner",
                "entity": "Perion Network Ltd.",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 409

    def test_non_admin_cannot_create_user(self, client, db, admin_token):
        # Create a control owner
        owner = UserModel(
            email="ap@perion.com",
            full_name="AP Manager",
            hashed_password=hash_password("pass123"),
            role=Role.CONTROL_OWNER.value,
            entity=PerionEntity.PERION.value,
            created_at=datetime.now(timezone.utc),
        )
        db.add(owner)
        db.commit()

        owner_token = client.post("/auth/login", data={"username": "ap@perion.com", "password": "pass123"}).json()["access_token"]

        resp = client.post(
            "/users",
            json={"email": "new@perion.com", "full_name": "New", "password": "p", "role": "reviewer", "entity": "Perion Network Ltd."},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert resp.status_code == 403
