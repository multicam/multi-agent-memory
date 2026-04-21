#!/usr/bin/env python3
"""Reconcile PG episodic + semantic records against JSONL source of truth.

Reports IDs present in PG but missing from JSONL (write failures or
curation-only promotions) and IDs in JSONL but missing from PG
(rebuild needed). Also cross-checks semantic row counts per parent
episodic so a facts/decisions-only PG write failure is visible.
"""

import argparse
import json

from src.config import Config
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage


def main():
    parser = argparse.ArgumentParser(
        description="Reconcile PG index against JSONL source of truth",
    )
    parser.add_argument(
        "--nas-path",
        help="Override NAS path (useful on workstation where NAS is a subfolder, not a mount point)",
    )
    args = parser.parse_args()

    config = Config.from_env()
    nas_path = args.nas_path or config.nas_path

    jsonl = JSONLStorage(nas_path)
    pg = PGStorage(config.pg_url)

    agents_dir = jsonl._nas_path / "agents"
    if not agents_dir.exists():
        print(f"ERROR: NAS path not accessible at {nas_path}/agents/")
        raise SystemExit(1)

    # Read all JSONL records (keep records around for semantic count cross-check)
    records = jsonl.read_all()
    jsonl_ids = {r["id"] for r in records}
    print(f"JSONL records: {len(jsonl_ids)}")

    # Expected semantic count per parent from each episodic JSONL record
    expected_semantic: dict[str, int] = {}
    for r in records:
        ex = r.get("extraction", {}) or {}
        n = len(ex.get("facts", []) or []) + len(ex.get("decisions", []) or [])
        if n:
            expected_semantic[r["id"]] = n
    expected_semantic_total = sum(expected_semantic.values())

    # Also check shared JSONL
    shared_dir = jsonl._nas_path / "shared" / "episodic"
    shared_ids = set()
    if shared_dir.exists():
        for jsonl_file in sorted(shared_dir.glob("*.jsonl")):
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    r = json.loads(line)
                    shared_ids.add(r["id"])
    print(f"Shared JSONL:  {len(shared_ids)}")

    # Read all PG episodic IDs + semantic counts per parent
    pg.connect()
    with pg.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, shared FROM memories WHERE memory_type = 'episodic'"
        ).fetchall()
        sem_rows = conn.execute(
            """
            SELECT provenance->>'source_memory_id' AS parent_id, COUNT(*) AS n
            FROM memories
            WHERE memory_type = 'semantic' AND provenance ? 'source_memory_id'
            GROUP BY parent_id
            """
        ).fetchall()
    pg_ids = {str(r["id"]) for r in rows}
    pg_shared_ids = {str(r["id"]) for r in rows if r["shared"]}
    pg_semantic_counts = {r["parent_id"]: int(r["n"]) for r in sem_rows if r["parent_id"]}
    pg.close()
    print(f"PG episodic:   {len(pg_ids)}")
    print(f"PG shared:     {len(pg_shared_ids)}")
    print(f"PG semantic:   {sum(pg_semantic_counts.values())} rows (expected from JSONL: {expected_semantic_total})")

    # Discrepancies
    all_jsonl = jsonl_ids | shared_ids
    in_pg_not_jsonl = pg_ids - all_jsonl
    in_jsonl_not_pg = all_jsonl - pg_ids
    shared_in_pg_not_jsonl = pg_shared_ids - shared_ids

    # Semantic drift: expected N facts+decisions for a parent, PG has fewer
    semantic_drift = []
    for parent_id, expected_n in expected_semantic.items():
        actual = pg_semantic_counts.get(parent_id, 0)
        if actual != expected_n:
            semantic_drift.append((parent_id, expected_n, actual))

    print(f"\n--- Discrepancies ---")

    print(f"\nEpisodic in PG but missing from JSONL: {len(in_pg_not_jsonl)}")
    for mid in sorted(in_pg_not_jsonl)[:10]:
        print(f"  {mid}")
    if len(in_pg_not_jsonl) > 10:
        print(f"  ... and {len(in_pg_not_jsonl) - 10} more")

    print(f"\nEpisodic in JSONL but missing from PG: {len(in_jsonl_not_pg)}")
    for mid in sorted(in_jsonl_not_pg)[:10]:
        print(f"  {mid}")
    if len(in_jsonl_not_pg) > 10:
        print(f"  ... and {len(in_jsonl_not_pg) - 10} more")

    print(f"\nShared in PG but missing from shared JSONL: {len(shared_in_pg_not_jsonl)}")
    for mid in sorted(shared_in_pg_not_jsonl)[:10]:
        print(f"  {mid}")
    if len(shared_in_pg_not_jsonl) > 10:
        print(f"  ... and {len(shared_in_pg_not_jsonl) - 10} more")

    print(f"\nSemantic drift (expected != PG rows, per parent): {len(semantic_drift)}")
    for parent_id, expected_n, actual in sorted(semantic_drift)[:10]:
        print(f"  {parent_id}: expected {expected_n}, PG has {actual}")
    if len(semantic_drift) > 10:
        print(f"  ... and {len(semantic_drift) - 10} more")

    # Summary
    if (
        not in_pg_not_jsonl
        and not in_jsonl_not_pg
        and not shared_in_pg_not_jsonl
        and not semantic_drift
    ):
        print("\nDiderot pattern: HEALTHY — PG and JSONL are in sync")
    else:
        if semantic_drift:
            print(
                "\nNote: semantic rows are tracked by parent episodic ID; "
                "re-run `rebuild_index.py --no-embeddings` to regenerate missing facts/decisions."
            )
        print("\nDiderot pattern: DIVERGED — see above for details")


if __name__ == "__main__":
    main()
