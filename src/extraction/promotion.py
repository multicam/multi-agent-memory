"""Rule-based auto-promotion of memories to the shared namespace."""

import logging

from src.extraction.facts import Extraction

log = logging.getLogger("agent-memory")

# Tags that indicate shareable infrastructure/domain knowledge
SHAREABLE_TAGS = {
    "infrastructure", "configuration", "deployment", "networking",
    "database", "server", "api", "tool", "command", "cli",
    "error-resolution", "fix", "solution", "setup", "install",
    "architecture", "design", "convention", "standard",
    "port", "dns", "ssh", "nginx", "postgresql", "docker",
}

# Tags that indicate private, session-specific content
PRIVATE_TAGS = {
    "in-progress", "hypothesis", "attempt", "debugging",
    "personal", "draft", "temporary", "wip",
}


def should_promote(extraction: Extraction) -> bool:
    """Determine if a memory should be promoted to the shared namespace.

    Uses the LLM's shareable flag as primary signal, with tag-based rules
    as override/confirmation.
    """
    # LLM said shareable — trust it unless tags say otherwise
    if extraction.shareable:
        tags = set(t.lower() for t in extraction.tags)
        if tags & PRIVATE_TAGS:
            log.info(f"LLM said shareable but private tags found: {tags & PRIVATE_TAGS}")
            return False
        return True

    # LLM said not shareable — check if tags suggest otherwise
    tags = set(t.lower() for t in extraction.tags)
    if tags & SHAREABLE_TAGS:
        log.info(f"LLM said private but shareable tags found: {tags & SHAREABLE_TAGS}")
        return True

    return False
