# SOX Sentinel — Architecture

## Three-layer design

```
┌─────────────────────────────────────────────────────┐
│  Dashboard (React or extended scoping dashboard)     │
│   Control list · Test plan viewer · Gap reports      │
└────────────────┬────────────────────────────────────┘
                 │ HTTP (JSON)
        ┌────────┴────────┐
        │   FastAPI       │
        │   backend/      │
        └────────┬────────┘
                 │
   ┌─────────────┼─────────────┬──────────────┐
   ▼             ▼             ▼              ▼
rcm_parser  test_plan_    workpaper_    gap_analyzer
            generator     ingester
   └─────────────┴─────────────┴──────────────┘
                       │
              ┌────────┴────────┐
              │   PostgreSQL    │
              │  (controls,     │
              │   test plans,   │
              │   workpapers,   │
              │   gap reports)  │
              └────────┬────────┘
                       │
              ┌────────┴────────┐
              │  Anthropic API  │
              │  (LLM calls)    │
              └─────────────────┘
```

## Module responsibilities

| Module | Role | LLM? |
|--------|------|------|
| `engine/rcm_parser` | Parse Excel RCM → structured `Control` objects | Yes (normalize free text) |
| `engine/test_plan_generator` | Control → numbered `TestPlan` with `TestStep`s | Yes (generation) |
| `engine/workpaper_ingester` | Excel/Word/PDF → `WorkpaperContent` | Yes (tab classification) |
| `engine/gap_analyzer` | `Workpaper` + `TestPlan` → `GapReport` | Yes (gap detection) |
| `engine/connectors` | NetSuite / Salesforce / Drive integrations | No |
| `backend/` | FastAPI endpoints, DB session, file upload | No |
| `backend/db/` | SQLAlchemy models + Alembic migrations | No |

## User roles and access

Three roles, Perion-internal only (no tenant isolation):

| Role | Access |
|------|--------|
| ADMIN | Everything — configure controls, manage users, all reports |
| CONTROL_OWNER | Own assigned controls + workpapers only |
| REVIEWER | All workpapers read + sign-off; cannot edit controls |

Auth: email + password, JWT tokens. Each API request carries a JWT; FastAPI dependency injects the current user. Role checks happen at the endpoint level.

## LLM model selection

- **Parsing / classification** (rcm_parser, workpaper_ingester tab classifier): `claude-sonnet-4-6` — fast, cheap
- **Test plan generation**: `claude-sonnet-4-6` — structured output, deterministic at temperature=0
- **Gap analysis**: `claude-opus-4-7` — highest accuracy for judgment calls

## Caching strategy (cost control)

Every LLM result is cached by a composite key. Zero re-spend if inputs unchanged:

| Result | Cache key | Invalidated when |
|--------|-----------|-----------------|
| Parsed control | `hash(rcm_row)` | RCM re-uploaded with changes |
| Test plan | `(control_id, period, rcm_version)` | Control description changes |
| Workpaper parse | `file_hash` | File re-uploaded |
| Gap report | `(workpaper file_hash, test_plan_id, test_plan_version, prompt_hash)` | Any input changes |

## Key design decisions

- LLM calls are isolated behind `engine/llm.py` — swap provider without touching business logic
- All LLM calls logged with prompt hash, model, token cost, latency
- Gap reports are idempotent: same workpaper + same test plan → same report (temperature=0, pinned model version)
- Real Perion documents never committed to repo (`uploads/`, `samples/real/` gitignored)
