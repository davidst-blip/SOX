#!/usr/bin/env python3
"""
fetch_boi.py — Pull Bank of Israel exchange rates via the SDMX API.

Requires: requests

Usage:
  python fetch_boi.py --period-start 2026-04-01 --period-end 2026-04-30 \
      --output /tmp/_boi_rates.json

  python fetch_boi.py --period-start 2026-04-01 --period-end 2026-04-30 \
      --output /tmp/_boi_rates.json --debug

  python fetch_boi.py --boi-csv /path/to/downloaded.csv \
      --period-start 2026-04-01 --period-end 2026-04-30 \
      --output /tmp/_boi_rates.json
"""

import argparse
import csv
import json
import sys
import io
from datetime import date, timedelta, datetime

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

SDMX_BASE = "https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0"
SERIES = "RER_USD_ILS,RER_EUR_ILS,RER_GBP_ILS"

BOI_SERIES_TO_PAIR = {
    "RER_USD_ILS": ("USD", "ILS"),
    "RER_EUR_ILS": ("EUR", "ILS"),
    "RER_GBP_ILS": ("GBP", "ILS"),
}


def fetch_sdmx(period_start: str, period_end: str, debug: bool) -> list[dict]:
    url = f"{SDMX_BASE}/{SERIES}"
    params = {
        "startperiod": period_start,
        "endperiod": period_end,
        "format": "csv",
    }
    headers = {
        "Accept": "text/csv,application/csv,*/*",
        "User-Agent": "Mozilla/5.0 (compatible; SOX-FR8-Skill/1.0)",
    }

    if debug:
        print(f"DEBUG: GET {url}", file=sys.stderr)
        print(f"DEBUG: params={params}", file=sys.stderr)

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        print("ERROR: BOI SDMX endpoint timed out (30s). Check network access.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"ERROR: BOI SDMX returned HTTP {resp.status_code}: {e}", file=sys.stderr)
        print("  If you see 403/Cloudflare, download the CSV manually from boi.org.il", file=sys.stderr)
        print("  and re-run with --boi-csv path/to/file.csv", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"ERROR: Network error fetching BOI: {e}", file=sys.stderr)
        sys.exit(1)

    if debug:
        print(f"DEBUG: response status={resp.status_code}, len={len(resp.text)}", file=sys.stderr)
        print(f"DEBUG: first 500 chars:\n{resp.text[:500]}", file=sys.stderr)

    return parse_sdmx_csv(resp.text, debug)


def parse_sdmx_csv(raw: str, debug: bool) -> list[dict]:
    """Parse SDMX-CSV into a list of {date, base_currency, quote_currency, rate} dicts.

    BOI SDMX-CSV column names vary between deployments. We handle the two
    known variants:
    Variant A: TIME_PERIOD, OBS_VALUE, SERIES_KEY (or similar)
    Variant B: Date, Rate, Series
    We detect by inspecting the header row.
    """
    reader = csv.DictReader(io.StringIO(raw.strip()))
    fieldnames = reader.fieldnames or []

    if debug:
        print(f"DEBUG: CSV columns: {fieldnames}", file=sys.stderr)

    # Normalise column names to lowercase for resilient matching
    col_lower = {f.lower().strip(): f for f in fieldnames}

    date_col = _find_col(col_lower, ["time_period", "date", "period", "time"])
    value_col = _find_col(col_lower, ["obs_value", "value", "rate", "exchangerate"])
    series_col = _find_col(col_lower, ["series_key", "series", "key", "indicator"])

    if not date_col or not value_col:
        print(f"ERROR: Cannot identify date or value columns in BOI CSV.", file=sys.stderr)
        print(f"  Found columns: {fieldnames}", file=sys.stderr)
        print("  Run with --debug to see raw response.", file=sys.stderr)
        sys.exit(1)

    observations = []
    for row in reader:
        raw_date = row.get(date_col, "").strip()
        raw_value = row.get(value_col, "").strip()
        raw_series = row.get(series_col, "").strip() if series_col else ""

        if not raw_date or not raw_value:
            continue

        try:
            obs_date = _parse_date(raw_date)
            obs_value = float(raw_value)
        except ValueError:
            if debug:
                print(f"DEBUG: Skipping unparseable row: {row}", file=sys.stderr)
            continue

        pair = _series_to_pair(raw_series, row)
        if pair is None:
            if debug:
                print(f"DEBUG: Cannot identify currency pair for row: {row}", file=sys.stderr)
            continue

        base, quote = pair
        observations.append({
            "date": obs_date.isoformat(),
            "base_currency": base,
            "quote_currency": quote,
            "rate": obs_value,
        })

    if debug:
        print(f"DEBUG: Parsed {len(observations)} observations", file=sys.stderr)

    return observations


def load_csv_file(csv_path: str, period_start: str, period_end: str, debug: bool) -> list[dict]:
    """Load a manually downloaded BOI CSV."""
    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            raw = f.read()
    except FileNotFoundError:
        print(f"ERROR: CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    observations = parse_sdmx_csv(raw, debug)

    # Filter to requested period
    start = date.fromisoformat(period_start)
    end = date.fromisoformat(period_end)
    filtered = [
        o for o in observations
        if start <= date.fromisoformat(o["date"]) <= end
    ]

    if debug:
        print(f"DEBUG: After period filter ({period_start}–{period_end}): {len(filtered)} observations", file=sys.stderr)

    return filtered


def _find_col(col_lower: dict, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in col_lower:
            return col_lower[c]
    return None


def _parse_date(raw: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {raw!r}")


def _series_to_pair(series_key: str, row: dict) -> tuple[str, str] | None:
    """Map a series key string to (base, quote) currency tuple."""
    for key, pair in BOI_SERIES_TO_PAIR.items():
        if key in series_key.upper():
            return pair

    # Try to infer from other row columns (some exports embed currency in a separate column)
    for col_val in row.values():
        for key, pair in BOI_SERIES_TO_PAIR.items():
            if key in (col_val or "").upper():
                return pair

    return None


def main():
    parser = argparse.ArgumentParser(description="Fetch BOI exchange rates for FR-8")
    parser.add_argument("--period-start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--period-end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--boi-csv", help="Path to manually downloaded BOI CSV (skips API call)")
    parser.add_argument("--debug", action="store_true", help="Print debug info to stderr")
    args = parser.parse_args()

    if args.boi_csv:
        observations = load_csv_file(args.boi_csv, args.period_start, args.period_end, args.debug)
        source_mode = f"csv:{args.boi_csv}"
    else:
        observations = fetch_sdmx(args.period_start, args.period_end, args.debug)
        source_mode = "live_api"

    if not observations:
        print("ERROR: Parsed 0 observations from BOI data. Check --debug output.", file=sys.stderr)
        sys.exit(1)

    output = {
        "source_mode": source_mode,
        "period_start": args.period_start,
        "period_end": args.period_end,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "observation_count": len(observations),
        "observations": observations,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"OK: {len(observations)} BOI observations written to {args.output}")


if __name__ == "__main__":
    main()
