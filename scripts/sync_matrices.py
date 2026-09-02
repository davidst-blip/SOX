"""
SOX Sentinel sync script — two modes in one command.

Usage:
    python scripts/sync_matrices.py                 # sync both matrices AND testing docs
    python scripts/sync_matrices.py --matrices-only # control definitions only
    python scripts/sync_matrices.py --testing-only  # testing documentation only
    python scripts/sync_matrices.py --dry-run       # preview, no DB writes
    python scripts/sync_matrices.py --force         # reprocess all files even if unchanged

─── What gets synced ────────────────────────────────────────────────────────

MATRICES (control definitions)
  Folder : G:\\Shared drives\\SOX\\SOX404\\Periods\\2026\\Matrix\\Matrix by process
  Files  : any .xlsx / .xlsm in the folder (or subfolders)
  Logic  : each row = one control → Haiku normalizes → upsert into controls table

TESTING DOCUMENTATION (prior-year evidence for test plan generator)
  Folder : G:\\Shared drives\\SOX\\SOX404\\Periods\\2026\\Matrix\\PH1 Testing
  Layout : PH1 Testing / {Entity} / {ProcessCode- ControlCode} / {any file}
             e.g.  Perion / EX- EX-1 / AP review workpaper.xlsx
  Logic  : entity from subfolder name, control code from control folder name (regex)
           → file parsed into sections → stored as knowledge_entry (no LLM needed for routing)
           → Haiku summarizes what was tested (runs only on new/changed files)

Change detection: SHA-256 hash of every file.
  Unchanged file = skip (zero LLM cost).
  New or modified file = re-parse + re-summarize.
"""

import argparse
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from anthropic import Anthropic  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.db.base import Base  # noqa: E402
from backend.db.models import ControlModel, KnowledgeEntryModel, MatrixSyncModel  # noqa: E402
from backend.settings import get_settings  # noqa: E402
from engine.rcm_parser.excel_parser import parse_rcm_excel  # noqa: E402
from engine.rcm_parser.llm_normalizer import PARSER_VERSION, normalize_row  # noqa: E402
from engine.workpaper_ingester.file_parser import parse_workpaper  # noqa: E402
from engine.workpaper_ingester.summarizer import summarize_workpaper  # noqa: E402

# ─── Default paths ────────────────────────────────────────────────────────────

DEFAULT_MATRICES = Path(r"G:\Shared drives\SOX\SOX404\Periods\2026\Matrix\Matrix by process")
DEFAULT_TESTING  = Path(r"G:\Shared drives\SOX\SOX404\Periods\2026\Matrix\PH1 Testing")
PERIOD = "PH1 2026"  # period label stored on every knowledge entry from this folder

# Entity subfolder name → PerionEntity enum value
ENTITY_MAP = {
    "perion":  "Perion Network Ltd.",
    "hive":    "Hivestack Canada",
    "ht":      "Hivestack Canada",  # alternate abbreviation
    "ut":      "Undertone USA",
    "vidazoo": "Vidazoo",
}

TESTING_EXTENSIONS = {".xlsx", ".xlsm", ".docx", ".pdf"}


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_control_code(folder_name: str) -> str | None:
    """
    Extract control code from a folder name like 'EX- EX-1' or 'FR- FR-6'.
    Returns 'EX-1', 'FR-6', etc., or None if not found.
    """
    # Match patterns like EX-1, FR-6, HS-FR-6, RE-17
    match = re.search(r'\b([A-Z]{1,4}-[A-Z]{0,4}-?\d+)\b', folder_name)
    return match.group(1) if match else None


def _map_entity(subfolder_name: str) -> str | None:
    """Map entity subfolder name to PerionEntity value."""
    return ENTITY_MAP.get(subfolder_name.strip().lower())


def _controls_differ(existing: ControlModel, new_desc: str,
                     new_freq: str, new_risk: str, new_name: str) -> bool:
    return (
        existing.description != new_desc
        or existing.frequency != new_freq
        or existing.risk_level != new_risk
        or existing.name != new_name
    )


# ─── Matrix sync ──────────────────────────────────────────────────────────────


