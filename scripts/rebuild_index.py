#!/usr/bin/env python3
"""Rebuild PostgreSQL index from JSONL source of truth.

Reads all JSONL records from NAS and inserts into PG.
Skips records that already exist (ON CONFLICT DO NOTHING).
Generates embeddings for each record (JSONL doesn't store them).
Does NOT re-extract facts — uses stored extraction data as-is.
"""

import argparse
import sys
from datetime import datetime

from src.config import Config
from src.embeddings import Embedder
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage


def main():
    parser = argparse.ArgumentParser(description="Rebuild PG index from JSONL files")
    parser.add_argument("--dry-run", action="store_true", help="Count records without inserting")
    parser.add_argument("--no-embeddings", action="store_true", help="Skip embedding generation (faster)")
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

    embedder = None
    if not args.no_embeddings:
        embedder = Embedder()
        embedder.load()
        print(f"Embedding model loaded: {embedder.model_name}")

    pg.connect()
    before = pg.count()
    errors = 0

    for i, r in enumerate(records):
        try:
            created_at = datetime.fromisoformat(r["timestamp"])
            embedding = None
            if embedder:
                embedding = embedder.embed(r["content"])

            pg.store(
                memory_id=r["id"],
                text=r["content"],
                agent_id=r["agent_id"],
                session_id=r.get("session_id", "unknown"),
                created_at=created_at,
                memory_type=r.get("type", "episodic"),
                embedding=embedding,
            )

            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(records)} processed...")

        except Exception as e:
            if "duplicate" not in str(e).lower():
                print(f"  ERROR on {r.get('id', '?')}: {e}")
                errors += 1

    after = pg.count()
    pg.close()

    print(f"Processed: {len(records)}")
    print(f"New rows:  {after - before}")
    print(f"Errors:    {errors}")


if __name__ == "__main__":
    main()
