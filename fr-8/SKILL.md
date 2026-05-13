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

## Workflow (Claude Code — fully automated)

### Step 0 — Confirm period
Ask the user in one sentence: "Running FR-8 for [Month Year] — OK?"
Derive period_start and period_end from the month name. Wait for confirmation.

### Step 1 — Pull NetSuite rates
Use the NetSuite MCP to run a SuiteQL query:

```sql
SELECT effectivedate, basecurrency, transactioncurrency, exchangerate
FROM currencyrate
WHERE effectivedate >= '{period_start}'
  AND effectivedate <= '{period_end}'
  AND basecurrency   IN ('ILS','USD','EUR','GBP')
  AND transactioncurrency IN ('ILS','USD','EUR','GBP')
ORDER BY effectivedate, basecurrency, transactioncurrency
```

Save the result as JSON to `/tmp/_ns_rates.json`.

**If SuiteQL fails** (schema mismatch or permission error):
1. Try `ns_listSavedSearches` to find "Currency Exchange Rates" saved search
2. Run it via `ns_runSavedSearch`
3. Map result columns to `{effectivedate, basecurrency, transactioncurrency, exchangerate}`
4. Save to `/tmp/_ns_rates.json` in the same format

NS JSON schema expected by `run_fr8.py`:
```json
[
  {"effectivedate": "2026-04-01", "basecurrency": "ILS",
   "transactioncurrency": "USD", "exchangerate": 3.65},
  ...
]
```

### Step 2 — Pull BOI rates
Run `fetch_boi.py` via bash:

```bash
python3 ~/.claude/skills/fr-8/scripts/fetch_boi.py \
    --period-start {period_start} \
    --period-end   {period_end} \
    --output /tmp/_boi_rates.json
```

If BOI API is blocked (Cloudflare / network error), tell the user:
> "BOI API is not reachable from this machine. Please download the CSV from
> boi.org.il → Exchange Rates → Export and re-run with --boi-csv /path/to/file.csv"

Then re-run with the `--boi-csv` flag.

### Step 3 — Generate working paper
```bash
python3 ~/.claude/skills/fr-8/scripts/run_fr8.py \
    --ns-json      /tmp/_ns_rates.json \
    --boi-json     /tmp/_boi_rates.json \
    --period-start {period_start} \
    --period-end   {period_end} \
    --output       "${FR8_OUTPUT_DIR:-/tmp}/Daily_Exchange_Rates_-_{MM}_{YYYY}.xlsx" \
    --script-version $(git -C ~/.claude/skills/fr-8 describe --tags 2>/dev/null || echo v1.0.0)
```

### Step 4 — Present results
Report to user:
- Output file path
- Summary: publishing days, comparisons performed, OK / exceptions / gaps
- Overall result: PASS or REVIEW REQUIRED
- Any exceptions or completeness gaps, with context

### Step 5 — Reviewer action (not automated)
Remind the user:
1. Open the workbook → Dashboard tab
2. Review the Exceptions tab (if any)
3. Check carry-forward dates against NS for legitimacy
4. Add Reviewer Notes to the Dashboard sign-off block
5. Sign and date the "Reviewed by" row
6. Save to the SOX evidence folder

---

## Environment Matrix

| Environment | BOI data | NS data | Notes |
|---|---|---|---|
| **Claude Code** (recommended) | Live API via `fetch_boi.py` | NS MCP | Fully automated |
| **claude.ai** | Manual CSV upload | NS MCP | Attach BOI CSV, skill uses `--boi-csv` |
| **Claude API / SDK** | Live API (if network available) | NS MCP | Same as Claude Code |

---

## IPE Rules (AS 2201.39)

The working paper constitutes IPE (Information Produced by the Entity). To satisfy
PCAOB standards:

1. **Completeness**: The IPE Evidence tab must record NS row count, BOI observation
   count, and the holiday calendar version used. Completeness gaps (BOI publish day
   with no NS rate) are flagged explicitly.

2. **Accuracy**: Source data for both NS and BOI is preserved in the NS-ER and BOI-ER
   tabs verbatim. Cross-rates show the derivation formula in reconciliation-design.md.

3. **Script version**: The `--script-version` flag writes the git tag to the IPE
   Evidence tab. The tag must be committed before the run to prove no mid-period
   change occurred. Auditors can trace any period's logic to an exact git commit.

4. **Reviewer precision**: The Dashboard sign-off block requires both prep and review
   signatures plus free-text Reviewer Notes documenting what the reviewer validated.

---

## Known Open Issues (address at next matrix refresh)

1. **Tautology risk**: If NS Currency Exchange Rate Provider = BOI, the comparison
   is not independent. Confirm with IT admin, then rewrite control narrative.

2. **Risk grade**: FR-8 is rated Low; Medium is more appropriate for a Key Control
   with IPE covering ASC 830 OCI on multiple currencies.

3. **Tolerance not in matrix**: The 0.0001 / 0.000001 thresholds are in the WP
   but not in the Perion SOX matrix. Add a Precision/Tolerance field.

---

## File Structure

```
fr-8/
├── SKILL.md                              ← this file
├── install.md                            ← one-time setup guide
├── mcp.json.template                     ← NetSuite MCP config snippet
├── scripts/
│   ├── run_fr8.py                        ← WP generator
│   └── fetch_boi.py                      ← BOI live fetcher
└── references/
    ├── control-narrative.md
    ├── reconciliation-design.md
    └── israeli-holidays.json
```

---

## Quick Demo Test
To verify the skill works without live data sources:

```bash
python3 ~/.claude/skills/fr-8/scripts/run_fr8.py \
    --demo \
    --period-start 2026-04-01 --period-end 2026-04-30 \
    --output /tmp/FR8_demo_Apr2026.xlsx
```

Expected output: `243 comparisons: 243 OK, 0 exceptions, 0 missing, 0 carry-fwd`
