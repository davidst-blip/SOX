#!/usr/bin/env python3
"""
format_ns_output.py — Write the raw NetSuite MCP SuiteQL result to the
standardised _ns_rates.json that run_fr8.py expects.

Claude Code calls this after running ns_runCustomSuiteQL. The MCP result
is piped in as stdin (JSON string) or passed as --input.

Usage (Claude Code writes MCP output to stdin):
  echo '<mcp_json_string>' | python3 format_ns_output.py --output /tmp/_ns_rates.json

Usage (from a file):
  python3 format_ns_output.py --input /tmp/raw_mcp.json --output /tmp/_ns_rates.json

The script:
  1. Accepts the raw MCP response: {data: [...], totalResults: N, ...}
  2. Validates required fields are present
  3. Filters to fxsourcemethod = 'MANUAL' (primary accounting book only)
  4. Writes {data: [...], source: "netsuite_suiteql", ...} to --output
"""

import argparse
import json
import sys
from datetime import datetime

REQUIRED_CURRENCIES = {"ILS", "USD", "EUR", "GBP"}


def validate_and_filter(records: list[dict]) -> tuple[list[dict], list[str]]:
    warnings = []
    filtered = []

    for rec in records:
        base = (rec.get("basecurrency") or "").upper().strip()
        quote = (rec.get("transactioncurrency") or "").upper().strip()

        # Skip self-pairs (USD/USD = 1 etc.)
        if base == quote:
            continue

        # Skip pairs outside the 4 currencies FR-8 cares about
        if base not in REQUIRED_CURRENCIES or quote not in REQUIRED_CURRENCIES:
            continue

        # Keep only primary accounting book rates
        method = (rec.get("fxsourcemethod") or "").upper().strip()
        if method != "MANUAL":
            continue

        filtered.append(rec)

    if not filtered:
        warnings.append("WARNING: Zero records after filtering. Check fxsourcemethod values in raw data.")

    # Check for any unexpected duplicates after filtering
    seen = set()
    for rec in filtered:
        key = (rec.get("effectivedate"), rec.get("basecurrency"), rec.get("transactioncurrency"))
        if key in seen:
            warnings.append(f"WARNING: Duplicate after MANUAL filter: {key}. Using last occurrence.")
        seen.add(key)

    return filtered, warnings


def main():
    parser = argparse.ArgumentParser(description="Format NS MCP output for FR-8")
    parser.add_argument("--input", help="Path to raw MCP JSON file (default: stdin)")
    parser.add_argument("--output", required=True, help="Output path for _ns_rates.json")
    parser.add_argument("--period-start", help="YYYY-MM-DD (optional, for metadata)")
    parser.add_argument("--period-end",   help="YYYY-MM-DD (optional, for metadata)")
    args = parser.parse_args()

    if args.input:
        with open(args.input) as f:
            raw = json.load(f)
    else:
        raw = json.load(sys.stdin)

    # Handle both raw list and wrapped {data: [...]} response
    records = raw if isinstance(raw, list) else raw.get("data", raw.get("records", []))
    total_raw = len(records)

    filtered, warnings = validate_and_filter(records)

    for w in warnings:
        print(w, file=sys.stderr)

    output = {
        "source": "netsuite_suiteql",
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "period_start": args.period_start or "",
        "period_end": args.period_end or "",
        "raw_record_count": total_raw,
        "filtered_record_count": len(filtered),
        "filter_applied": "fxsourcemethod = MANUAL",
        "data": filtered,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"OK: {len(filtered)} NS rate records (filtered from {total_raw} raw) → {args.output}")
    if warnings:
        sys.exit(1)


if __name__ == "__main__":
    main()
