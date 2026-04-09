#!/usr/bin/env python3
"""Batch LLM curation of private memories for missed promotions.

Reviews recent private (non-shared) memories and asks a cheap LLM
whether any should be promoted to the shared namespace.
"""

import argparse
import json
import sys

import anthropic

from src.config import Config
from src.storage.jsonl import JSONLStorage
from src.storage.postgres import PGStorage

CURATION_PROMPT = """Review these private agent memories. For each, decide if it contains
infrastructure knowledge, domain facts, tool commands, or error resolutions that would be
useful to OTHER agents working on different tasks.

Memories:
{memories}

Return a JSON array of memory IDs that should be promoted to shared.
If none should be promoted, return an empty array.
JSON only, no markdown fences:"""


def main():
    parser = argparse.ArgumentParser(description="Curate private memories for promotion")
    parser.add_argument("--limit", type=int, default=50, help="Max private memories to review")
    parser.add_argument("--dry-run", action="store_true", help="Show candidates without promoting")
    args = parser.parse_args()

    config = Config.from_env()
    if not config.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY required for curation")
        sys.exit(1)

    pg = PGStorage(config.pg_url)
    pg.connect()

    # Fetch recent private memories
    with pg._get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, agent_id, content, created_at
            FROM memories
            WHERE shared = FALSE
              AND memory_type = 'episodic'
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (args.limit,),
        ).fetchall()

    if not rows:
        print("No private memories to review")
        pg.close()
        return

    reviewed_ids = {str(r["id"]) for r in rows}
    print(f"Reviewing {len(rows)} private memories...")

    # Build prompt
    memories_text = "\n".join(
        f"ID: {r['id']} | Agent: {r['agent_id']} | {r['content'][:200]}"
        for r in rows
    )

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": CURATION_PROMPT.format(memories=memories_text)}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        promote_ids = json.loads(raw)
    except json.JSONDecodeError:
        print(f"Failed to parse LLM response: {raw[:200]}")
        pg.close()
        return

    if not isinstance(promote_ids, list) or not all(isinstance(x, str) for x in promote_ids):
        print(f"LLM returned invalid format (expected list of strings): {raw[:200]}")
        pg.close()
        return

    # Validate: only promote IDs that were actually in the reviewed batch
    valid_ids = [pid for pid in promote_ids if pid in reviewed_ids]
    rejected = len(promote_ids) - len(valid_ids)
    if rejected:
        print(f"Rejected {rejected} IDs not in reviewed batch (LLM hallucination)")

    if not valid_ids:
        print("No memories recommended for promotion")
        pg.close()
        return

    print(f"LLM recommends promoting {len(valid_ids)} memories")

    if args.dry_run:
        for pid in valid_ids:
            matching = [r for r in rows if str(r["id"]) == pid]
            if matching:
                print(f"  {pid}: {matching[0]['content'][:100]}")
        print("Dry run — no changes made")
        pg.close()
        return

    # Promote with per-record transactions (Diderot pattern: PG commit only after JSONL succeeds)
    jsonl = JSONLStorage(config.nas_path)
    promoted = 0
    skipped_jsonl = 0

    with pg._get_conn() as conn:
        for pid in valid_ids:
            try:
                with conn.transaction():
                    result = conn.execute(
                        """
                        UPDATE memories
                        SET shared = TRUE, shared_by = agent_id, updated_at = NOW()
                        WHERE id = %s AND shared = FALSE
                        RETURNING id, agent_id, content, source_session, provenance, created_at
                        """,
                        (pid,),
                    ).fetchone()

                    if result is None:
                        continue

                    # Write promoted record to shared JSONL — if this fails,
                    # the transaction rolls back so PG stays consistent with JSONL
                    record = {
                        "id": str(result["id"]),
                        "agent_id": result["agent_id"],
                        "timestamp": result["created_at"].isoformat() if result["created_at"] else "",
                        "type": "episodic",
                        "content": result["content"],
                        "session_id": result["source_session"] or "curated",
                        "metadata": {},
                        "extraction": result["provenance"] or {},
                        "promoted": True,
                    }
                    jsonl.append_shared(record=record, session_id=result["source_session"] or "curated")
                    promoted += 1
            except OSError as e:
                print(f"  WARNING: JSONL write failed for {pid}, PG rolled back: {e}")
                skipped_jsonl += 1

    pg.close()

    print(f"Promoted:      {promoted}")
    print(f"JSONL skipped: {skipped_jsonl}")
    print(f"Skipped:       {len(valid_ids) - promoted - skipped_jsonl}")


if __name__ == "__main__":
    main()
