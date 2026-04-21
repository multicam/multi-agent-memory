#!/usr/bin/env python3
"""Rebuild PostgreSQL index from JSONL source of truth.

Reads all JSONL records from NAS and inserts into PG.
Skips records that already exist (ON CONFLICT DO NOTHING).
Generates embeddings for each record (JSONL doesn't store them).
Uses cached extractions from JSONL (no LLM calls needed).
"""

import argparse
import sys
import uuid
from datetime import datetime, timezone

from src.config import Config
from src.embeddings import Embedder
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage


def _parse_iso_tz(s: str) -> datetime:
    """Parse an ISO-8601 timestamp, tolerating a trailing 'Z' and naive dates.

    Ensures the returned datetime is tz-aware (UTC if the source string had
    no tz), so inserts into TIMESTAMPTZ columns don't raise in psycopg 3.
    2026-04-15 review P2.
    """
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


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
            created_at = _parse_iso_tz(r["timestamp"])

            # Embed the episodic content
            embedding = None
            if embedder:
                embedding = embedder.embed(r["content"])

            # Build provenance from cached extraction
            extraction = r.get("extraction", {})
            provenance = None
            if extraction:
                provenance = {
                    "extraction_model": extraction.get("model", ""),
                    "extraction_status": extraction.get("status", "rebuilt"),
                    "extracted_at": extraction.get("extracted_at", ""),
                }

            # Single atomic write: episodic + facts + decisions in one transaction.
            # Mirrors server.py's store_memory path — avoids partial state where
            # episodic commits but semantic children fail (2026-04-21 P1 fix).
            facts = extraction.get("facts", []) if extraction else []
            decisions = extraction.get("decisions", []) if extraction else []
            all_semantic = facts + decisions
            sem_embeddings = None
            if embedder and all_semantic:
                sem_embeddings = [embedder.embed(s) for s in all_semantic]

            pg.store_with_facts_and_chunks(
                memory_id=r["id"],
                text=r["content"],
                agent_id=r["agent_id"],
                session_id=r.get("session_id", "unknown"),
                created_at=created_at,
                memory_type=r.get("type", "episodic"),
                embedding=embedding,
                provenance=provenance,
                shared=r.get("promoted", False),
                facts=facts or None,
                decisions=decisions or None,
                fact_embeddings=sem_embeddings,
            )

            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(records)} processed...")

        except Exception as e:
            print(f"  ERROR on {r.get('id', '?')}: {e}")
            errors += 1

    after = pg.count()
    pg.close()

    print(f"Processed: {len(records)}")
    print(f"New rows:  {after - before}")
    print(f"Errors:    {errors}")


if __name__ == "__main__":
    main()
