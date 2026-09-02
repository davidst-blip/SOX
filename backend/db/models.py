"""
SQLAlchemy ORM models. Mirror the Pydantic schemas in engine/schemas.py.

Rule: queryable fields get dedicated columns; nested/variable-length data
goes in JSON. This keeps queries fast without over-normalizing.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from backend.db.base import Base
from engine.schemas import (
    COSOComponent,
    ControlNature,
    ControlType,
    Frequency,
    PerionEntity,
    RiskLevel,
    Role,
    WorkpaperFormat,
)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(Enum(Role), nullable=False)
    entity: Mapped[str] = mapped_column(Enum(PerionEntity), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owned_controls: Mapped[list["ControlModel"]] = relationship(
        "ControlModel", foreign_keys="ControlModel.owner_id", back_populates="owner"
    )
    reviewer_controls: Mapped[list["ControlModel"]] = relationship(
        "ControlModel", foreign_keys="ControlModel.reviewer_id", back_populates="reviewer_user"
    )


class ControlModel(Base):
    __tablename__ = "controls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    entity: Mapped[str] = mapped_column(Enum(PerionEntity), nullable=False, index=True)

    control_type: Mapped[str] = mapped_column(Enum(ControlType), nullable=False)
    nature: Mapped[str] = mapped_column(Enum(ControlNature), nullable=False)
    frequency: Mapped[str] = mapped_column(Enum(Frequency), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(Enum(RiskLevel), nullable=False, index=True)

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Legacy string fields for imports before user accounts are assigned
    owner_name: Mapped[str] = mapped_column(String(255), nullable=True)
    reviewer_name: Mapped[str] = mapped_column(String(255), nullable=True)

    process: Mapped[str] = mapped_column(String(255), nullable=True)
    sub_process: Mapped[str] = mapped_column(String(255), nullable=True)
    coso_component: Mapped[str] = mapped_column(Enum(COSOComponent), nullable=True)

    # JSON for variable-length nested data
    assertions: Mapped[list] = mapped_column(JSON, default=list)
    attributes: Mapped[list] = mapped_column(JSON, default=list)
    raw_rcm_row: Mapped[dict] = mapped_column(JSON, default=dict)
    review_reasons: Mapped[list] = mapped_column(JSON, default=list)

    parsed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)

    owner: Mapped["UserModel"] = relationship("UserModel", foreign_keys=[owner_id], back_populates="owned_controls")
    reviewer_user: Mapped["UserModel"] = relationship("UserModel", foreign_keys=[reviewer_id], back_populates="reviewer_controls")
    test_plans: Mapped[list["TestPlanModel"]] = relationship("TestPlanModel", back_populates="control")
    workpapers: Mapped[list["WorkpaperModel"]] = relationship("WorkpaperModel", back_populates="control")


class TestPlanModel(Base):
    __tablename__ = "test_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("controls.id"), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False, index=True)

    objective: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    source_inputs: Mapped[list] = mapped_column(JSON, default=list)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_by: Mapped[str] = mapped_column(String(20), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float] = mapped_column(nullable=True)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    control: Mapped["ControlModel"] = relationship("ControlModel", back_populates="test_plans")
    workpapers: Mapped[list["WorkpaperModel"]] = relationship("WorkpaperModel", back_populates="test_plan")


class WorkpaperModel(Base):
    __tablename__ = "workpapers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("controls.id"), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    test_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("test_plans.id"), nullable=True)

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_format: Mapped[str] = mapped_column(Enum(WorkpaperFormat), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    content: Mapped[dict] = mapped_column(JSON, nullable=True)
    parsing_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    parsing_error: Mapped[str] = mapped_column(Text, nullable=True)

    control: Mapped["ControlModel"] = relationship("ControlModel", back_populates="workpapers")
    test_plan: Mapped["TestPlanModel"] = relationship("TestPlanModel", back_populates="workpapers")
    gap_reports: Mapped[list["GapReportModel"]] = relationship("GapReportModel", back_populates="workpaper")


class GapReportModel(Base):
    __tablename__ = "gap_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workpaper_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("workpapers.id"), nullable=False, index=True)
    test_plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("test_plans.id"), nullable=False)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("controls.id"), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False)

    gaps: Mapped[list] = mapped_column(JSON, default=list)
    deterministic_check_results: Mapped[dict] = mapped_column(JSON, default=dict)

    total_gaps: Mapped[int] = mapped_column(Integer, default=0)
    critical_count: Mapped[int] = mapped_column(Integer, default=0)
    material_count: Mapped[int] = mapped_column(Integer, default=0)
    minor_count: Mapped[int] = mapped_column(Integer, default=0)

    overall_status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(50), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=True)
    prompt_hash: Mapped[str] = mapped_column(String(100), nullable=True)

    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=True)
    reviewed_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_notes: Mapped[str] = mapped_column(Text, nullable=True)

    workpaper: Mapped["WorkpaperModel"] = relationship("WorkpaperModel", back_populates="gap_reports")


class KnowledgeEntryModel(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    control_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    period: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)

    # Parsed sections from file_parser (list of {name, text, tables})
    raw_sections: Mapped[list] = mapped_column(JSON, default=list)
    # Haiku-generated summary of what was tested
    summary: Mapped[dict] = mapped_column(JSON, default=dict)

    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