def _upsert_control(db, control, source_file: str, dry_run: bool) -> str:
    entity_str = str(control.entity)
    existing = (
        db.query(ControlModel)
        .filter(ControlModel.code == control.code, ControlModel.entity == entity_str)
        .first()
    )
    assertions = [str(a) for a in control.assertions]
    attributes = [a.model_dump() for a in control.attributes]

    if existing is None:
        if not dry_run:
            db.add(ControlModel(
                code=control.code,
                name=control.name,
                description=control.description,
                entity=entity_str,
                control_type=str(control.control_type),
                nature=str(control.nature),
                frequency=str(control.frequency),
                risk_level=str(control.risk_level),
                process=control.process,
                sub_process=control.sub_process,
                coso_component=str(control.coso_component) if control.coso_component else None,
                assertions=assertions,
                attributes=attributes,
                owner_name=control.owner,
                reviewer_name=control.reviewer,
                raw_rcm_row=control.raw_rcm_row,
                parsed_at=control.parsed_at,
                parser_version=control.parser_version,
                needs_human_review=control.needs_human_review,
                review_reasons=control.review_reasons,
                source_file=source_file,
            ))
        return "created"

    if _controls_differ(existing, control.description,
                        str(control.frequency), str(control.risk_level), control.name):
        if not dry_run:
            existing.name = control.name
            existing.description = control.description
            existing.frequency = str(control.frequency)
            existing.risk_level = str(control.risk_level)
            existing.control_type = str(control.control_type)
            existing.nature = str(control.nature)
            existing.assertions = assertions
            existing.attributes = attributes
            existing.needs_human_review = control.needs_human_review
            existing.review_reasons = control.review_reasons
            existing.raw_rcm_row = control.raw_rcm_row
            existing.updated_at = datetime.now(timezone.utc)
            existing.parser_version = PARSER_VERSION
            existing.source_file = source_file
        return "updated"

    return "unchanged"


def _sync_matrix_file(path: Path, db, anthropic: Anthropic, dry_run: bool) -> dict:
    print(f"  Parsing {path.name} ...")
    try:
        raw_rows = parse_rcm_excel(path)
    except Exception as exc:
        print(f"    ✗ Parse error: {exc}")
        return {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    if not raw_rows:
        print("    ⚠ No data rows found")
        return {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}

    print(f"    {len(raw_rows)} rows — normalizing via Haiku ...")
    created = updated = unchanged = skipped = 0
    batch_cache: dict = {}

    for i, raw_row in enumerate(raw_rows, 1):
        try:
            control = normalize_row(raw_row, anthropic, _cache=batch_cache)
        except Exception as exc:
            print(f"    ✗ Row {i}: {exc}")
            skipped += 1
            continue

        result = _upsert_control(db, control, str(path), dry_run)
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        else:
            unchanged += 1

    if not dry_run:
        db.commit()

    return {"created": created, "updated": updated, "unchanged": unchanged, "skipped": skipped}


def run_matrices_sync(folder: Path, db, anthropic: Anthropic, dry_run: bool, force: bool) -> None:
    files = sorted(folder.glob("**/*.xlsx")) + sorted(folder.glob("**/*.xlsm"))
    if not files:
        print(f"  No .xlsx/.xlsm files found in {folder}")
        return

    print(f"\n{'[DRY RUN] ' if dry_run else ''}MATRICES — {len(files)} file(s) in:\n  {folder}\n")
    total = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0}
    processed = skipped_files = 0

    for path in files:
        file_hash = _sha256(path)
        rec = db.query(MatrixSyncModel).filter(MatrixSyncModel.file_path == str(path)).first()

        if rec and rec.file_hash == file_hash and not force:
            print(f"  ─ {path.name} (unchanged)")
            skipped_files += 1
            continue

        stats = _sync_matrix_file(path, db, anthropic, dry_run)
        processed += 1
        for k in total:
            total[k] += stats.get(k, 0)

        print(f"    ✓ created={stats.get('created',0)}  updated={stats.get('updated',0)}  "
              f"unchanged={stats.get('unchanged',0)}  skipped={stats.get('skipped',0)}")

        if not dry_run:
            if rec:
                rec.file_hash = file_hash
                rec.controls_created = stats.get("created", 0)
                rec.controls_updated = stats.get("updated", 0)
                rec.controls_unchanged = stats.get("unchanged", 0)
                rec.last_synced_at = datetime.now(timezone.utc)
            else:
                db.add(MatrixSyncModel(
                    file_path=str(path),
                    file_name=path.name,
                    file_hash=file_hash,
                    controls_created=stats.get("created", 0),
                    controls_updated=stats.get("updated", 0),
                    controls_unchanged=stats.get("unchanged", 0),
                    last_synced_at=datetime.now(timezone.utc),
                ))
            db.commit()

    print(f"\n  Matrices summary: processed={processed} skipped={skipped_files} | "
          f"created={total['created']} updated={total['updated']} "
          f"unchanged={total['unchanged']} errors={total['skipped']}")


# ─── Testing documentation sync ───────────────────────────────────────────────


