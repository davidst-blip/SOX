#!/usr/bin/env python3
"""
run_fr8.py — Generate the FR-8 Exchange Rate Reconciliation working paper.

Reads:
  _ns_rates.json   — NetSuite exchange rates (written by Claude Code NS MCP call)
  _boi_rates.json  — BOI exchange rates (written by fetch_boi.py)

Writes:
  Daily_Exchange_Rates_-_MM_YYYY.xlsx

Usage:
  python run_fr8.py \\
      --ns-json /tmp/_ns_rates.json \\
      --boi-json /tmp/_boi_rates.json \\
      --period-start 2026-04-01 \\
      --period-end 2026-04-30 \\
      --output /tmp/Daily_Exchange_Rates_-_04_2026.xlsx \\
      --script-version v1.0.0

  python run_fr8.py --demo --period-start 2025-12-01 --period-end 2025-12-31 \\
      --output /tmp/Daily_Exchange_Rates_-_12_2025.xlsx
"""

import argparse
import json
import math
import os
import sys
from datetime import date, timedelta, datetime
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import (
        Alignment, Border, Font, PatternFill, Side
    )
    from openpyxl.utils import get_column_letter
except ImportError:
    print("ERROR: 'openpyxl' not installed. Run: pip install openpyxl", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DIRECT_PAIRS = [
    ("ILS", "USD"),
    ("ILS", "EUR"),
    ("ILS", "GBP"),
]

CROSS_PAIRS = [
    ("USD", "ILS"),
    ("EUR", "ILS"),
    ("USD", "EUR"),
    ("EUR", "USD"),
    ("USD", "GBP"),
    ("GBP", "USD"),
]

ALL_PAIRS = DIRECT_PAIRS + CROSS_PAIRS

TOLERANCE_DIRECT = 0.0001
TOLERANCE_CROSS = 0.000001

RESULT_OK = "OK"
RESULT_EXCEPTION = "EXCEPTION"
RESULT_BOI_NO_PUBLISH = "BOI_NO_PUBLISH"
RESULT_NS_MISSING = "NS_MISSING"
RESULT_NS_CARRY_FWD = "NS_CARRY_FWD"

# ---------------------------------------------------------------------------
# Colour palette (hex without #)
# ---------------------------------------------------------------------------
C_HEADER_DARK = "1F3864"   # dark navy
C_HEADER_MID  = "2E75B6"   # mid blue
C_HEADER_LIGHT = "BDD7EE"  # light blue
C_OK           = "E2EFDA"  # light green
C_EXCEPTION    = "FFCCCC"  # light red
C_HOLIDAY      = "F2F2F2"  # light grey
C_CARRY        = "FFF2CC"  # light yellow
C_MISSING      = "FFE0B2"  # light orange
C_WHITE        = "FFFFFF"
C_DASH_BG      = "D6E4F0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _load_holidays(json_path: Path | None) -> set[date]:
    if json_path is None:
        json_path = Path(__file__).parent.parent / "references" / "israeli-holidays.json"
    if not json_path.exists():
        return set()
    with open(json_path) as f:
        data = json.load(f)
    holidays = set()
    for year_dates in data.get("non_publish_dates", {}).values():
        for entry in year_dates:
            try:
                holidays.add(date.fromisoformat(entry["date"]))
            except (KeyError, ValueError):
                pass
    return holidays


def _is_boi_publish_day(d: date, holidays: set[date]) -> bool:
    if d.weekday() == 5:   # Saturday
        return False
    if d in holidays:
        return False
    return True


def _derive_cross_rate(base: str, quote: str, boi_direct: dict) -> float | None:
    """
    boi_direct: {('USD','ILS'): float, ('EUR','ILS'): float, ('GBP','ILS'): float}
    Cross-rate derivation from ILS-base rates.
    """
    # USD/ILS → 1 / (ILS/USD)
    # EUR/ILS → 1 / (ILS/EUR)
    # USD/EUR → (ILS/EUR) / (ILS/USD)
    # EUR/USD → (ILS/USD) / (ILS/EUR)
    # USD/GBP → (ILS/GBP) / (ILS/USD)
    # GBP/USD → (ILS/USD) / (ILS/GBP)

    ils_usd = boi_direct.get(("ILS", "USD"))
    ils_eur = boi_direct.get(("ILS", "EUR"))
    ils_gbp = boi_direct.get(("ILS", "GBP"))

    try:
        if base == "USD" and quote == "ILS":
            return 1.0 / ils_usd if ils_usd else None
        if base == "EUR" and quote == "ILS":
            return 1.0 / ils_eur if ils_eur else None
        if base == "USD" and quote == "EUR":
            return ils_eur / ils_usd if ils_eur and ils_usd else None
        if base == "EUR" and quote == "USD":
            return ils_usd / ils_eur if ils_usd and ils_eur else None
        if base == "USD" and quote == "GBP":
            return ils_gbp / ils_usd if ils_gbp and ils_usd else None
        if base == "GBP" and quote == "USD":
            return ils_usd / ils_gbp if ils_usd and ils_gbp else None
    except ZeroDivisionError:
        return None
    return None


def _tolerance(base: str, quote: str) -> float:
    if (base, quote) in DIRECT_PAIRS:
        return TOLERANCE_DIRECT
    return TOLERANCE_CROSS


def _result_fill(result_code: str) -> PatternFill | None:
    mapping = {
        RESULT_OK: C_OK,
        RESULT_EXCEPTION: C_EXCEPTION,
        RESULT_BOI_NO_PUBLISH: C_HOLIDAY,
        RESULT_NS_MISSING: C_MISSING,
        RESULT_NS_CARRY_FWD: C_CARRY,
    }
    colour = mapping.get(result_code)
    return PatternFill("solid", fgColor=colour) if colour else None


def _thin_border():
    thin = Side(style="thin")
    return Border(left=thin, right=thin, top=thin, bottom=thin)


def _hfill(colour: str) -> PatternFill:
    return PatternFill("solid", fgColor=colour)


def _header_font(bold: bool = True, white: bool = True, size: int = 10) -> Font:
    return Font(bold=bold, color=C_WHITE if white else "000000", size=size)


def _auto_width(ws, min_width: int = 10, max_width: int = 40):
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        best = min_width
        for cell in col:
            if cell.value:
                best = max(best, min(len(str(cell.value)) + 2, max_width))
        ws.column_dimensions[col_letter].width = best


# ---------------------------------------------------------------------------
# Demo data generator (for testing without live sources)
# ---------------------------------------------------------------------------

def _generate_demo_ns(period_start: date, period_end: date, holidays: set[date]) -> dict:
    """Return {(d, base, quote): rate} mirroring realistic values for all 9 pairs."""
    ils_usd_base = 3.65
    ils_eur_base = 3.90
    ils_gbp_base = 4.62
    ns_data = {}
    for d in _date_range(period_start, period_end):
        if not _is_boi_publish_day(d, holidays):
            continue
        off = (d - period_start).days
        ils_usd = round(ils_usd_base + off * 0.001, 6)
        ils_eur = round(ils_eur_base + off * 0.001, 6)
        ils_gbp = round(ils_gbp_base + off * 0.001, 6)
        ns_data[(d, "ILS", "USD")] = ils_usd
        ns_data[(d, "ILS", "EUR")] = ils_eur
        ns_data[(d, "ILS", "GBP")] = ils_gbp
        ns_data[(d, "USD", "ILS")] = round(1.0 / ils_usd, 6)
        ns_data[(d, "EUR", "ILS")] = round(1.0 / ils_eur, 6)
        ns_data[(d, "USD", "EUR")] = round(ils_eur / ils_usd, 6)
        ns_data[(d, "EUR", "USD")] = round(ils_usd / ils_eur, 6)
        ns_data[(d, "USD", "GBP")] = round(ils_gbp / ils_usd, 6)
        ns_data[(d, "GBP", "USD")] = round(ils_usd / ils_gbp, 6)
    return ns_data


def _generate_demo_boi(period_start: date, period_end: date, holidays: set[date]) -> dict:
    """Return {(d, 'ILS', quote): rate} for the 3 direct BOI-published pairs only."""
    ils_usd_base = 3.65
    ils_eur_base = 3.90
    ils_gbp_base = 4.62
    boi_data = {}
    for d in _date_range(period_start, period_end):
        if not _is_boi_publish_day(d, holidays):
            continue
        off = (d - period_start).days
        boi_data[(d, "ILS", "USD")] = round(ils_usd_base + off * 0.001, 6)
        boi_data[(d, "ILS", "EUR")] = round(ils_eur_base + off * 0.001, 6)
        boi_data[(d, "ILS", "GBP")] = round(ils_gbp_base + off * 0.001, 6)
    return boi_data


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def load_ns_json(path: str) -> dict:
    """Load NS JSON into {(date, base, quote): rate}."""
    with open(path) as f:
        data = json.load(f)

    result = {}
    records = data if isinstance(data, list) else data.get("records", data.get("data", []))
    for rec in records:
        try:
            d = date.fromisoformat(rec.get("effectivedate") or rec.get("date") or "")
            base = (rec.get("basecurrency") or rec.get("base_currency") or "").upper()
            quote = (rec.get("transactioncurrency") or rec.get("quote_currency") or rec.get("transaction_currency") or "").upper()
            rate = float(rec.get("exchangerate") or rec.get("rate") or 0)
            if d and base and quote and rate:
                result[(d, base, quote)] = rate
        except (ValueError, TypeError):
            continue
    return result


def load_boi_json(path: str) -> dict:
    """Load BOI JSON (from fetch_boi.py) into {(date, base, quote): rate}."""
    with open(path) as f:
        data = json.load(f)

    observations = data.get("observations", [])
    result = {}
    for obs in observations:
        try:
            d = date.fromisoformat(obs["date"])
            base = obs["base_currency"].upper()
            quote = obs["quote_currency"].upper()
            rate = float(obs["rate"])
            result[(d, base, quote)] = rate
        except (KeyError, ValueError):
            continue
    return result


def _get_boi_source_mode(boi_json_path: str) -> str:
    try:
        with open(boi_json_path) as f:
            data = json.load(f)
        return data.get("source_mode", "unknown")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Reconciliation engine
# ---------------------------------------------------------------------------

def reconcile(
    ns_data: dict,
    boi_direct: dict,
    period_start: date,
    period_end: date,
    holidays: set[date],
) -> list[dict]:
    """
    Returns list of row dicts with keys:
      date, base, quote, ns_rate, boi_rate, gap, tolerance, result
    """
    rows = []
    prev_ns_rates: dict[tuple, float] = {}

    for d in _date_range(period_start, period_end):
        is_publish = _is_boi_publish_day(d, holidays)

        for (base, quote) in ALL_PAIRS:
            boi_rate = None
            if (base, quote) in DIRECT_PAIRS:
                boi_rate = boi_direct.get((d, base, quote))
            else:
                # Build boi_direct_for_day from direct observations
                direct_on_day = {
                    k[1:]: v
                    for k, v in boi_direct.items()
                    if k[0] == d and k[1:] in DIRECT_PAIRS
                }
                boi_rate = _derive_cross_rate(base, quote, direct_on_day)

            ns_rate = ns_data.get((d, base, quote))

            if not is_publish:
                result = RESULT_BOI_NO_PUBLISH
                gap = None
            elif ns_rate is None:
                result = RESULT_NS_MISSING
                gap = None
            elif boi_rate is None:
                result = RESULT_NS_MISSING
                gap = None
            else:
                gap = abs(ns_rate - boi_rate)
                tol = _tolerance(base, quote)
                if gap > tol:
                    result = RESULT_EXCEPTION
                else:
                    # Check for carry-forward
                    prev = prev_ns_rates.get((base, quote))
                    if prev is not None and math.isclose(ns_rate, prev, rel_tol=1e-9):
                        result = RESULT_NS_CARRY_FWD
                    else:
                        result = RESULT_OK

            if ns_rate is not None:
                prev_ns_rates[(base, quote)] = ns_rate

            rows.append({
                "date": d,
                "base": base,
                "quote": quote,
                "ns_rate": ns_rate,
                "boi_rate": boi_rate,
                "gap": gap,
                "tolerance": _tolerance(base, quote) if is_publish else None,
                "result": result,
            })

    return rows


# ---------------------------------------------------------------------------
# Workbook builder
# ---------------------------------------------------------------------------

def build_workbook(
    rows: list[dict],
    period_start: date,
    period_end: date,
    ns_json_path: str | None,
    boi_json_path: str | None,
    script_version: str,
    demo_mode: bool,
) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # remove default sheet

    _build_dashboard(wb, rows, period_start, period_end, script_version, demo_mode, boi_json_path)
    _build_reconciliation(wb, rows, period_start, period_end)
    _build_ns_tab(wb, rows)
    _build_boi_tab(wb, rows)
    _build_exceptions_tab(wb, rows)
    _build_ipe_evidence(wb, rows, period_start, period_end, ns_json_path, boi_json_path, script_version, demo_mode)

    return wb


def _build_dashboard(wb, rows, period_start, period_end, script_version, demo_mode, boi_json_path):
    ws = wb.create_sheet("Dashboard")
    ws.sheet_view.showGridLines = False

    total = sum(1 for r in rows if r["result"] in (RESULT_OK, RESULT_EXCEPTION, RESULT_NS_CARRY_FWD, RESULT_NS_MISSING))
    ok = sum(1 for r in rows if r["result"] == RESULT_OK)
    exceptions = sum(1 for r in rows if r["result"] == RESULT_EXCEPTION)
    carry = sum(1 for r in rows if r["result"] == RESULT_NS_CARRY_FWD)
    missing = sum(1 for r in rows if r["result"] == RESULT_NS_MISSING)
    no_pub = sum(1 for r in rows if r["result"] == RESULT_BOI_NO_PUBLISH)

    pub_days_set = {r["date"] for r in rows if r["result"] != RESULT_BOI_NO_PUBLISH}
    cal_days = (period_end - period_start).days + 1

    source_mode = "DEMO" if demo_mode else (_get_boi_source_mode(boi_json_path) if boi_json_path else "unknown")
    period_str = f"{period_start.strftime('%B %Y')}"
    run_ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Title
    ws.merge_cells("B2:H2")
    title = ws["B2"]
    title.value = f"FR-8 Exchange Rate Reconciliation — {period_str}"
    title.font = Font(bold=True, size=14, color=C_WHITE)
    title.fill = _hfill(C_HEADER_DARK)
    title.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 30

    ws.merge_cells("B3:H3")
    sub = ws["B3"]
    sub.value = "Perion Network Ltd. | SOX Control FR-8 | Detective / Manual / Monthly / Key"
    sub.font = Font(size=10, color="444444")
    sub.alignment = Alignment(horizontal="center")

    # Summary metrics
    _dash_row(ws, 5, "Period", period_str)
    _dash_row(ws, 6, "Calendar days", cal_days)
    _dash_row(ws, 7, "BOI publishing days", len(pub_days_set))
    _dash_row(ws, 8, "Total comparisons performed", total)
    _dash_row(ws, 9, "OK", ok, C_OK)
    _dash_row(ws, 10, "Carry-forward flagged (review)", carry, C_CARRY)
    _dash_row(ws, 11, "Completeness gaps (NS missing)", missing, C_MISSING if missing else C_OK)
    _dash_row(ws, 12, "EXCEPTIONS (> tolerance)", exceptions, C_EXCEPTION if exceptions else C_OK)
    _dash_row(ws, 13, "Non-publish days (holidays/Sat)", no_pub)
    _dash_row(ws, 14, "Data source (BOI)", source_mode)
    _dash_row(ws, 15, "Script version", script_version)
    _dash_row(ws, 16, "Run timestamp", run_ts)

    # Overall result
    ws.merge_cells("B18:C18")
    ws["B18"].value = "Overall Result"
    ws["B18"].font = Font(bold=True, size=11)

    ws.merge_cells("D18:H18")
    if exceptions > 0 or missing > 0:
        result_text = "REVIEW REQUIRED — see Exceptions tab"
        result_fill = _hfill(C_EXCEPTION)
    else:
        result_text = "PASS — No exceptions identified"
        result_fill = _hfill(C_OK)
    ws["D18"].value = result_text
    ws["D18"].font = Font(bold=True, size=11)
    ws["D18"].fill = result_fill
    ws["D18"].alignment = Alignment(horizontal="center", vertical="center")

    # Sign-off block
    _section_header(ws, 20, "Sign-Off")
    _signoff_row(ws, 21, "Prepared by (Bookkeeper)", "Kati", "", "")
    _signoff_row(ws, 22, "Reviewed by (Bookkeeping Manager)", "", "", "")
    _signoff_row(ws, 23, "Review date", "", "", "")

    # Reviewer notes
    _section_header(ws, 25, "Reviewer Notes (document what was reviewed)")
    ws.merge_cells("B26:H29")
    notes_cell = ws["B26"]
    notes_cell.value = ""
    notes_cell.alignment = Alignment(wrap_text=True, vertical="top")
    notes_cell.border = _thin_border()

    ws.row_dimensions[26].height = 60

    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 35
    ws.column_dimensions["C"].width = 20
    ws.column_dimensions["D"].width = 20
    ws.column_dimensions["E"].width = 15
    ws.column_dimensions["F"].width = 15
    ws.column_dimensions["G"].width = 15
    ws.column_dimensions["H"].width = 15


def _dash_row(ws, row, label, value, fill_colour=None):
    lbl = ws.cell(row=row, column=2, value=label)
    lbl.font = Font(bold=True, size=10)
    lbl.border = _thin_border()

    val = ws.cell(row=row, column=3, value=value)
    val.font = Font(size=10)
    val.border = _thin_border()
    if fill_colour:
        val.fill = _hfill(fill_colour)
    ws.row_dimensions[row].height = 16


def _section_header(ws, row, text):
    ws.merge_cells(f"B{row}:H{row}")
    cell = ws[f"B{row}"]
    cell.value = text
    cell.font = Font(bold=True, size=10, color=C_WHITE)
    cell.fill = _hfill(C_HEADER_MID)
    cell.alignment = Alignment(horizontal="left")
    ws.row_dimensions[row].height = 18


def _signoff_row(ws, row, label, default_name, default_date, default_sig):
    ws.cell(row=row, column=2, value=label).font = Font(bold=True, size=10)
    ws.cell(row=row, column=3, value=default_name).border = _thin_border()
    ws.cell(row=row, column=4, value="Date:").font = Font(size=10)
    ws.cell(row=row, column=5, value=default_date).border = _thin_border()


def _build_reconciliation(wb, rows, period_start, period_end):
    ws = wb.create_sheet("Reconciliation")

    headers = ["Date", "Day", "Base", "Quote", "Pair", "NS Rate", "BOI Rate", "Gap", "Tolerance", "Result"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = _header_font()
        cell.fill = _hfill(C_HEADER_DARK)
        cell.alignment = Alignment(horizontal="center")
        cell.border = _thin_border()

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"

    for r_idx, row in enumerate(rows, 2):
        d = row["date"]
        pair_str = f"{row['base']}/{row['quote']}"
        fill = _result_fill(row["result"])

        values = [
            d,
            d.strftime("%A"),
            row["base"],
            row["quote"],
            pair_str,
            row["ns_rate"],
            row["boi_rate"],
            row["gap"],
            row["tolerance"],
            row["result"],
        ]
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=r_idx, column=col, value=val)
            cell.border = _thin_border()
            if fill:
                cell.fill = fill
            if col in (6, 7, 8, 9):
                cell.number_format = "0.000000"
            if col == 1:
                cell.number_format = "YYYY-MM-DD"

    _auto_width(ws)


def _build_ns_tab(wb, rows):
    ws = wb.create_sheet("NS-ER")
    headers = ["Date", "Base Currency", "Quote Currency", "Pair", "NS Exchange Rate"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = _header_font()
        cell.fill = _hfill(C_HEADER_MID)
        cell.border = _thin_border()

    ws.freeze_panes = "A2"

    written = set()
    r_idx = 2
    for row in rows:
        key = (row["date"], row["base"], row["quote"])
        if key in written or row["ns_rate"] is None:
            continue
        written.add(key)
        values = [row["date"], row["base"], row["quote"], f"{row['base']}/{row['quote']}", row["ns_rate"]]
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=r_idx, column=col, value=val)
            cell.border = _thin_border()
            if col == 5:
                cell.number_format = "0.000000"
            if col == 1:
                cell.number_format = "YYYY-MM-DD"
        r_idx += 1

    _auto_width(ws)


def _build_boi_tab(wb, rows):
    ws = wb.create_sheet("BOI-ER")
    headers = ["Date", "Base Currency", "Quote Currency", "Pair", "BOI Exchange Rate", "Source"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = _header_font()
        cell.fill = _hfill(C_HEADER_MID)
        cell.border = _thin_border()

    ws.freeze_panes = "A2"

    written = set()
    r_idx = 2
    for row in rows:
        key = (row["date"], row["base"], row["quote"])
        if key in written or row["boi_rate"] is None:
            continue
        if row["result"] == RESULT_BOI_NO_PUBLISH:
            continue
        written.add(key)
        is_direct = (row["base"], row["quote"]) in DIRECT_PAIRS
        source = "BOI SDMX direct" if is_direct else "Derived (cross-rate)"
        values = [row["date"], row["base"], row["quote"], f"{row['base']}/{row['quote']}", row["boi_rate"], source]
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=r_idx, column=col, value=val)
            cell.border = _thin_border()
            if col == 5:
                cell.number_format = "0.000000"
            if col == 1:
                cell.number_format = "YYYY-MM-DD"
        r_idx += 1

    _auto_width(ws)


def _build_exceptions_tab(wb, rows):
    ws = wb.create_sheet("Exceptions")

    exc_rows = [r for r in rows if r["result"] in (RESULT_EXCEPTION, RESULT_NS_MISSING)]

    ws.merge_cells("A1:J1")
    header_cell = ws["A1"]
    if exc_rows:
        header_cell.value = f"EXCEPTIONS — {len(exc_rows)} item(s) require investigation"
        header_cell.fill = _hfill(C_EXCEPTION)
    else:
        header_cell.value = "No exceptions — All comparisons passed"
        header_cell.fill = _hfill(C_OK)
    header_cell.font = Font(bold=True, size=11)
    header_cell.alignment = Alignment(horizontal="center")

    col_headers = ["Date", "Base", "Quote", "Pair", "NS Rate", "BOI Rate", "Gap", "Tolerance", "Result", "Investigation Notes"]
    for col, h in enumerate(col_headers, 1):
        cell = ws.cell(row=2, column=col, value=h)
        cell.font = _header_font()
        cell.fill = _hfill(C_HEADER_DARK)
        cell.border = _thin_border()

    for r_idx, row in enumerate(exc_rows, 3):
        fill = _result_fill(row["result"])
        values = [
            row["date"], row["base"], row["quote"],
            f"{row['base']}/{row['quote']}",
            row["ns_rate"], row["boi_rate"], row["gap"], row["tolerance"],
            row["result"], ""
        ]
        for col, val in enumerate(values, 1):
            cell = ws.cell(row=r_idx, column=col, value=val)
            cell.border = _thin_border()
            if fill:
                cell.fill = fill
            if col in (5, 6, 7, 8):
                cell.number_format = "0.000000"
            if col == 1:
                cell.number_format = "YYYY-MM-DD"

    _auto_width(ws)


def _build_ipe_evidence(wb, rows, period_start, period_end, ns_json_path, boi_json_path, script_version, demo_mode):
    ws = wb.create_sheet("IPE Evidence")
    ws.sheet_view.showGridLines = False

    ws.merge_cells("B2:F2")
    ws["B2"].value = "IPE Evidence — FR-8 Control Run Metadata"
    ws["B2"].font = Font(bold=True, size=12, color=C_WHITE)
    ws["B2"].fill = _hfill(C_HEADER_DARK)
    ws["B2"].alignment = Alignment(horizontal="center")

    metadata = [
        ("Control reference", "FR-8"),
        ("Period start", period_start.isoformat()),
        ("Period end", period_end.isoformat()),
        ("Run timestamp (UTC)", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Script version", script_version),
        ("Data source — NS", ns_json_path or ("DEMO" if demo_mode else "Not specified")),
        ("Data source — BOI", boi_json_path or ("DEMO" if demo_mode else "Not specified")),
        ("BOI source mode", "DEMO" if demo_mode else (_get_boi_source_mode(boi_json_path) if boi_json_path else "N/A")),
        ("Total rows reconciled", len(rows)),
        ("NS-ER row count", len({(r["date"], r["base"], r["quote"]) for r in rows if r["ns_rate"] is not None})),
        ("BOI-ER row count", len({(r["date"], r["base"], r["quote"]) for r in rows if r["boi_rate"] is not None})),
        ("Exceptions", sum(1 for r in rows if r["result"] == RESULT_EXCEPTION)),
        ("Completeness gaps", sum(1 for r in rows if r["result"] == RESULT_NS_MISSING)),
        ("Tolerance — direct rates", TOLERANCE_DIRECT),
        ("Tolerance — cross-rates", TOLERANCE_CROSS),
        ("Holiday calendar source", "fr-8/references/israeli-holidays.json"),
        ("Run by", "FR-8 automated skill (Claude Code)"),
    ]

    for r_idx, (label, value) in enumerate(metadata, 4):
        lbl = ws.cell(row=r_idx, column=2, value=label)
        lbl.font = Font(bold=True, size=10)
        lbl.border = _thin_border()
        val = ws.cell(row=r_idx, column=3, value=str(value))
        val.font = Font(size=10)
        val.border = _thin_border()
        ws.row_dimensions[r_idx].height = 15

    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 35
    ws.column_dimensions["C"].width = 40


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate FR-8 Exchange Rate WP")
    parser.add_argument("--ns-json", help="Path to _ns_rates.json from NS MCP")
    parser.add_argument("--boi-json", help="Path to _boi_rates.json from fetch_boi.py")
    parser.add_argument("--period-start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--period-end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--output", required=True, help="Output .xlsx path")
    parser.add_argument("--holidays-json", help="Path to custom holidays JSON")
    parser.add_argument("--script-version", default="v1.0.0", help="Git tag or version string")
    parser.add_argument("--demo", action="store_true", help="Generate WP with synthetic data (no live sources)")
    args = parser.parse_args()

    period_start = date.fromisoformat(args.period_start)
    period_end = date.fromisoformat(args.period_end)
    holidays_path = Path(args.holidays_json) if args.holidays_json else None
    holidays = _load_holidays(holidays_path)

    if args.demo:
        print("Running in DEMO mode — synthetic data, zero-gap scenario")
        ns_data = _generate_demo_ns(period_start, period_end, holidays)
        boi_data = _generate_demo_boi(period_start, period_end, holidays)
        ns_json_path = None
        boi_json_path = None
    else:
        if not args.ns_json or not args.boi_json:
            print("ERROR: --ns-json and --boi-json are required unless --demo is used.", file=sys.stderr)
            sys.exit(1)
        print(f"Loading NS data from {args.ns_json}...")
        ns_data = load_ns_json(args.ns_json)
        print(f"  Loaded {len(ns_data)} NS rate records")

        print(f"Loading BOI data from {args.boi_json}...")
        boi_data = load_boi_json(args.boi_json)
        print(f"  Loaded {len(boi_data)} BOI rate records")
        ns_json_path = args.ns_json
        boi_json_path = args.boi_json

    print("Running reconciliation...")
    rows = reconcile(ns_data, boi_data, period_start, period_end, holidays)

    ok = sum(1 for r in rows if r["result"] == RESULT_OK)
    exc = sum(1 for r in rows if r["result"] == RESULT_EXCEPTION)
    miss = sum(1 for r in rows if r["result"] == RESULT_NS_MISSING)
    carry = sum(1 for r in rows if r["result"] == RESULT_NS_CARRY_FWD)
    total = ok + exc + miss + carry
    print(f"  {total} comparisons: {ok} OK, {exc} exceptions, {miss} missing, {carry} carry-fwd")

    print("Building workbook...")
    wb = build_workbook(
        rows, period_start, period_end,
        ns_json_path if not args.demo else None,
        boi_json_path if not args.demo else None,
        args.script_version,
        args.demo,
    )

    out_path = args.output
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    wb.save(out_path)
    print(f"\nOK: Workbook saved → {out_path}")

    if exc > 0 or miss > 0:
        print(f"\nWARNING: {exc} rate exception(s), {miss} completeness gap(s) — review Exceptions tab")
        sys.exit(2)


if __name__ == "__main__":
    main()
