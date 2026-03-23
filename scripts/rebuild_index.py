#!/usr/bin/env python3
"""Rebuild PostgreSQL index from JSONL source of truth.

Reads all JSONL records from NAS and inserts into PG.
Skips records that already exist (ON CONFLICT DO NOTHING).
Does NOT re-extract facts — uses stored data as-is.
"""

import argparse
import sys
from datetime import datetime, timezone

from src.config import Config
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage


def main():
    parser = argparse.ArgumentParser(description="Rebuild PG index from JSONL files")
    parser.add_argument("--dry-run", action="store_true", help="Count records without inserting")
    args = parser.parse_args()

    config = Config.from_env()
    jsonl = JSONLStorage(config.nas_path)
    pg = PGStorage(config.pg_url)

    if not jsonl.is_mounted():
        print(f"ERROR: NAS not mounted at {config.nas_path}")
        sys.exit(1)

    records = jsonl.read_all()
    print(f"Found {len(records)} JSONL records")

    if args.dry_run:
        print("Dry run — no changes made")
        return

    pg.connect()
    before = pg.count()

    inserted = 0
    skipped = 0
    errors = 0

    for r in records:
        try:
            created_at = datetime.fromisoformat(r["timestamp"])
            pg.store(
                memory_id=r["id"],
                text=r["content"],
                agent_id=r["agent_id"],
                session_id=r.get("session_id", "unknown"),
                created_at=created_at,
                memory_type=r.get("type", "episodic"),
            )
            inserted += 1
        except Exception as e:
            # ON CONFLICT DO NOTHING means duplicates silently skip
            # Real errors get logged
            if "duplicate" not in str(e).lower():
                print(f"  ERROR on {r.get('id', '?')}: {e}")
                errors += 1
            else:
                skipped += 1

    after = pg.count()
    pg.close()

    print(f"Processed: {len(records)}")
    print(f"Inserted:  {after - before}")
    print(f"Skipped:   {skipped} (already in PG)")
    print(f"Errors:    {errors}")


if __name__ == "__main__":
    main()
