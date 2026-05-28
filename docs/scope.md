# SOX Sentinel — V1 Scope

## In scope (V1)

- Ingest Perion's RCM (all entities: Perion Ltd, Undertone USA, Hivestack Canada, Vidazoo, CodeFuel)
- For each control: generate structured test plan + attributes (with or without prior-year docs)
- Ingest completed workpapers (Excel, Word, PDF)
- Analyze workpaper against test plan → gap report with severity and citations
- Dashboard: control status, gap reports, review readiness per period

## Explicitly out of scope (V1 — defer to V2)

- **Performing controls** — the existing Claude skills (EX-1, EX-4, FR-6) do this; keep them separate
- **Live system integration** — no live NetSuite/Salesforce pulls; user uploads evidence files
- **Multi-tenant / external sale** — internal Perion use only
- **Control inter-dependency modelling** — e.g. EX-4 assumes EX-1 is working
- **Versioned RCM snapshots** — latest upload wins; track via parsed_at timestamp
- **Auditor-facing portal** — V2 candidate once gap reports are validated internally

## Perion entities in scope

| Entity | Code | Notes |
|--------|------|-------|
| Perion Network Ltd. | PERION | HQ, Israel |
| Undertone USA | UT USA | New York |
| Hivestack Canada | HS CA | Montreal |
| Vidazoo | VZ | Israel |
| CodeFuel | CF | Israel |
