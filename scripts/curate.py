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
    rows = pg._conn.execute(
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
        return

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
        return

    if not promote_ids:
        print("No memories recommended for promotion")
        return

    print(f"LLM recommends promoting {len(promote_ids)} memories")

    if args.dry_run:
        for pid in promote_ids:
            matching = [r for r in rows if str(r["id"]) == pid]
            if matching:
                print(f"  {pid}: {matching[0]['content'][:100]}")
        print("Dry run — no changes made")
        return

    # Promote
    promoted = 0
    for pid in promote_ids:
        pg._conn.execute(
            """
            UPDATE memories
            SET shared = TRUE, shared_by = agent_id, updated_at = NOW()
            WHERE id = %s AND shared = FALSE
            """,
            (pid,),
        )
        promoted += 1

    pg._conn.commit()
    pg.close()

    print(f"Promoted: {promoted}")
    print(f"Skipped:  {len(promote_ids) - promoted}")


if __name__ == "__main__":
    main()
