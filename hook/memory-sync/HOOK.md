---
name: memory-sync
description: "Sync session context to the multi-agent-memory server on session transitions"
metadata:
  openclaw:
    emoji: "🧠"
    events: ["command:new", "command:reset", "message:sent"]
---

# Memory Sync Hook

Connects OpenClaw to the multi-agent-memory server. On session transitions (`/new`, `/reset`), saves the conversation to persistent memory with fact extraction. On each agent response (`message:sent`), stores the turn for continuous capture.

## Configuration

Set in `~/.openclaw/openclaw.json`:

```json
{
  "hooks": {
    "internal": {
      "entries": {
        "memory-sync": {
          "enabled": true,
          "env": {
            "MEMORY_API_URL": "http://192.168.10.24:8888/mcp",
            "AGENT_ID": "ag-1"
          }
        }
      }
    }
  }
}
```

- `MEMORY_API_URL` — Memory MCP server endpoint (required)
- `AGENT_ID` — This agent's identifier, e.g. "ag-1" or "ag-2" (required)

## Behavior

- **`command:new` / `command:reset`**: Stores a summary of the ending session's conversation
- **`message:sent`**: Stores each agent response for continuous capture (auto-recall at session start is handled by the agent's system prompt)
