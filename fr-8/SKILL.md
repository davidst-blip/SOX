# Skill: FR-8 — Monthly Exchange Rate Reconciliation

## Triggers
Activate this skill when the user types any of:
- FR-8, FR8
- exchange rate review, FX rate review
- monthly currency WP, monthly FX reconciliation
- BOI NetSuite reconciliation
- Daily Exchange Rates
- run FR-8

## Control Reference
**FR-8 | Financial Reporting | Foreign Currency Translation | Perion Network SOX Matrix**

Risk: Foreign transactions translated at incorrect FX rate
Nature: Manual (reviewer signs Dashboard) | Detective | Monthly | Key Control | IPE = Yes
Preparer: Bookkeeper (Kati) | Reviewer: Bookkeeping Manager

---

## Workflow — Step by Step

### Step 0 — Confirm period
Say in one sentence: "Running FR-8 for [Month Year] ([period_start] to [period_end]) — OK?"
Derive period_start = first day of month, period_end = last day of month.
Wait for user confirmation before proceeding.

---

### Step 1 — Pull NetSuite exchange rates

Call `ns_runCustomSuiteQL` with this exact query (substitute the two date literals):

```sql
SELECT
    cr.effectivedate,
    bc.symbol AS basecurrency,
    tc.symbol AS transactioncurrency,
    cr.exchangerate,
    cr.fxsourcemethod
FROM currencyrate cr
INNER JOIN currency bc ON cr.basecurrency = bc.id
INNER JOIN currency tc ON cr.transactioncurrency = tc.id
WHERE cr.basecurrency   IN (1, 2, 4, 5)
  AND cr.transactioncurrency IN (1, 2, 4, 5)
  AND bc.symbol != tc.symbol
  AND cr.fxsourcemethod = 'MANUAL'
  AND cr.effectivedate >= TO_DATE('{period_start}','YYYY-MM-DD')
  AND cr.effectivedate <= TO_DATE('{period_end}','YYYY-MM-DD')
ORDER BY cr.effectivedate, bc.symbol, tc.symbol
```

**Currency IDs (Perion tenant — confirmed):**
| Symbol | NS Internal ID |
|--------|---------------|
| USD    | 1             |
| ILS    | 2             |
| EUR    | 4             |
| GBP    | 5             |

**Why `fxsourcemethod = 'MANUAL'`:**
Perion has two accounting books. The secondary book is auto-populated by an
Xignite feed (`fxsourcemethod = 'DIRECT'`). The primary accounting book uses
manually-entered BOI rates (`fxsourcemethod = 'MANUAL'`). FR-8 tests the primary
book — the filter is mandatory to avoid including the wrong rates.

After the query returns, write its `data` array to `/tmp/_ns_rates_raw.json`:

```python
import json
# 'result' is the dict returned by ns_runCustomSuiteQL
with open('/tmp/_ns_rates_raw.json', 'w') as f:
    json.dump(result, f)
```

Then run the formatter:

```bash
python3 ~/.claude/skills/fr-8/scripts/format_ns_output.py \
    --input  /tmp/_ns_rates_raw.json \
    --output /tmp/_ns_rates.json \
    --period-start {period_start} \
    --period-end   {period_end}
```

Expected output: `OK: N NS rate records (filtered from M raw) → /tmp/_ns_rates.json`

**If SuiteQL fails** (permission error or unknown field):
1. Run `ns_listSavedSearches` with query `currency rate`
2. Look for a saved search with record type "Currency Rate"
3. Run it via `ns_runSavedSearch` with the appropriate searchId
4. Write the result array to `/tmp/_ns_rates_raw.json` and run the formatter

---

### Step 2 — Pull BOI exchange rates

```bash
python3 ~/.claude/skills/fr-8/scripts/fetch_boi.py \
    --period-start {period_start} \
    --period-end   {period_end} \
    --output /tmp/_boi_rates.json
```

Expected output: `OK: N BOI observations written to /tmp/_boi_rates.json`

**If BOI API is blocked** (Cloudflare / HTTP error), tell the user:
> "BOI API blocked from this machine. Go to boi.org.il → Exchange Rates →
> Export → CSV, save the file, then re-run with: --boi-csv /path/to/file.csv"

Then re-run:
```bash
python3 ~/.claude/skills/fr-8/scripts/fetch_boi.py \
    --boi-csv /path/to/downloaded.csv \
    --period-start {period_start} \
    --period-end   {period_end} \
    --output /tmp/_boi_rates.json
```

---

### Step 3 — Generate working paper

```bash
SCRIPT_VERSION=$(git -C ~/.claude/skills/fr-8 describe --tags 2>/dev/null || echo v1.0.0)
OUTPUT_DIR="${FR8_OUTPUT_DIR:-/tmp}"
OUTPUT_FILE="${OUTPUT_DIR}/Daily_Exchange_Rates_-_$(date -d '{period_start}' '+%m_%Y').xlsx"

python3 ~/.claude/skills/fr-8/scripts/run_fr8.py \
    --ns-json      /tmp/_ns_rates.json \
    --boi-json     /tmp/_boi_rates.json \
    --period-start {period_start} \
    --period-end   {period_end} \
    --output       "${OUTPUT_FILE}" \
    --script-version "${SCRIPT_VERSION}"
```

