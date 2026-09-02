"""
LLM normalizer — takes a raw RCM row dict and returns a structured Control.

Model: claude-haiku-4-5 (cheap, fast; Sonnet reserved for generation/analysis).
Caching: same raw_row hash → skip LLM call, return cached result.
"""

import hashlib
import json
from datetime import datetime, timezone

from anthropic import Anthropic

from engine.schemas import (
    Assertion,
    COSOComponent,
    Control,
    ControlAttribute,
    ControlNature,
    ControlType,
    Frequency,
    PerionEntity,
    RiskLevel,
    Source,
)

PARSER_VERSION = "0.2.0"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"

# ─── Prompt ───────────────────────────────────────────────────────────────────

_SYSTEM = """You are a SOX compliance expert normalizing raw RCM (Risk and Control Matrix) rows
from Perion Network Ltd. into structured JSON.

Your job: map messy Excel column data to the target schema fields below.
Return ONLY valid JSON — no markdown fences, no explanation.

Target JSON schema:
{
  "code": "string (e.g. EX-1, FR-6, HS-FR-6) — infer from Ref/No/Control ID columns",
  "name": "short control name (≤120 chars)",
  "description": "full control description — use the longest relevant text field",
  "entity": "one of: Perion Network Ltd. | Undertone USA | Hivestack Canada | Vidazoo | CodeFuel | Other",
  "control_type": "preventive | detective",
  "nature": "manual | automated | it_dependent_manual",
  "frequency": "continuous | daily | weekly | monthly | quarterly | semi_annual | annual | event_driven | unknown",
  "risk_level": "key | non_key | unknown",
  "process": "process name or null",
  "sub_process": "sub-process or null",
  "coso_component": "Control Environment | Risk Assessment | Control Activities | Information & Communication | Monitoring | null",
  "assertions": ["E/O","C","A","V","P/D","CU"] subset — parse from assertion column; empty list if none,
  "owner": "name/email string or null",
  "reviewer": "name/email string or null",
  "attributes": [
    {"key": "...", "value": "...", "source": "rcm", "confidence": 1.0, "notes": null}
  ],
  "needs_human_review": true/false,
  "review_reasons": ["reason 1", "reason 2"]
}

Rules:
- Set needs_human_review=true if: frequency=unknown, risk_level=unknown, code cannot be determined,
  description is very short (<30 chars), or any critical field is ambiguous.
- Add review_reasons listing what triggered the flag.
- For attributes: capture any useful data not in the core fields (e.g. population_source, system, threshold).
- Confidence 1.0 = directly from RCM text; 0.7-0.9 = inferred; <0.7 = guess → flag.
"""


def _row_hash(raw_row: dict[str, str]) -> str:
    """Deterministic hash of the raw row for cache keying."""
    canonical = json.dumps(raw_row, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def normalize_row(
    raw_row: dict[str, str],
    client: Anthropic,
    _cache: dict[str, Control] | None = None,
) -> Control:
    """
    Normalize one raw RCM row into a Control schema object.

    _cache is an optional in-process dict for deduplication within a batch.
    For cross-request caching, use DB lookup by file_hash before calling this.
    """
    cache_key = _row_hash(raw_row)
    if _cache is not None and cache_key in _cache:
        return _cache[cache_key]

    user_msg = f"Raw RCM row:\n{json.dumps(raw_row, ensure_ascii=False, indent=2)}"

    response = client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=1024,
        temperature=0,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )

    block = response.content[0]
    raw_json = (block.text if hasattr(block, "text") else "{}").strip()
    data = json.loads(raw_json)

    # Build attributes list
    attrs = [
        ControlAttribute(
            key=a["key"],
            value=a["value"],
            source=Source.RCM,
            confidence=a.get("confidence", 1.0),
            notes=a.get("notes"),
        )
        for a in data.get("attributes", [])
    ]

    # Parse assertions safely
    valid_assertion_values = {a.value for a in Assertion}
    assertions = [a for a in data.get("assertions", []) if a in valid_assertion_values]

    control = Control(
        code=data.get("code") or f"UNKNOWN-{cache_key}",
        name=data.get("name", "")[:120],
        description=data.get("description", ""),
        entity=_coerce_enum(data.get("entity", "Other"), PerionEntity, PerionEntity.OTHER),
        control_type=_coerce_enum(data.get("control_type", "preventive"), ControlType, ControlType.PREVENTIVE),
        nature=_coerce_enum(data.get("nature", "manual"), ControlNature, ControlNature.MANUAL),
        frequency=_coerce_enum(data.get("frequency", "unknown"), Frequency, Frequency.UNKNOWN),
        risk_level=_coerce_enum(data.get("risk_level", "unknown"), RiskLevel, RiskLevel.UNKNOWN),
        process=data.get("process"),
        sub_process=data.get("sub_process"),
        coso_component=_coerce_enum(data.get("coso_component"), COSOComponent, None),
        assertions=assertions,
        owner=data.get("owner"),
        reviewer=data.get("reviewer"),
        attributes=attrs,
        raw_rcm_row=raw_row,
        parsed_at=datetime.now(timezone.utc),
        parser_version=PARSER_VERSION,
        needs_human_review=data.get("needs_human_review", False),
        review_reasons=data.get("review_reasons", []),
    )

    if _cache is not None:
        _cache[cache_key] = control

    return control


def _coerce_enum(value, enum_cls, default):
    """Return enum member matching value string, or default."""
    if value is None:
        return default
    try:
        return enum_cls(value)
    except ValueError:
        return default
