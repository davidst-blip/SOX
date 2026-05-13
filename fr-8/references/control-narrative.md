# FR-8 Control Narrative

## Control Reference
**Control ID:** FR-8  
**Process:** Financial Reporting  
**Sub-process:** Foreign Currency Translation  
**Matrix location:** Perion Network SOX Control Matrix

## Control Description
On a monthly basis, the Bookkeeper retrieves NetSuite exchange rates and compares
them to the Bank of Israel (BOI) published rates for ILS/USD, ILS/EUR, and ILS/GBP.
Any differences are investigated. The Bookkeeping Manager reviews the comparison
working paper, signs, and dates to evidence review.

## Risk Addressed
**Risk statement:** Foreign transactions are translated at the incorrect foreign
exchange rate, resulting in materially misstated financial statements.

## Control Attributes (as of matrix last update)
| Attribute | Value |
|---|---|
| Risk grade | Low |
| Control type | Detective |
| Control nature | Manual |
| Frequency | Monthly |
| Key control | Yes |
| IPE | Yes |
| Preparer | Bookkeeper (Kati) |
| Reviewer | Bookkeeping Manager |

## Risk-Grade Observation
The Low grade is potentially understated. FX translation errors flow directly into
the ASC 830 OCI translation adjustment and affect every consolidated subsidiary
balance. For a Key Control with IPE = Yes covering three functional currencies on
a monthly basis, **Medium** is more defensible under AS 2201. Recommend discussing
with SDOF (Gilad) and Corporate Controller (Eden) at the next matrix refresh cycle.

## Tautology Risk (Design Gap)
If Perion's NetSuite "Currency Exchange Rate Provider" (Setup → Company → General
Preferences → Currency) is configured to pull daily rates from Bank of Israel
directly, then NS rates ARE BOI rates by definition. In this scenario, the
monthly comparison is not an independent tie-out — it is a check that:
- The NS-BOI automated feed did not break (stale rates would be detected)
- No one manually overrode a rate after auto-population

This is a meaningful control, but it is not what the matrix says the control
does. The current wording implies two independent data sources are being compared.
**Action required:** Confirm the NS Currency Exchange Rate Provider setting with
IT admin, then rewrite the control activity at the next matrix refresh to accurately
describe what the test proves.

## Precision / Tolerance
The WP uses `ABS(NS_rate − BOI_rate)` for each comparison. Tolerances applied
in this automated skill:
- **Direct rates** (ILS/USD, ILS/EUR, ILS/GBP): ≤ 0.0001
- **Cross-rates** (USD/EUR, EUR/USD, USD/GBP, GBP/USD, USD/ILS, EUR/ILS): ≤ 0.000001

Rationale: Cross-rates derived from two BOI rates via division accumulate rounding
to 6+ decimal places; the tighter threshold avoids false positives while still
catching any material pricing errors. These thresholds should be documented in the
matrix's Precision / Tolerance field at next refresh.
