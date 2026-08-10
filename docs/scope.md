# SOX Sentinel — V1 Scope

## In scope (V1)

- Multi-user platform: Admin, Control Owner, Reviewer roles (Perion-internal only)
- Ingest Perion's RCM (all entities); controls assigned to specific owners
- For each control: generate structured test plan + attributes (with or without prior-year docs)
- Control owners upload completed workpapers; platform parses them
- Gap analysis: workpaper reviewed against test plan → gap report with severity and citations
- Reviewer signs off on gap reports before Big 4 review
- Dashboard: each user sees their assigned controls, gap reports, review status

## Explicitly out of scope (V1 — defer to V2)

- **Performing controls** — the existing Claude skills (EX-1, EX-4, FR-6) do this; keep them separate
- **Live system integration** — no live NetSuite/Salesforce pulls; users upload evidence files
- **External auditor access** — Big 4 read-only portal is a V2 candidate
- **Multi-company / SaaS** — Perion-internal only; no tenant isolation needed
- **Control inter-dependency modelling** — e.g. EX-4 assumes EX-1 is working
- **SSO / Azure AD integration** — email + password for V1; SSO is a V2 upgrade

## User roles

| Role | Who | Can do |
|------|-----|--------|
| ADMIN | David (davidst@perion.com) | Configure controls, manage users, view everything |
| CONTROL_OWNER | AP Manager, AR Manager, etc. | View assigned controls, upload workpapers, view own gap reports |
| REVIEWER | Eldar (or designated reviewer) | View all workpapers, sign off on gap reports, add reviewer notes |

## Perion entities in scope

| Entity | Notes |
|--------|-------|
| Perion Network Ltd. | HQ, Israel |
| Undertone USA | New York |
| Hivestack Canada | Montreal |
| Vidazoo | Israel |
| CodeFuel | Israel |

## Cost model

LLM calls are the only variable cost. Design principle: **run once, cache forever** (keyed by file hash + test plan version).

| Operation | Model | Est. cost | Trigger |
|-----------|-------|-----------|---------|
| RCM control parsing | claude-sonnet-4-6 | ~$0.01/control | Once on RCM upload |
| Test plan generation | claude-sonnet-4-6 | ~$0.05/control | Once per period, reused unless RCM changes |
| Workpaper tab classification | claude-haiku-4-5 | ~$0.002/upload | Once per file (keyed to file hash) |
| Gap analysis | claude-opus-4-7 | ~$0.50–$2.00/report | Once per workpaper version — cached if file unchanged |

Estimated full-cycle cost per control per quarter: **~$2–3**.  
At 30 controls × 4 quarters = ~$240–360/year in API costs.

## ROI metrics to track from day one

- **Time saved**: minutes from workpaper upload → "ready for review" vs. current manual review cycle
- **Internal catch rate**: gaps flagged by platform before auditor review ÷ total gaps found
- **Auditor time reduction**: hours saved on Big 4 review (at ~$300–500/hr, each hour saved = significant ROI)
