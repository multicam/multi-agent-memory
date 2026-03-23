"""LLM-based fact extraction from memory content."""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import anthropic

log = logging.getLogger("agent-memory")

EXTRACTION_PROMPT = """Extract structured facts from the following text. Return valid JSON only.

Text:
{text}

Return a JSON object with these fields:
- "facts": list of concise factual statements extracted (strings)
- "entities": list of objects with "name" (string) and "type" (one of: person, organization, tool, service, infrastructure, concept)
- "tags": list of topic tags (lowercase, short)
- "shareable": boolean — true if this is general infrastructure/domain knowledge useful to other agents, false if it's session-specific or in-progress work

If the text contains no extractable facts, return empty lists and shareable=false.
JSON only, no markdown fences:"""


@dataclass
class Extraction:
    facts: list[str] = field(default_factory=list)
    entities: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    shareable: bool = False
    model: str = ""
    extracted_at: str = ""
    status: str = "success"

    def to_dict(self) -> dict:
        return {
            "facts": self.facts,
            "entities": self.entities,
            "tags": self.tags,
            "shareable": self.shareable,
            "model": self.model,
            "extracted_at": self.extracted_at,
            "status": self.status,
        }


class FactExtractor:
    def __init__(self, api_key: str | None = None, ollama_base_url: str | None = None):
        self._client: anthropic.Anthropic | None = None
        self._ollama_base_url = ollama_base_url
        if api_key:
            self._client = anthropic.Anthropic(api_key=api_key)

    def extract(self, text: str) -> Extraction:
        """Extract facts from text. Tries Haiku first, falls back to Ollama."""
        now = datetime.now(timezone.utc).isoformat()

        # Try Haiku
        if self._client:
            try:
                return self._extract_haiku(text, now)
            except Exception as e:
                log.warning(f"Haiku extraction failed: {e}")

        # Fallback to Ollama
        if self._ollama_base_url:
            try:
                return self._extract_ollama(text, now)
            except Exception as e:
                log.warning(f"Ollama extraction failed: {e}")

        # Both failed
        log.warning("All extraction backends failed, skipping extraction")
        return Extraction(status="skipped", extracted_at=now)

    def _extract_haiku(self, text: str, now: str) -> Extraction:
        prompt = EXTRACTION_PROMPT.format(text=text)
        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        data = self._parse_json(raw)

        return Extraction(
            facts=data.get("facts", []),
            entities=data.get("entities", []),
            tags=data.get("tags", []),
            shareable=data.get("shareable", False),
            model="claude-haiku-4-5-20251001",
            extracted_at=now,
            status="success",
        )

    def _extract_ollama(self, text: str, now: str) -> Extraction:
        import httpx

        prompt = EXTRACTION_PROMPT.format(text=text)
        response = httpx.post(
            f"{self._ollama_base_url}/api/generate",
            json={"model": "llama3", "prompt": prompt, "stream": False},
            timeout=30.0,
        )
        response.raise_for_status()
        raw = response.json()["response"].strip()
        data = self._parse_json(raw)

        return Extraction(
            facts=data.get("facts", []),
            entities=data.get("entities", []),
            tags=data.get("tags", []),
            shareable=data.get("shareable", False),
            model="ollama/llama3",
            extracted_at=now,
            status="fallback",
        )

    def _parse_json(self, raw: str) -> dict:
        """Parse JSON, stripping markdown fences if present."""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning(f"Failed to parse extraction JSON: {raw[:200]}")
            return {}