def _sync_testing_file(
    path: Path, control_code: str, entity: str, db, anthropic: Anthropic, dry_run: bool
) -> str:
    """
    Ingest one testing documentation file as a knowledge entry.
    Returns 'created' | 'cached' | 'skipped'.
    """
    file_hash = _sha256(path)

    # Cache check: same file+control+period already ingested
    existing = (
        db.query(KnowledgeEntryModel)
        .filter(
            KnowledgeEntryModel.file_hash == file_hash,
            KnowledgeEntryModel.control_code == control_code,
            KnowledgeEntryModel.period == PERIOD,
        )
        .first()
    )
    if existing:
        return "cached"

    try:
        sections = parse_workpaper(path)
    except Exception as exc:
        print(f"      ✗ Parse error: {exc}")
        return "skipped"

    summary: dict = {}
    try:
        summary = summarize_workpaper(sections, control_code, anthropic)
    except Exception:
        summary = {"error": "summarization failed"}

    lean_sections = [{"name": s["name"], "text": s["text"]} for s in sections]

    if not dry_run:
        db.add(KnowledgeEntryModel(
            control_code=control_code,
            period=PERIOD,
            source_filename=path.name,
            file_hash=file_hash,
            file_format=path.suffix.lstrip(".").lower(),
            raw_sections=lean_sections,
            summary=summary,
            ingested_at=datetime.now(timezone.utc),
        ))
        db.commit()

    return "created"


def run_testing_sync(folder: Path, db, anthropic: Anthropic, dry_run: bool, force: bool) -> None:
    """
    Walk PH1 Testing / {Entity} / {ProcessCode- ControlCode} / {files}
    and ingest each testing file as a knowledge entry.
    """
    print(f"\n{'[DRY RUN] ' if dry_run else ''}TESTING DOCS — scanning:\n  {folder}\n")

    created = cached = skipped = unknown = 0

    # Level 1: entity folders (Perion, Hive, UT, Vidazoo)
    for entity_dir in sorted(folder.iterdir()):
        if not entity_dir.is_dir():
            continue
        entity = _map_entity(entity_dir.name)
        if not entity:
            print(f"  ⚠ Unknown entity folder: {entity_dir.name!r} — skipping")
            unknown += 1
            continue

        # Level 2: control folders (e.g. "EX- EX-1")
        for control_dir in sorted(entity_dir.iterdir()):
            if not control_dir.is_dir():
                continue
            control_code = _extract_control_code(control_dir.name)
            if not control_code:
                print(f"  ⚠ Cannot extract control code from {control_dir.name!r} — skipping")
                unknown += 1
                continue

            # Level 3: the actual testing files
            files = [
                f for f in control_dir.iterdir()
                if f.is_file() and f.suffix.lower() in TESTING_EXTENSIONS
            ]
            if not files:
                continue

            print(f"  {entity_dir.name}/{control_dir.name} ({control_code}) — {len(files)} file(s)")

            for path in sorted(files):
                if force:
                    # Delete existing entries for this file so it gets re-ingested
                    file_hash = _sha256(path)
                    db.query(KnowledgeEntryModel).filter(
                        KnowledgeEntryModel.file_hash == file_hash,
                        KnowledgeEntryModel.control_code == control_code,
                        KnowledgeEntryModel.period == PERIOD,
                    ).delete()
                    db.commit()

                result = _sync_testing_file(path, control_code, entity, db, anthropic, dry_run)
                print(f"    {path.name}: {result}")
                if result == "created":
                    created += 1
                elif result == "cached":
                    cached += 1
                else:
                    skipped += 1

    print(f"\n  Testing docs summary: created={created} cached={cached} "
          f"skipped={skipped} unknown_folders={unknown}")


# ─── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Perion SOX data into SOX Sentinel")
    parser.add_argument("--matrices-folder", type=Path, default=DEFAULT_MATRICES)
    parser.add_argument("--testing-folder", type=Path, default=DEFAULT_TESTING)
    parser.add_argument("--matrices-only", action="store_true")
    parser.add_argument("--testing-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
    parser.add_argument("--force", action="store_true", help="Reprocess all files even if unchanged")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("✗ ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    engine = create_engine(settings.database_url)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    anthropic = Anthropic(api_key=settings.anthropic_api_key)

    do_matrices = not args.testing_only
    do_testing  = not args.matrices_only

    if do_matrices:
        if not args.matrices_folder.exists():
            print(f"✗ Matrices folder not found: {args.matrices_folder}")
        else:
            run_matrices_sync(args.matrices_folder, db, anthropic, args.dry_run, args.force)

    if do_testing:
        if not args.testing_folder.exists():
            print(f"✗ Testing folder not found: {args.testing_folder}")
        else:
            run_testing_sync(args.testing_folder, db, anthropic, args.dry_run, args.force)

    db.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