Exit codes:
- `0` = PASS (no exceptions, no gaps)
- `2` = REVIEW REQUIRED (exceptions or completeness gaps found)

---

### Step 4 — Report to user

Report:
1. Output file path (full path so user can find it)
2. **NS extract**: N records pulled (after MANUAL filter)
3. **BOI extract**: N observations (N publishing days × 3 currencies)
4. **Reconciliation**: N publishing days × 9 pairs = N comparisons
5. **Result**: PASS / REVIEW REQUIRED
6. If exceptions or gaps: list each one with date, pair, NS rate, BOI rate, gap

---

### Step 5 — Reviewer actions (manual — not automated)

Remind the user to:
1. Open the workbook → **Dashboard** tab
2. If exceptions: read the **Exceptions** tab and investigate each one
3. Check any `NS_CARRY_FWD` rows — confirm NS genuinely carried the prior rate
   (expected on holidays/weekends; investigate if on a BOI publish day)
4. Add notes to the **Reviewer Notes** box on the Dashboard
5. Sign and date the **"Reviewed by (Bookkeeping Manager)"** row
6. Save to the SOX evidence folder

---

## Environment Matrix

| Environment | BOI data | NS data | Manual step? |
|---|---|---|---|
| **Claude Code** (recommended) | Live API via `fetch_boi.py` | NS MCP SuiteQL | None |
| **claude.ai** | Manual CSV upload | NS MCP SuiteQL | Attach BOI CSV |
| **Claude API / SDK** | Live API (if network available) | NS MCP SuiteQL | None |

---

## NetSuite Schema Notes (Perion tenant)

Confirmed by live query on 2026-05-13:

| Field | Value |
|---|---|
| Table | `currencyrate` |
| Date column | `effectivedate` — returned as `DD/MM/YYYY` by SuiteQL |
| Base currency | `basecurrency` — **numeric ID** (must JOIN to `currency` table) |
| Quote currency | `transactioncurrency` — **numeric ID** (must JOIN to `currency` table) |
| Rate column | `exchangerate` |
| Book discriminator | `fxsourcemethod` — `'MANUAL'` = primary book (BOI), `'DIRECT'` = Xignite secondary book |
| Currency IDs | USD=1, ILS=2, EUR=4, GBP=5 |
| Accounting books | Primary (id=1), Secondary (id=2) — no `accountingbook` column on `currencyrate` |

---

## IPE Rules (AS 2201.39)

1. **Completeness**: IPE Evidence tab records NS row count, BOI observation count,
   holiday calendar version. Completeness gaps (BOI publish day missing NS rate) are
   flagged explicitly.

2. **Accuracy**: NS-ER and BOI-ER tabs preserve source data verbatim. Cross-rate
   derivation formulas documented in `references/reconciliation-design.md`.

3. **Script version**: `--script-version` writes the git tag to IPE Evidence. Commit
   and tag before each run to give auditors a traceable logic version per period.

4. **Reviewer precision**: Dashboard sign-off requires prep + review signatures and
   free-text Reviewer Notes documenting what the reviewer validated.

---

## Known Open Issues (next matrix refresh)

1. **Tautology risk**: Confirm with IT admin whether NS Currency Exchange Rate Provider
   is configured to BOI. If yes, the DIRECT rates are from BOI feed and the MANUAL
   rates may be from a different source — rewrite the control narrative accordingly.

2. **Risk grade**: FR-8 is rated Low; Medium is more appropriate for a Key Control
   with IPE covering ASC 830 OCI on multiple currencies.

3. **Tolerance not in matrix**: The 0.0001 / 0.000001 thresholds are in the WP but
   not in the Perion SOX matrix. Add a Precision/Tolerance field at next refresh.

---

## File Structure

```
fr-8/
├── SKILL.md                              ← this file
├── install.md                            ← one-time setup guide
├── mcp.json.template                     ← NetSuite MCP config snippet
├── scripts/
│   ├── format_ns_output.py               ← filters & saves MCP SuiteQL result
│   ├── fetch_boi.py                      ← BOI SDMX live fetcher
│   └── run_fr8.py                        ← reconciliation + WP generator
└── references/
    ├── control-narrative.md
    ├── reconciliation-design.md
    └── israeli-holidays.json
```

---

## Quick Demo Test

Verify the skill works without live sources:

```bash
python3 ~/.claude/skills/fr-8/scripts/run_fr8.py \
    --demo \
    --period-start 2026-04-01 --period-end 2026-04-30 \
    --output /tmp/FR8_demo_Apr2026.xlsx
```

Expected: `243 comparisons: 243 OK, 0 exceptions, 0 missing, 0 carry-fwd`
