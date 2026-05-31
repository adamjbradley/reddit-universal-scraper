# Claude Desktop Connector — reddit-scraper MCP

A portable bundle to connect **Claude Desktop** to this project's MCP server.

The MCP server speaks **streamable-HTTP with a static Bearer token**, which Claude
Desktop's native "Custom Connector" UI can't supply. This bundle bridges that gap
using [`mcp-remote`](https://www.npmjs.com/package/mcp-remote): Claude Desktop
launches a tiny stdio process that forwards to the HTTP endpoint and injects the
`Authorization` header.

```
Claude Desktop  --stdio-->  npx mcp-remote  --HTTP+Bearer-->  MCP server (:8765/mcp)
```

## Contents

| File | Purpose |
|------|---------|
| `install.ps1` | Merges the connector into `%APPDATA%\Claude\claude_desktop_config.json` (preserves existing connectors, backs up the old file). |
| `claude_desktop_config.template.json` | The raw config, for manual installation. |
| `README.md` | This file. |

## Prerequisites (on the Claude Desktop machine)

1. **Claude Desktop** installed (creates `%APPDATA%\Claude\` on first launch — the
   script will create it if missing).
2. **Node.js LTS** on `PATH` — `mcp-remote` runs via `npx`. Get it from
   <https://nodejs.org>.
3. The **MCP server running and reachable** from this machine (see below).

## Step 1 — Run the MCP server somewhere

On the host that will run the server (can be the same machine or a remote one):

```powershell
# Docker (compose service is pre-wired on port 8765):
#   set MCP_AUTH_TOKEN in .env first
docker compose up -d mcp

# …or directly:
$env:MCP_AUTH_TOKEN = "your-secret-token"
python main.py --mcp
```

Verify (health check is unauthenticated):

```powershell
curl http://localhost:8765/healthz   # -> {"status":"ok"}
```

> **Remote host:** make sure port `8765` is open in the firewall and bound on a
> reachable interface. The server already listens on `0.0.0.0` by default. Note the
> machine's IP/hostname — you'll pass it as `-ServerUrl` below.

## Step 2 — Install the connector

Copy this `claude-desktop-connector` folder to the Claude Desktop machine, then in
PowerShell:

```powershell
# Server on the same machine:
.\install.ps1 -Token your-secret-token

# Server on a remote host:
.\install.ps1 -ServerUrl http://10.0.0.5:8765/mcp -Token your-secret-token
```

- `-Token` must match the server's `MCP_AUTH_TOKEN`. If omitted, the script reads it
  from the repo `.env` (when present), otherwise it prompts.
- The `/mcp` path is required in the URL.

If PowerShell blocks the script:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Token your-secret-token
```

## Step 3 — Restart Claude Desktop

**Fully quit** Claude Desktop (system tray → Quit — closing the window isn't enough),
then relaunch. The `reddit-scraper` tools should appear in the tools/connectors menu.

## Config file location

The script auto-detects which build you have:

| Build | Config path |
|---|---|
| **Microsoft Store / MSIX** | `%LOCALAPPDATA%\Packages\Claude_<hash>\LocalCache\Roaming\Claude\claude_desktop_config.json` |
| **Standalone installer** | `%APPDATA%\Claude\claude_desktop_config.json` |

The Store build's AppData is *virtualized*, so writing to the plain `%APPDATA%\Claude`
path has no effect — use the script, or the `-ConfigPath` override for an unusual setup.

## Manual install (no script)

1. Open (or create) the config file at the path for your build (see table above).
2. Copy the `reddit-scraper` block from `claude_desktop_config.template.json` into the
   `mcpServers` object, merging with any existing entries.
3. Replace `__SERVER_URL__` with the endpoint (e.g. `http://10.0.0.5:8765/mcp`) and
   `__MCP_TOKEN__` with the token.
4. Fully quit and relaunch Claude Desktop.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Connector missing after restart | Didn't *fully* quit Claude Desktop; or invalid JSON (run `Get-Content $env:APPDATA\Claude\claude_desktop_config.json \| ConvertFrom-Json`). |
| `401 unauthorized` | Token doesn't match the server's `MCP_AUTH_TOKEN`. |
| Connection refused / timeout | Server not running, wrong host/port, or firewall blocking `8765`. Test with `curl http://<host>:8765/healthz`. |
| `npx` / command not found | Node.js not installed or not on `PATH`. |

## Security notes

- The Bearer token is stored in plaintext in `claude_desktop_config.json`. Treat that
  file as a secret. The installer writes a `.bak` copy of any prior config.
- Over an untrusted network, put the server behind **HTTPS** (a reverse proxy) so the
  Bearer token isn't sent in cleartext — use an `https://…/mcp` URL in that case.
- This controls *who can call the tools*. It does **not** defend against prompt
  injection in scraped Reddit content surfaced through the tools — that's a separate
  concern handled in the tool layer.
