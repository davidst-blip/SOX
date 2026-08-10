"""
Core schemas for SOX Sentinel.

These Pydantic models are the contract between every layer:
rcm_parser → test_plan_generator → workpaper_ingester → gap_analyzer → DB → API → dashboard.

Design principles:
- Every model has a stable UUID id (assigned by DB, not LLM)
- source fields track provenance: RCM / prior docs / LLM / human
- confidence fields on LLM output feed a human-review queue
- JSONB-friendly: nested models serialize cleanly to Postgres JSONB
- Timestamps in UTC, ISO 8601
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ─── Enums ────────────────────────────────────────────────────────────────────


class Role(str, Enum):
    """
    Platform roles — Perion-internal only.

    ADMIN: David. Configures controls, manages users, oversees the platform.
    CONTROL_OWNER: Performs the control, uploads the workpaper, views their own gap reports.
    REVIEWER: Eldar or designated reviewer. Signs off on workpapers before Big 4.
    """

    ADMIN = "admin"
    CONTROL_OWNER = "control_owner"
    REVIEWER = "reviewer"


class Frequency(str, Enum):
    CONTINUOUS = "continuous"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEMI_ANNUAL = "semi_annual"
    ANNUAL = "annual"
    EVENT_DRIVEN = "event_driven"
    UNKNOWN = "unknown"  # sends to human-review queue


class ControlType(str, Enum):
    PREVENTIVE = "preventive"
    DETECTIVE = "detective"


class ControlNature(str, Enum):
    MANUAL = "manual"
    AUTOMATED = "automated"
    IT_DEPENDENT_MANUAL = "it_dependent_manual"


class RiskLevel(str, Enum):
    KEY = "key"
    NON_KEY = "non_key"
    UNKNOWN = "unknown"


class Source(str, Enum):
    RCM = "rcm"
    PRIOR_WORKPAPER = "prior_workpaper"
    LLM_INFERENCE = "llm_inference"
    HUMAN_INPUT = "human_input"


class GapSeverity(str, Enum):
    CRITICAL = "critical"        # control failure / unauditable
    MATERIAL = "material"        # significant deficiency
    MINOR = "minor"              # housekeeping / improvement
    INFORMATIONAL = "informational"


class GapCategory(str, Enum):
    MISSING_PROCEDURE = "missing_procedure"
    INCOMPLETE_PROCEDURE = "incomplete_procedure"
    MISSING_EVIDENCE = "missing_evidence"
    MISSING_IPE_CA = "missing_ipe_ca"
    MISSING_SIGNOFF = "missing_signoff"
    SOD_VIOLATION = "sod_violation"          # preparer == reviewer
    UNDOCUMENTED_EXCEPTION = "undocumented_exception"
    ATTRIBUTE_NOT_TESTED = "attribute_not_tested"
    DATE_MISSING = "date_missing"
    POPULATION_MISMATCH = "population_mismatch"
    OTHER = "other"


class WorkpaperFormat(str, Enum):
    XLSX = "xlsx"
    XLSM = "xlsm"
    DOCX = "docx"
    PDF = "pdf"
    OTHER = "other"


# Perion-specific: all known PCAOB financial statement assertions
class Assertion(str, Enum):
    EXISTENCE_OCCURRENCE = "E/O"
    COMPLETENESS = "C"
    ACCURACY = "A"
    VALUATION = "V"
    PRESENTATION_DISCLOSURE = "P/D"
    CUTOFF = "CU"


# Perion-specific: entities in scope per scoping dashboard
class PerionEntity(str, Enum):
    PERION = "Perion Network Ltd."
    UNDERTONE_USA = "Undertone USA"
    HIVESTACK_CANADA = "Hivestack Canada"
    VIDAZOO = "Vidazoo"
    CODEFUEL = "CodeFuel"
    OTHER = "Other"  # for new entities that appear post-upload


class COSOComponent(str, Enum):
    CONTROL_ENVIRONMENT = "Control Environment"
    RISK_ASSESSMENT = "Risk Assessment"
    CONTROL_ACTIVITIES = "Control Activities"
    INFORMATION_COMMUNICATION = "Information & Communication"
    MONITORING = "Monitoring"


# ─── Base ─────────────────────────────────────────────────────────────────────


class SOXBase(BaseModel):
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        use_enum_values=True,
    )


# ─── User ─────────────────────────────────────────────────────────────────────


class User(SOXBase):
    """
    A platform user. Perion-internal only — no tenant isolation needed.

    Controls are assigned an owner_id (who performs the control and uploads
    the workpaper) and a reviewer_id (who signs off before Big 4).
    Auth is handled by the backend; password hash is never in this schema.

    Roles:
      ADMIN         — David. Configures controls, manages users.
      CONTROL_OWNER — Performs the control, uploads the workpaper.
      REVIEWER      — Signs off on workpapers before the Big 4 sees them.
    """

    id: UUID = Field(default_factory=uuid4)
    email: str = Field(..., description="Perion email e.g. davidst@perion.com")
    full_name: str
    role: Role
    entity: PerionEntity
    is_active: bool = True
    created_at: datetime
    last_login: datetime | None = None


# ─── Control & attributes ─────────────────────────────────────────────────────


class ControlAttribute(SOXBase):
    """
    A single key-value attribute of a control.

    Separate model (not a dict) so we can track where each attribute came from
    and how confident the parser/LLM is. Source + confidence drives the
    human-review queue: anything LLM_INFERENCE with confidence < 0.8 gets flagged.

    Examples:
      key="population_source",  value="customsearch21965",  source=RCM
      key="threshold_usd",      value="50000",              source=PRIOR_WORKPAPER
      key="reviewer_role",      value="VP Finance",         source=LLM_INFERENCE, confidence=0.7
    """

    key: str = Field(..., min_length=1, max_length=100)
    value: str
    source: Source
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: str | None = None


class Control(SOXBase):
    """
    A SOX control as parsed from the Perion RCM.

    raw_rcm_row preserves the original messy Excel data. Never delete it —
    when the parser improves, old controls can be re-parsed without re-uploading.
    """

    id: UUID = Field(default_factory=uuid4)
    code: str = Field(..., description="e.g. 'EX-1', 'RE-17', 'FR-6', 'HS-FR-6'")
    name: str
    description: str
    entity: PerionEntity

    control_type: ControlType
    nature: ControlNature
    frequency: Frequency
    risk_level: RiskLevel

    # User assignments — UUIDs referencing User.id
    owner_id: UUID | None = None      # control owner (performs + uploads)
    reviewer_id: UUID | None = None   # reviewer (signs off before Big 4)

    # Legacy string fields kept for RCM import where user accounts don't exist yet
    owner: str | None = None
    reviewer: str | None = None

    process: str | None = None
    sub_process: str | None = None
    coso_component: COSOComponent | None = None
    assertions: list[Assertion] = Field(default_factory=list)

    attributes: list[ControlAttribute] = Field(default_factory=list)

    raw_rcm_row: dict[str, str] = Field(
        default_factory=dict,
        description="Original RCM row as-is from Excel. Preserve always.",
    )
    parsed_at: datetime
    parser_version: str
    needs_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


# ─── Test plan ────────────────────────────────────────────────────────────────


class TestStep(SOXBase):
    """
    One step in a control test plan.

    attributes_tested maps back to ControlAttribute.key values. This mapping
    is what lets the gap analyzer check "did you test attribute X?" rather than
    just "did you do something?"
    """

    number: int = Field(..., ge=1)
    procedure: str = Field(..., description="What the tester does")
    expected_evidence: str = Field(..., description="What proves this step was done")
    attributes_tested: list[str] = Field(
        default_factory=list,
        description="ControlAttribute keys covered by this step",
    )
    sample_size_guidance: str | None = None
    estimated_minutes: int | None = None


class TestPlan(SOXBase):
    """
    A test plan for a specific control, for a specific period.

    Versioned: LLM regeneration or human edit increments version.
    The gap analyzer always compares a workpaper against a specific version.

    Period format: 'Q1 2026', 'Q2 2026' etc. — matches scoping dashboard convention.
    """

    id: UUID = Field(default_factory=uuid4)
    control_id: UUID
    period: str = Field(..., description="e.g. 'Q1 2026', 'Q2 2026'")
    version: int = Field(default=1, ge=1)

    objective: str
    scope: str
    steps: list[TestStep]

    generated_at: datetime
    generated_by: Literal["llm", "human", "hybrid"]
    source_inputs: list[Source]
    llm_model: str | None = None

    # LLM confidence on the generated plan as a whole
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    status: Literal["draft", "in_review", "approved", "superseded"] = "draft"
    approved_by: str | None = None
    approved_at: datetime | None = None


# ─── Workpaper ────────────────────────────────────────────────────────────────


class WorkpaperTab(SOXBase):
    """
    One tab/section of an uploaded workpaper after parsing.

    classified_as is the LLM's guess at the tab's role.
    Common values: 'procedures', 'ipe_ca', 'signoff', 'results',
    'screenshots', 'original_report', 'unknown'.
    """

    name: str
    classified_as: str | None = None
    text_content: str
    tables: list[list[list[str]]] = Field(default_factory=list)
    has_images: bool = False
    image_paths: list[str] = Field(default_factory=list)
    notes: str | None = None


class WorkpaperContent(SOXBase):
    """Parsed content of an uploaded workpaper. Pre-analysis."""

    tabs: list[WorkpaperTab]
    total_pages: int | None = None
    total_sheets: int | None = None
    parsed_at: datetime
    parser_warnings: list[str] = Field(default_factory=list)


class Workpaper(SOXBase):
    """
    An uploaded completed workpaper for a control.

    file_hash (sha256) detects re-uploads and avoids redundant parsing.
    parsing_status supports async parsing: upload returns immediately,
    background worker fills content and flips status to 'parsed'.
    """

    id: UUID = Field(default_factory=uuid4)
    control_id: UUID
    period: str
    test_plan_id: UUID | None = None

    original_filename: str
    file_path: str
    file_hash: str
    file_format: WorkpaperFormat
    file_size_bytes: int

    uploaded_by: str           # display name / email (always set)
    uploaded_by_id: UUID | None = None   # User.id if uploaded through the platform
    uploaded_at: datetime

    content: WorkpaperContent | None = None
    parsing_status: Literal["pending", "parsing", "parsed", "failed"] = "pending"
    parsing_error: str | None = None


# ─── Gap report ───────────────────────────────────────────────────────────────


class Gap(SOXBase):
    """
    A single finding from the gap analyzer.

    evidence_citation is mandatory in spirit: the LLM prompt must require
    the analyzer to cite WHERE it looked (tab + section). Without citations
    a finding is unactionable. detected_by distinguishes rule-based findings
    (always trustworthy) from LLM findings (confidence-weighted).
    """

    category: GapCategory
    severity: GapSeverity
    title: str = Field(..., max_length=200)
    description: str
    test_step_number: int | None = None
    attribute_key: str | None = None
    evidence_citation: str | None = Field(
        default=None,
        description="Tab + section/cell/line where the analyzer searched",
    )
    recommendation: str | None = None
    detected_by: Literal["deterministic_check", "llm_analysis"]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    suppressed: bool = False
    suppression_reason: str | None = None
    suppressed_by: str | None = None


class GapReport(SOXBase):
    """
    Output of analyzing one workpaper against one test plan.

    Designed to be idempotent: same inputs → same report (temperature=0,
    prompt_hash pinned). prompt_hash lets us flag reports generated under
    an older prompt version when we tune the gap analyzer.
    """

    id: UUID = Field(default_factory=uuid4)
    workpaper_id: UUID
    test_plan_id: UUID
    control_id: UUID
    period: str

    gaps: list[Gap] = Field(default_factory=list)

    total_gaps: int = 0
    critical_count: int = 0
    material_count: int = 0
    minor_count: int = 0

    overall_status: Literal["ready_for_review", "gaps_found", "blocking_issues", "error"]
    summary: str

    analyzed_at: datetime
    analyzer_version: str
    llm_model: str | None = None
    prompt_hash: str | None = None
    deterministic_check_results: dict[str, bool] = Field(default_factory=dict)

    reviewed_by: str | None = None           # display name
    reviewed_by_id: UUID | None = None       # User.id (REVIEWER or ADMIN role)
    reviewed_at: datetime | None = None
    reviewer_notes: str | None = None
