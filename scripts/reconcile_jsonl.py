#!/usr/bin/env python3
"""Reconcile PG episodic records against JSONL source of truth.

Reports IDs present in PG but missing from JSONL (write failures or
curation-only promotions) and IDs in JSONL but missing from PG
(rebuild needed).
"""

import sys

from src.config import Config
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage


def main():
    config = Config.from_env()

    # Allow --nas-path override (useful on workstation where NAS is a subfolder, not a mount point)
    nas_path = config.nas_path
    for i, arg in enumerate(sys.argv):
        if arg == "--nas-path" and i + 1 < len(sys.argv):
            nas_path = sys.argv[i + 1]

    jsonl = JSONLStorage(nas_path)
    pg = PGStorage(config.pg_url)

    agents_dir = jsonl._nas_path / "agents"
    if not agents_dir.exists():
        print(f"ERROR: NAS path not accessible at {nas_path}/agents/")
        sys.exit(1)

    # Read all JSONL IDs
    records = jsonl.read_all()
    jsonl_ids = {r["id"] for r in records}
    print(f"JSONL records: {len(jsonl_ids)}")

    # Also check shared JSONL
    shared_dir = jsonl._nas_path / "shared" / "episodic"
    shared_ids = set()
    if shared_dir.exists():
        import json

        for jsonl_file in sorted(shared_dir.glob("*.jsonl")):
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    r = json.loads(line)
                    shared_ids.add(r["id"])
    print(f"Shared JSONL:  {len(shared_ids)}")

    # Read all PG episodic IDs
    pg.connect()
    rows = pg._conn.execute(
        "SELECT id, shared FROM memories WHERE memory_type = 'episodic'"
    ).fetchall()
    pg_ids = {str(r["id"]) for r in rows}
    pg_shared_ids = {str(r["id"]) for r in rows if r["shared"]}
    pg.close()
    print(f"PG episodic:   {len(pg_ids)}")
    print(f"PG shared:     {len(pg_shared_ids)}")

    # Discrepancies
    all_jsonl = jsonl_ids | shared_ids
    in_pg_not_jsonl = pg_ids - all_jsonl
    in_jsonl_not_pg = all_jsonl - pg_ids
    shared_in_pg_not_jsonl = pg_shared_ids - shared_ids

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

    # Summary
    if not in_pg_not_jsonl and not in_jsonl_not_pg and not shared_in_pg_not_jsonl:
        print("\nDiderot pattern: HEALTHY — PG and JSONL are in sync")
    else:
        print("\nDiderot pattern: DIVERGED — see above for details")


if __name__ == "__main__":
    main()
