# Connecting AI tools

Medical Doctor MCP uses standard MCP, so it works with any MCP client.

- **Remote clients** connect over **Streamable HTTP** at `https://<your-host>/mcp`.
- **Local clients** connect over **stdio** by running `medical-doctor-mcp`.

There are two ways to authenticate:

| Method | Best for | How the client gets in |
|---|---|---|
| **OAuth sign-in** (Google, GitHub, Microsoft, or your SSO) | People using chat apps and IDEs | The client finds the server's sign-in settings on its own, registers itself (Dynamic Client Registration), and opens a browser to sign in. You only paste the URL. |
| **API key** | Agents, scripts, backends, and clients without OAuth | Send `Authorization: Bearer <key>`. Generate a key with `medical-doctor-mcp gen-key <label>`. |

> **Remote apps need a public HTTPS URL.** ChatGPT and Claude.ai connect from their own servers, so `localhost` won't work for them. Deploy the server (see the README), or for a quick test run `cloudflared tunnel --url http://localhost:8000` and set `MEDICAL_DOCTOR_PUBLIC_URL` to the tunnel URL.

Client menus change often. If a label below doesn't match what you see, look for "custom connector", "MCP server", or "remote MCP" in that app's settings.

---

## Chat apps

### ChatGPT (web/desktop)

1. Turn on **Developer mode** under *Settings → Apps & Connectors → Advanced settings*. On Business, Enterprise, or Edu plans, a workspace admin may need to allow custom connectors first.
2. Choose **Create** (Add custom connector), name it "Medical Doctor", set the MCP server URL to `https://<your-host>/mcp`, and pick **Authentication: OAuth**.
3. ChatGPT registers itself and opens the sign-in page. Pick Google, GitHub, or another provider, and allow access.
4. In a chat, enable the connector from the **+ / tools** menu.

Every tool is marked read-only, so ChatGPT won't ask for write-action confirmation. The server also provides the `search` and `fetch` tools that ChatGPT's connectors and deep research look for.

If consults time out in ChatGPT, set `MEDICAL_DOCTOR_EFFORT=medium`. The server sends progress updates during long consults, but some chat apps still enforce a fixed time limit.

### Claude (claude.ai, Claude Desktop, mobile)

Go to *Settings → Connectors → Add custom connector*, paste `https://<your-host>/mcp`, then choose **Connect** and sign in. Organization admins can add it once for everyone.

### Microsoft Copilot Studio

Add a tool, choose **Model Context Protocol**, and set the server URL to `https://<your-host>/mcp` with the Streamable transport. For authentication, choose OAuth 2.0 with dynamic discovery, or API key with a Bearer header.

---

## Coding agents and IDEs

### Claude Code
```bash
claude mcp add --transport http medical-doctor https://<your-host>/mcp
# then run /mcp inside Claude Code and choose "Authenticate"
# with an API key instead:
claude mcp add --transport http medical-doctor https://<your-host>/mcp --header "Authorization: Bearer $MEDICAL_DOCTOR_TOKEN"
```

### OpenAI Codex CLI (`~/.codex/config.toml`)
```toml
[mcp_servers.medical-doctor]
url = "https://<your-host>/mcp"
# OAuth: run `codex mcp login medical-doctor`, or use an API key:
bearer_token_env_var = "MEDICAL_DOCTOR_TOKEN"
```

### Gemini CLI (`~/.gemini/settings.json`)
```json
{
  "mcpServers": {
    "medical-doctor": {
      "httpUrl": "https://<your-host>/mcp",
      "headers": { "Authorization": "Bearer ${MEDICAL_DOCTOR_TOKEN}" }
    }
  }
}
```
For OAuth, leave out `headers` and run `/mcp auth medical-doctor`.

### Cursor (`~/.cursor/mcp.json`)
```json
{ "mcpServers": { "medical-doctor": { "url": "https://<your-host>/mcp" } } }
```
Cursor runs the OAuth sign-in when it first connects. To use an API key instead, add `"headers": { "Authorization": "Bearer <key>" }`.

### VS Code / GitHub Copilot (`.vscode/mcp.json`)
```json
{ "servers": { "medical-doctor": { "type": "http", "url": "https://<your-host>/mcp" } } }
```

### Windsurf (`~/.codeium/windsurf/mcp_config.json`)
```json
{ "mcpServers": { "medical-doctor": { "serverUrl": "https://<your-host>/mcp" } } }
```

### OpenClaw
Add it as a saved MCP server with `openclaw mcp add`, or put an entry in your config. Use the URL, plus an API key header if you aren't using OAuth. See [`examples/openclaw-mcp.json`](../examples/openclaw-mcp.json).

### Clients that only support stdio
Bridge them to the remote server with [`mcp-remote`](https://www.npmjs.com/package/mcp-remote), which also handles the OAuth sign-in:
```json
{ "mcpServers": { "medical-doctor": { "command": "npx", "args": ["-y", "mcp-remote", "https://<your-host>/mcp"] } } }
```
You can also run the server locally over stdio with `medical-doctor-mcp`, which needs no auth.

---

## Using it from code

### OpenAI Responses API
```python
from openai import OpenAI

resp = OpenAI().responses.create(
    model="gpt-5",
    tools=[{
        "type": "mcp",
        "server_label": "medical_doctor",
        "server_url": "https://<your-host>/mcp",
        "authorization": MEDICAL_DOCTOR_TOKEN,      # API key or OAuth access token
        "require_approval": "never",
    }],
    input="3-year-old, fever 39.4 °C for 5 days, red eyes, cracked lips. What should the parents do?",
)
```

### OpenAI Agents SDK
```python
from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp

async with MCPServerStreamableHttp(params={
    "url": "https://<your-host>/mcp",
    "headers": {"Authorization": f"Bearer {MEDICAL_DOCTOR_TOKEN}"},
}) as doctor:
    agent = Agent(name="Triage", instructions="Use the medical-doctor tools.", mcp_servers=[doctor])
    print((await Runner.run(agent, "Interpret Na 128, glucose 620")).final_output)
```

### Anthropic Messages API (MCP connector)
```python
import anthropic

resp = anthropic.Anthropic().beta.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    betas=["mcp-client-2025-11-20"],
    mcp_servers=[{"type": "url", "url": "https://<your-host>/mcp", "name": "medical-doctor",
                  "authorization_token": MEDICAL_DOCTOR_TOKEN}],
    tools=[{"type": "mcp_toolset", "mcp_server_name": "medical-doctor"}],
    messages=[{"role": "user", "content": "CHA2DS2-VASc for a 72-year-old woman with hypertension?"}],
)
```

### LangChain / LangGraph
```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({"medical-doctor": {
    "transport": "streamable_http",
    "url": "https://<your-host>/mcp",
    "headers": {"Authorization": f"Bearer {MEDICAL_DOCTOR_TOKEN}"},
}})
tools = await client.get_tools()
```

### n8n, Zapier, Make, and other automation tools
Use the platform's **MCP Client** node or step. Set the transport to HTTP Streamable, the URL to `https://<your-host>/mcp`, and authentication to a Bearer token with an API key.

---

## Checking a connection

- Call the **`whoami`** tool. It returns the signed-in account and client.
- `GET https://<your-host>/healthz` returns `{"status":"ok"}`.
- For a visual check, run `npx @modelcontextprotocol/inspector`, connect to the URL, and walk through the OAuth flow.
