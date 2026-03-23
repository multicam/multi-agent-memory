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

function getConfig(event: HookEvent) {
  const hookEnv =
    event.context.cfg?.hooks?.internal?.entries?.["memory-sync"]?.env || {};
  return {
    apiUrl:
      hookEnv.MEMORY_API_URL ||
      process.env.MEMORY_API_URL ||
      "http://192.168.10.24:8888/mcp",
    agentId: hookEnv.AGENT_ID || process.env.AGENT_ID || "unknown",
  };
}

const handler = async (event: HookEvent) => {
  const { apiUrl, agentId } = getConfig(event);

  // Session end: store conversation summary
  if (
    event.type === "command" &&
    ["new", "reset"].includes(event.action)
  ) {
    const session =
      event.context.previousSessionEntry || event.context.sessionEntry;
    const messages = session?.messages || [];

    if (messages.length === 0) {
      return;
    }

    // Build a summary of the session
    const turns = messages
      .slice(-20) // last 20 messages max
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
