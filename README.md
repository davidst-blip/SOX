# SOX Sentinel

Internal SOX documentation review and gap-analysis platform for Perion Network Ltd.

## What it does

1. **RCM ingestion** — upload the Risk Control Matrix; platform parses each control into structured attributes
2. **Test plan generation** — AI generates numbered test steps + evidence requirements per control
3. **Workpaper ingestion** — upload completed workpapers (Excel, Word, PDF)
4. **Gap analysis** — platform reviews each workpaper against its test plan and flags missing procedures, evidence, sign-off, IPE/C&A

## Status

Phase 0 — Foundations

## Setup

See [docs/setup.md](docs/setup.md)

## Architecture

See [docs/architecture.md](docs/architecture.md)

## Scope

See [docs/scope.md](docs/scope.md)

## Important

Real Perion SOX documents must never be committed to this repo.  
Use `samples/real/` locally (it is gitignored). Only synthetic/anonymized samples go in `samples/synthetic/`.
