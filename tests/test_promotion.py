"""Tests for memory promotion rules."""

from src.extraction.facts import Extraction
from src.extraction.promotion import should_promote


def _extraction(shareable: bool = False, tags: list[str] | None = None) -> Extraction:
    return Extraction(
        facts=["test fact"],
        entities=[],
        tags=tags or [],
        shareable=shareable,
        model="test",
        extracted_at="2026-01-01",
    )


def test_llm_says_shareable_no_conflicting_tags():
    """LLM says shareable + no private tags → promote."""
    assert should_promote(_extraction(shareable=True, tags=["infrastructure"]))


def test_llm_says_shareable_with_private_tags():
    """LLM says shareable but private tags → do NOT promote."""
    assert not should_promote(_extraction(shareable=True, tags=["in-progress", "debugging"]))


def test_llm_says_private_but_shareable_tags():
    """LLM says not shareable but infrastructure tags → promote anyway."""
    assert should_promote(_extraction(shareable=False, tags=["infrastructure", "deployment"]))


def test_llm_says_private_no_shareable_tags():
    """LLM says not shareable + no shareable tags → do NOT promote."""
    assert not should_promote(_extraction(shareable=False, tags=["personal", "random"]))


def test_empty_tags_not_shareable():
    """Empty tags + not shareable → do NOT promote."""
    assert not should_promote(_extraction(shareable=False, tags=[]))


def test_empty_tags_shareable():
    """Empty tags + shareable → promote (no private tags to block)."""
    assert should_promote(_extraction(shareable=True, tags=[]))


def test_shareable_tags_comprehensive():
    """All shareable tag categories trigger promotion."""
    shareable_tags = [
        "infrastructure", "configuration", "deployment", "networking",
        "database", "server", "api", "tool", "command", "cli",
        "error-resolution", "fix", "solution", "setup", "install",
        "architecture", "design", "convention", "standard",
        "port", "dns", "ssh", "nginx", "postgresql", "docker",
    ]
    for tag in shareable_tags:
        assert should_promote(_extraction(shareable=False, tags=[tag])), f"Tag '{tag}' should trigger promotion"


def test_private_tags_comprehensive():
    """All private tags block promotion even when LLM says shareable."""
    private_tags = ["in-progress", "hypothesis", "attempt", "debugging", "personal", "draft", "temporary", "wip"]
    for tag in private_tags:
        assert not should_promote(_extraction(shareable=True, tags=[tag])), f"Tag '{tag}' should block promotion"


def test_case_insensitive_tags():
    """Tags are compared case-insensitively."""
    assert should_promote(_extraction(shareable=False, tags=["Infrastructure"]))
    assert not should_promote(_extraction(shareable=True, tags=["IN-PROGRESS"]))
