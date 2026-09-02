"""
Matrix sync script — keeps SOX Sentinel in sync with the Perion matrix folder.

Usage:
    python scripts/sync_matrices.py                          # use default folder
    python scripts/sync_matrices.py --folder "G:\\path"      # override folder
    python scripts/sync_matrices.py --dry-run                # preview only, no DB writes
    python scripts/sync_matrices.py --force                  # re-process all files even if unchanged

Default folder: G:\\Shared drives\\SOX\\SOX404\\Periods\\2026\\Matrix\\Matrix by process

What it does:
  1. Scans the folder for all .xlsx / .xlsm files (recursively)
  2. Computes SHA-256 of each file
  3. Skips files whose hash hasn't changed since last sync (free — no LLM cost)
  4. For changed/new files: parses each row with the Excel parser,
     normalizes via Haiku LLM, then upserts controls in the DB
       - New control code+entity  → INSERT
       - Existing but changed     → UPDATE (description, frequency, risk_level, attributes)
       - Existing and identical   → skip (counted as unchanged)
  5. Prints a summary and saves sync metadata to the matrix_syncs table
"""

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from anthropic import Anthropic  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.db.base import Base  # noqa: E402
from backend.db.models import ControlModel, MatrixSyncModel  # noqa: E402
from backend.settings import get_settings  # noqa: E402
from engine.rcm_parser.excel_parser import parse_rcm_excel  # noqa: E402
from engine.rcm_parser.llm_normalizer import PARSER_VERSION, normalize_row  # noqa: E402

DEFAULT_FOLDER = Path(r"G:\Shared drives\SOX\SOX404\Periods\2026\Matrix\Matrix by process")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _controls_differ(existing: ControlModel, new_code: str, new_desc: str,
                     new_freq: str, new_risk: str, new_name: str) -> bool:
    """Return True if any key field changed and an update is warranted."""
    return (
        existing.description != new_desc
        or existing.frequency != new_freq
        or existing.risk_level != new_risk
        or existing.name != new_name
    )


def _upsert_control(db, control, source_file: str, dry_run: bool) -> str:
    """
    Insert or update a control in the DB.
    Returns 'created' | 'updated' | 'unchanged'.
    """
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

    if _controls_differ(existing, control.code, control.description,
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


def sync_file(path: Path, db, anthropic: Anthropic, dry_run: bool) -> dict:
    """Process one matrix file. Returns {created, updated, unchanged, skipped}."""
    print(f"  Parsing {path.name} ...")
    try:
        raw_rows = parse_rcm_excel(path)
    except Exception as exc:
        print(f"    ✗ Parse error: {exc}")
        return {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "error": str(exc)}

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
            print(f"    ✗ Row {i}: normalize error — {exc}")
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


def run_sync(folder: Path, dry_run: bool, force: bool) -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        print("✗ ANTHROPIC_API_KEY not set in .env — cannot normalize controls")
        sys.exit(1)

    engine = create_engine(settings.database_url)
    Base.metadata.create_all(bind=engine)  # create tables if they don't exist yet
    Session = sessionmaker(bind=engine)
    db = Session()
    anthropic = Anthropic(api_key=settings.anthropic_api_key)

    files = sorted(folder.glob("**/*.xlsx")) + sorted(folder.glob("**/*.xlsm"))
    if not files:
        print(f"No .xlsx/.xlsm files found in {folder}")
        return

    print(f"\n{'DRY RUN — ' if dry_run else ''}Scanning {len(files)} file(s) in:\n  {folder}\n")

    total_created = total_updated = total_unchanged = total_skipped = 0
    files_processed = files_skipped = 0

    for path in files:
        file_hash = _sha256(path)

        # Check if this file has changed since last sync
        sync_record = db.query(MatrixSyncModel).filter(MatrixSyncModel.file_path == str(path)).first()
        if sync_record and sync_record.file_hash == file_hash and not force:
            print(f"  ─ {path.name} (unchanged, skipping)")
            files_skipped += 1
            continue

        stats = sync_file(path, db, anthropic, dry_run)
        files_processed += 1
        total_created += stats.get("created", 0)
        total_updated += stats.get("updated", 0)
        total_unchanged += stats.get("unchanged", 0)
        total_skipped += stats.get("skipped", 0)

        print(f"    ✓ created={stats.get('created',0)}  updated={stats.get('updated',0)}  "
              f"unchanged={stats.get('unchanged',0)}  skipped={stats.get('skipped',0)}")

        # Save sync record
        if not dry_run:
            if sync_record:
                sync_record.file_hash = file_hash
                sync_record.controls_created = stats.get("created", 0)
                sync_record.controls_updated = stats.get("updated", 0)
                sync_record.controls_unchanged = stats.get("unchanged", 0)
                sync_record.last_synced_at = datetime.now(timezone.utc)
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

    db.close()

    print(f"""
{'─' * 50}
Sync {'(DRY RUN) ' if dry_run else ''}complete
  Files processed : {files_processed}
  Files skipped   : {files_skipped} (unchanged)
  Controls created: {total_created}
  Controls updated: {total_updated}
  Controls unchanged: {total_unchanged}
  Rows skipped (errors): {total_skipped}
{'─' * 50}
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Perion SOX matrix files into SOX Sentinel")
    parser.add_argument(
        "--folder",
        type=Path,
        default=DEFAULT_FOLDER,
        help=f"Path to matrix folder (default: {DEFAULT_FOLDER})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing to DB")
    parser.add_argument("--force", action="store_true", help="Re-process all files even if unchanged")
    args = parser.parse_args()

    if not args.folder.exists():
        print(f"✗ Folder not found: {args.folder}")
        print("  Check the path or use --folder to override")
        sys.exit(1)

    run_sync(args.folder, dry_run=args.dry_run, force=args.force)
