"""
Round-trip tests for all core Pydantic schemas.

These tests prove the schemas can be instantiated, serialized to JSON,
and deserialized back without data loss. They catch schema drift early —
if any model breaks here, every downstream layer is affected.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from engine.schemas import (
    Assertion,
    COSOComponent,
    Control,
    ControlAttribute,
    ControlNature,
    ControlType,
    Frequency,
    Gap,
    GapCategory,
    GapReport,
    GapSeverity,
    PerionEntity,
    RiskLevel,
    Source,
    TestPlan,
    TestStep,
    Workpaper,
    WorkpaperContent,
    WorkpaperFormat,
    WorkpaperTab,
)

NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


def make_control_attribute() -> ControlAttribute:
    return ControlAttribute(
        key="population_source",
        value="customsearch21965",
        source=Source.RCM,
        confidence=None,
    )


def make_control() -> Control:
    return Control(
        code="EX-1",
        name="Vendor Master Changes — SoD",
        description=(
            "Changes to vendor master file require approval from someone "
            "other than the requestor (segregation of duties)."
        ),
        entity=PerionEntity.PERION,
        control_type=ControlType.PREVENTIVE,
        nature=ControlNature.MANUAL,
        frequency=Frequency.EVENT_DRIVEN,
        risk_level=RiskLevel.KEY,
        owner="AP Manager",
        reviewer="VP Finance",
        process="Procure-to-Pay",
        coso_component=COSOComponent.CONTROL_ACTIVITIES,
        assertions=[Assertion.EXISTENCE_OCCURRENCE, Assertion.COMPLETENESS],
        attributes=[make_control_attribute()],
        raw_rcm_row={"A": "EX-1", "B": "Vendor Master Changes", "C": "Key"},
        parsed_at=NOW,
        parser_version="0.1.0",
    )


def make_test_step() -> TestStep:
    return TestStep(
        number=1,
        procedure="Obtain the vendor change log from customsearch21965 for the period.",
        expected_evidence="Excel export of all vendor master changes with requester and approver columns.",
        attributes_tested=["population_source"],
        sample_size_guidance="Haphazard 25 from population",
        estimated_minutes=30,
    )


def make_test_plan(control_id) -> TestPlan:
    return TestPlan(
        control_id=control_id,
        period="Q1 2026",
        version=1,
        objective="Confirm vendor master changes cannot be self-approved.",
        scope="All vendor master changes in Q1 2026 for Perion Network Ltd.",
        steps=[make_test_step()],
        generated_at=NOW,
        generated_by="llm",
        source_inputs=[Source.RCM],
        llm_model="claude-opus-4-7",
        confidence=0.92,
        status="draft",
    )


def make_workpaper(control_id, plan_id) -> Workpaper:
    return Workpaper(
        control_id=control_id,
        period="Q1 2026",
        test_plan_id=plan_id,
        original_filename="EX-1_Q1_2026_Perion.xlsx",
        file_path="uploads/EX-1_Q1_2026_Perion.xlsx",
        file_hash="abc123def456",
        file_format=WorkpaperFormat.XLSX,
        file_size_bytes=204800,
        uploaded_by="davidst@perion.com",
        uploaded_at=NOW,
    )


def make_gap() -> Gap:
    return Gap(
        category=GapCategory.MISSING_IPE_CA,
        severity=GapSeverity.MATERIAL,
        title="IPE C&A tab not populated",
        description="The workpaper has an IPE C&A tab but all cells are empty.",
        test_step_number=2,
        evidence_citation="Tab 'IPE C&A', cells B2:F50 — all blank",
        recommendation="Complete the IPE C&A tab by documenting completeness and accuracy of the population source.",
        detected_by="deterministic_check",
    )


def make_gap_report(workpaper_id, plan_id, control_id) -> GapReport:
    g = make_gap()
    return GapReport(
        workpaper_id=workpaper_id,
        test_plan_id=plan_id,
        control_id=control_id,
        period="Q1 2026",
        gaps=[g],
        total_gaps=1,
        material_count=1,
        overall_status="gaps_found",
        summary="One material gap found: IPE C&A tab is blank. All other procedures documented.",
        analyzed_at=NOW,
        analyzer_version="0.1.0",
        llm_model="claude-opus-4-7",
        prompt_hash="sha256:deadbeef",
        deterministic_check_results={"signoff_present": True, "ipe_ca_populated": False},
    )


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestControlAttribute:
    def test_round_trip(self):
        obj = make_control_attribute()
        assert ControlAttribute.model_validate(obj.model_dump()) == obj

    def test_rejects_unknown_fields(self):
        with pytest.raises(Exception):
            ControlAttribute(key="k", value="v", source=Source.RCM, surprise="boom")

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            ControlAttribute(key="k", value="v", source=Source.RCM, confidence=1.5)


class TestControl:
    def test_round_trip(self):
        obj = make_control()
        assert Control.model_validate(obj.model_dump()) == obj

    def test_json_round_trip(self):
        obj = make_control()
        restored = Control.model_validate_json(obj.model_dump_json())
        assert restored.code == obj.code
        assert restored.entity == obj.entity
        assert restored.assertions == obj.assertions

    def test_unknown_entity_rejected(self):
        data = make_control().model_dump()
        data["entity"] = "Acme Corp"
        with pytest.raises(Exception):
            Control.model_validate(data)


class TestTestPlan:
    def test_round_trip(self):
        control = make_control()
        obj = make_test_plan(control.id)
        assert TestPlan.model_validate(obj.model_dump()) == obj

    def test_period_format_preserved(self):
        control = make_control()
        obj = make_test_plan(control.id)
        assert obj.period == "Q1 2026"


class TestWorkpaper:
    def test_round_trip(self):
        control = make_control()
        plan = make_test_plan(control.id)
        obj = make_workpaper(control.id, plan.id)
        assert Workpaper.model_validate(obj.model_dump()) == obj

    def test_default_status_is_pending(self):
        control = make_control()
        plan = make_test_plan(control.id)
        obj = make_workpaper(control.id, plan.id)
        assert obj.parsing_status == "pending"


class TestGapReport:
    def test_round_trip(self):
        control = make_control()
        plan = make_test_plan(control.id)
        wp = make_workpaper(control.id, plan.id)
        obj = make_gap_report(wp.id, plan.id, control.id)
        assert GapReport.model_validate(obj.model_dump()) == obj

    def test_gap_detected_by_preserved(self):
        control = make_control()
        plan = make_test_plan(control.id)
        wp = make_workpaper(control.id, plan.id)
        obj = make_gap_report(wp.id, plan.id, control.id)
        assert obj.gaps[0].detected_by == "deterministic_check"
