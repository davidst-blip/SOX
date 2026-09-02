from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.auth import hash_password
from backend.db.models import UserModel
from backend.db.session import get_db
from backend.dependencies import require_role
from engine.schemas import PerionEntity, Role

router = APIRouter(prefix="/users", tags=["users"])


class CreateUserRequest(BaseModel):
    email: str
    full_name: str
    password: str
    role: Role
    entity: PerionEntity


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    entity: str
    is_active: bool


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    body: CreateUserRequest,
    db: Session = Depends(get_db),
    _admin=Depends(require_role(Role.ADMIN)),
):
    if db.query(UserModel).filter(UserModel.email == body.email).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = UserModel(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=body.role.value,
        entity=body.entity.value,
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        entity=user.entity,
        is_active=user.is_active,
    )


@router.get("", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    _admin=Depends(require_role(Role.ADMIN)),
):
    users = db.query(UserModel).filter(UserModel.is_active.is_(True)).all()
    return [
        UserResponse(id=str(u.id), email=u.email, full_name=u.full_name, role=u.role, entity=u.entity, is_active=u.is_active)
        for u in users
    ]
