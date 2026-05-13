# FR-8 Reconciliation Design

## Overview
FR-8 compares NetSuite (NS) exchange rates to Bank of Israel (BOI) published rates
for each business day in the period. Nine currency pair comparisons are performed
per day across three data sources.

## Data Sources

### NetSuite
- **Access method:** SuiteQL on `currencyrate` table (primary)
- **Fallback:** Saved search "Currency Exchange Rates" if SuiteQL schema differs
- **SuiteQL query (primary):**
  ```sql
  SELECT effectivedate, basecurrency, transactioncurrency, exchangerate
  FROM currencyrate
  WHERE effectivedate >= '{start_date}' AND effectivedate <= '{end_date}'
  AND basecurrency IN ('ILS','USD','EUR','GBP')
  AND transactioncurrency IN ('ILS','USD','EUR','GBP')
  ORDER BY effectivedate, basecurrency, transactioncurrency
  ```
- **Rates extracted:** ILS→USD, ILS→EUR, ILS→GBP (direct NS rates)

### Bank of Israel (BOI)
- **Live endpoint (Claude Code):** `https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0/RER_USD_ILS,RER_EUR_ILS,RER_GBP_ILS`
  - Returns SDMX-CSV; column names vary by deployment — parser handles both
  - `--debug` flag writes raw response to stdout for troubleshooting
- **Fallback (CSV upload):** `--boi-csv path/to/file.csv` for environments without network
- **Current-rate API (reference only):** `https://boi.org.il/PublicApi/GetExchangeRates`
  - Returns JSON with most-recent rate only; not used for reconciliation

### BOI Rate Definitions
BOI publishes ILS-base rates:
- `RER_USD_ILS` → how many ILS per 1 USD (e.g., 3.65 means 1 USD = 3.65 ILS)
- `RER_EUR_ILS` → how many ILS per 1 EUR
- `RER_GBP_ILS` → how many ILS per 1 GBP

NS stores rates in the same convention for direct pairs.

## Nine Comparison Pairs

### Direct Pairs (BOI source vs. NS stored rate)
| Pair | NS field | BOI series | Tolerance |
|---|---|---|---|
| ILS/USD (ILS per USD) | currencyrate where base=ILS, txn=USD | RER_USD_ILS | 0.0001 |
| ILS/EUR (ILS per EUR) | currencyrate where base=ILS, txn=EUR | RER_EUR_ILS | 0.0001 |
| ILS/GBP (ILS per GBP) | currencyrate where base=ILS, txn=GBP | RER_GBP_ILS | 0.0001 |

### Cross-Rates (derived from BOI direct rates vs. NS stored rate)
Cross-rates are not published directly by BOI; they are derived from the three
ILS-base rates. Formula: `USD/EUR = RER_EUR_ILS / RER_USD_ILS`

| Pair | NS field | BOI derivation | Tolerance |
|---|---|---|---|
| USD/ILS | base=USD, txn=ILS | 1 / RER_USD_ILS | 0.000001 |
| EUR/ILS | base=EUR, txn=ILS | 1 / RER_EUR_ILS | 0.000001 |
| USD/EUR | base=USD, txn=EUR | RER_EUR_ILS / RER_USD_ILS | 0.000001 |
| EUR/USD | base=EUR, txn=USD | RER_USD_ILS / RER_EUR_ILS | 0.000001 |
| USD/GBP | base=USD, txn=GBP | RER_GBP_ILS / RER_USD_ILS | 0.000001 |
| GBP/USD | base=GBP, txn=USD | RER_USD_ILS / RER_GBP_ILS | 0.000001 |

## Completeness Check
The script validates that every BOI publishing day in the period has a corresponding
NS rate loaded. BOI publishing days = all calendar days in the period MINUS:
1. Saturdays (BOI never publishes)
2. Israeli public holidays listed in `references/israeli-holidays.json`

Any NS rate that is missing for a BOI publishing day is flagged as a
**Completeness Gap** (separate from exchange-rate exceptions). Completeness gaps
indicate a potential NS feed outage or configuration issue.

## Classification of Each Day × Pair
| Result code | Meaning |
|---|---|
| OK | ABS(NS − BOI) ≤ tolerance |
| EXCEPTION | ABS(NS − BOI) > tolerance — requires investigation and sign-off |
| BOI_NO_PUBLISH | BOI did not publish on this date (holiday/Saturday) — NS carry-forward expected |
| NS_MISSING | BOI published but NS has no rate loaded — completeness gap |
| NS_CARRY_FWD | NS rate matches prior-day rate — possible carry-forward; flagged for review |

## Workbook Output Structure
| Tab | Contents |
|---|---|
| Dashboard | Summary: period, comparison count, OK/Exception/Gap counts, sign-off block, reviewer notes |
| NS-ER | Raw NS rate extract, one row per date × pair |
| BOI-ER | Raw BOI rate extract, one row per date × currency |
| Reconciliation | Full 9-pair × N-day comparison with gap column and result code |
| Exceptions | Filtered view — only rows with result code EXCEPTION or NS_MISSING |
| IPE Evidence | Run metadata: timestamp, source mode (live/CSV), script version, row counts |
