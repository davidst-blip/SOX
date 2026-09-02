"""
Knowledge base summarizer — uses Haiku to summarize what was tested in a historical workpaper.

Output is stored as a KnowledgeEntry and used as prior-year context for test plan generation.
"""

from anthropic import Anthropic

_HAIKU_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = """You are a SOX audit expert analyzing a historical workpaper for Perion Network Ltd.

Summarize what was actually TESTED in this workpaper in structured JSON.
Return ONLY valid JSON — no markdown fences, no explanation.

Target JSON schema:
{
  "what_was_tested": "2-3 sentence description of the control procedure that was tested",
  "evidence_reviewed": ["list of evidence items examined (e.g. 'NetSuite AP aging report', 'invoice samples')"],
  "sample_approach": "how samples were selected and how many (e.g. '25 invoices selected judgmentally over Q1 2025')",
  "key_attributes_tested": ["list of control attributes verified (e.g. 'authorization', 'completeness', 'cutoff')"],
  "exceptions_noted": ["list of exceptions or gaps found, empty list if none"],
  "testing_conclusion": "pass | fail | pass_with_exceptions",
  "period": "period this covers if determinable (e.g. 'Q1 2025'), else null"
}
"""


def summarize_workpaper(
    sections: list[dict],
    control_code: str,
    client: Anthropic,
) -> dict:
    """
    Summarize a parsed workpaper's sections into a structured knowledge entry.

    sections: output of file_parser.parse_workpaper()
    control_code: e.g. "EX-1" — gives the LLM context about what control this is for
    """
    # Build a compact text representation (cap at ~6000 chars to stay within Haiku context)
    parts = [f"Control: {control_code}", ""]
    total_chars = 0
    for section in sections:
        header = f"--- {section['name']} ---"
        body = section.get("text", "")[:2000]  # cap per section
        parts.append(header)
        parts.append(body)
        total_chars += len(body)
        if total_chars > 6000:
            parts.append("[... truncated for length ...]")
            break

    workpaper_text = "\n".join(parts)

    response = client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=512,
        temperature=0,
        system=_SYSTEM,
        messages=[{"role": "user", "content": workpaper_text}],
    )

    import json
    block = response.content[0]
    raw_json = (block.text if hasattr(block, "text") else "{}").strip()
    try:
        return json.loads(raw_json)
    except json.JSONDecodeError:
        return {
            "what_was_tested": "Parse error — review manually",
            "evidence_reviewed": [],
            "sample_approach": "",
            "key_attributes_tested": [],
            "exceptions_noted": [],
            "testing_conclusion": "pass",
            "period": None,
        }
