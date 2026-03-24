/**
 * memory-sync hook for OpenClaw
 *
 * Syncs conversation context to the multi-agent-memory server.
 * - On /new or /reset: stores a session summary
 * - On message:sent: stores each agent response for continuous capture
 */

interface HookEvent {
  type: string;
  action: string;
  sessionKey: string;
  timestamp: Date;
  messages: string[];
  context: {
    sessionEntry?: { messages?: any[] };
    previousSessionEntry?: { messages?: any[] };
    senderId?: string;
    commandSource?: string;
    workspaceDir?: string;
    cfg?: any;
    content?: string;
    from?: string;
    to?: string;
  };
}

async function callMemoryServer(
  apiUrl: string,
  method: string,
  params: Record<string, any>,
): Promise<any> {
  // Initialize MCP session
  const initRes = await fetch(apiUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "openclaw-hook", version: "1.0" },
      },
    }),
  });

  const initText = await initRes.text();
  const sessionId = initRes.headers.get("mcp-session-id") || "";

  // Call the tool
  const toolRes = await fetch(apiUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "Mcp-Session-Id": sessionId,
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 2,
      method: "tools/call",
      params: { name: method, arguments: params },
    }),
  });

  const toolText = await toolRes.text();
  try {
    const dataLine = toolText.split("data: ")[1];
    if (dataLine) {
      return JSON.parse(dataLine);
    }
  } catch {
    // parse error — logged below
  }
  return null;
}

async function getConfig(event: HookEvent) {
  // Try event context first
  let hookEnv =
    event.context.cfg?.hooks?.internal?.entries?.["memory-sync"]?.env;

  // Fallback: read openclaw.json directly (event.context.cfg may not be populated)
  if (!hookEnv?.AGENT_ID) {
    try {
      const { readFileSync } = await import("node:fs");
      const { join } = await import("node:path");
      const home = process.env.HOME || "/home/tgds";
      const config = JSON.parse(
        readFileSync(join(home, ".openclaw", "openclaw.json"), "utf-8"),
      );
      hookEnv =
        config?.hooks?.internal?.entries?.["memory-sync"]?.env || {};
    } catch {
      hookEnv = {};
    }
  }

  return {
    apiUrl:
      hookEnv?.MEMORY_API_URL ||
      process.env.MEMORY_API_URL ||
      "http://192.168.10.24:8888/mcp",
    agentId: hookEnv?.AGENT_ID || process.env.AGENT_ID || "unknown",
  };
}

const handler = async (event: HookEvent) => {
  const { apiUrl, agentId } = await getConfig(event);

  // Session end: store conversation summary
  if (
    event.type === "command" &&
    ["new", "reset"].includes(event.action)
  ) {
    // Store old session summary (if there are messages)
    const session =
      event.context.previousSessionEntry || event.context.sessionEntry;
    const messages = session?.messages || [];

    if (messages.length > 0) {
      const turns = messages
        .slice(-20)
        .map((m: any) => {
          const role = m.role || "unknown";
          const content =
            typeof m.content === "string"
              ? m.content.slice(0, 500)
              : JSON.stringify(m.content).slice(0, 500);
          return `[${role}] ${content}`;
        })
        .join("\n");

      const summary = `Session ended (${event.action}). ${messages.length} turns. Last messages:\n${turns}`;

      try {
        const result = await callMemoryServer(apiUrl, "store_memory", {
          text: summary,
          agent_id: agentId,
          session_id: event.sessionKey,
        });

        const stored = result?.result?.structuredContent;
        if (stored?.id) {
          const promoted = stored.promoted ? " [shared]" : "";
          console.log(
            `[memory-sync] Session stored: ${stored.id}${promoted} (${stored.extraction?.facts || 0} facts)`,
          );
        }
      } catch (error: any) {
        console.error(`[memory-sync] Failed to store session: ${error.message}`);
      }
    }

    // Session-start recall (always runs, even if old session was empty): fetch relevant shared memories for the new session
    try {
      const recalled = await callMemoryServer(apiUrl, "recall", {
        query: "important facts about JM preferences, tools, infrastructure, project conventions, and lessons learned",
        agent_id: agentId,
        limit: 10,
      });

      const memories = recalled?.result?.content?.[0]?.text;
      if (memories) {
        const parsed = JSON.parse(memories);
        if (Array.isArray(parsed) && parsed.length > 0) {
          // Filter to meaningful results (>50% similarity)
          const relevant = parsed.filter((m: any) => !m.similarity || m.similarity > 0.5);
          if (relevant.length === 0) {
            return;
          }

          const { writeFileSync, mkdirSync } = await import("node:fs");
          const { join } = await import("node:path");
          const wsDir =
            event.context.workspaceDir ||
            join(process.env.HOME || "/home/tgds", ".openclaw", "workspace");
          const memDir = join(wsDir, "memory");
          mkdirSync(memDir, { recursive: true });

          const lines = relevant.map((m: any) => {
            const sim = m.similarity ? ` (${(m.similarity * 100).toFixed(0)}%)` : "";
            const src = m.shared_by && m.shared_by !== agentId ? ` — from ${m.shared_by}` : "";
            return `- ${m.content?.slice(0, 200)}${sim}${src}`;
          });

          const md = `# Recalled Context\n\n_Auto-populated on session start. ${relevant.length} memories recalled._\n\n${lines.join("\n")}\n`;
          writeFileSync(join(memDir, "recalled.md"), md, "utf-8");
          console.log(`[memory-sync] Recalled ${relevant.length} memories → memory/recalled.md`);
        }
      }
    } catch (error: any) {
      console.error(`[memory-sync] Recall failed (non-fatal): ${error.message}`);
    }

    return;
  }

  // Message sent: continuous capture of agent responses
  if (event.type === "message" && event.action === "sent") {
    const content = event.context.content;
    if (!content || content.length < 20) {
      return; // skip trivial messages
    }

    try {
      await callMemoryServer(apiUrl, "store_memory", {
        text: content.slice(0, 2000), // cap at 2000 chars
        agent_id: agentId,
        session_id: event.sessionKey,
      });
    } catch (error: any) {
      // Don't spam errors for continuous capture — log once
      console.error(
        `[memory-sync] Capture failed: ${error.message}`,
      );
    }
  }
};

export default handler;
