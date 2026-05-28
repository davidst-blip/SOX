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

## LLM model selection

- **Parsing / classification** (rcm_parser, workpaper_ingester tab classifier): `claude-sonnet-4-6` — fast, cheap
- **Generation + analysis** (test_plan_generator, gap_analyzer): `claude-opus-4-7` — highest accuracy for judgment calls

## Key design decisions

- LLM calls are isolated behind `engine/llm.py` — swap provider without touching business logic
- All LLM calls logged with prompt hash, model, token cost, latency
- Gap reports are idempotent: same workpaper + same test plan → same report (temperature=0, pinned model version)
- Real Perion documents never committed to repo (`uploads/`, `samples/real/` gitignored)
