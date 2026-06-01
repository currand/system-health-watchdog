# Service Discovery Catalog

**Purpose:** A reference for the agent to systematically discover what services exist on a machine during Phase 2 (Introspection) of onboarding. Not every machine has every service — the agent probes for each one and only adds probes for what it finds.

**When to load:** During Phase 2 of `skill-onboarding-and-configuration.md` (introspection), or when the user says "check what services I have" or "find all Hermes services on this machine".

**Do NOT load:** During routine failure triage (Context A). This is a setup-time reference only.

---

## Overview

The agent should discover services in dependency order, from most fundamental (OS) to most specific (user-configured MCPs). The rough order:

1. **System layer** — DNS, disk, process supervisor
2. **Hermes core** — gateways, WebUI, memory, cron
3. **Hermes config layer** — MCP servers, plugins, tools
4. **Messaging platforms** — gateway adapters, webhooks
5. **External integrations** — Home Assistant, etc.
6. **Docker containers** — infrastructure containers
7. **Unsupervised processes** — long-running MCPs not managed by a supervisor

---

## 1. System Layer (always present)

These are OS-level services the watchdog should always check, regardless of Hermes installation.

| Service | Probe | Notes |
|---------|-------|-------|
| **DNS resolution** | `host google.com` → "has address" | Works on any OS. If DNS fails, everything breaks. |
| **Disk space** | `df -h /` → `int(out) < 90` | Alert on < 90%. Critical for log writes. |
| **Process supervisor** | `launchctl list` (macOS) or `systemctl` (Linux) | Verify the supervisor itself is running |
| **Network connectivity** | `curl -s -o /dev/null -w "%{http_code}" https://hermes-agent.nousresearch.com` | Tests external reachability |

---

## 2. Hermes Core Services (always present)

Every Hermes installation has these. They're the foundation the agent runs on.

### Gateways

Hermes runs one or more gateway instances. Each is a long-running process that handles messaging, cron, and webhooks.

**How to discover:**
- Launchd (macOS): `launchctl list | grep ai.hermes.gateway`
- Systemd (Linux): `systemctl list-units | grep hermes-gateway`
- Process table: `ps aux | grep -e 'hermes.*gateway' | grep -v grep`
- Config: `cat ~/.hermes/config.yaml | grep -A 5 'gateways:'`

**Default naming convention:**
- Default gateway: `ai.hermes.gateway` (port :8644 typically)
- Profile gateways: `ai.hermes.gateway.<profile>` (e.g., `ai.hermes.gateway.coach` on port :8649)

**References:** `references/probe-design.md` for HTTP probe patterns

### WebUI

Hermes' browser-based chat interface. Not always running if the user only uses CLI/messaging.

**How to discover:**
- Launchd: `launchctl list | grep ai.hermes.webui`
- Process: `ps aux | grep -e 'hermes.*webui' | grep -v grep`
- Port scan: `lsof -iTCP -sTCP:LISTEN -P -n | grep :8787`

**Default port:** :8787

### Hindsight Memory

Persistent memory backend. Can run as an embedded library (no separate process) or as a standalone Docker container.

**How to discover:**
- Docker: `docker ps -a --format '{{.Names}} {{.Status}}' | grep -i hindsight`
- Process: `ps aux | grep -e 'hindsight' | grep -v grep`
- HTTP endpoint: `curl -s http://localhost:8888/health` (if running as standalone)
- Config: `cat ~/.hermes/config.yaml | grep -A 10 'memory:'`

### Cron Scheduler

Hermes' internal cron system. Used for scheduled agent runs, watchdog pre-scanner, and recurring tasks.

**How to discover:**
- `hermes cron list` — shows all registered cron jobs
- Look for pre-existing watchdog, backup, or maintenance jobs

**Always add:** At minimum, probe that `hermes cron list` returns output with "Next run"

---

## 3. Hermes Config Layer (varies per installation)

### MCP Servers

Configured in `~/.hermes/config.yaml` under `mcp_servers:`. Each MCP server is either:
- **Stdio (command-based)** — runs as a local subprocess
- **HTTP (URL-based)** — connects to a remote endpoint via HTTP

**How to discover:**
- Read `~/.hermes/config.yaml` and parse the `mcp_servers:` section
- For each server, determine if it has `command:` (stdio) or `url:` (HTTP)
- For stdio servers: create a `process` probe using `ps aux | grep` matching a unique argument
- For HTTP servers: create an `http` probe hitting the URL (typically `GET /health` or `GET /`)

**Common MCP server patterns to look for:**

| Name | Common pattern | Probe type |
|------|---------------|------------|
| Google Workspace MCP | `python3 ... google-workspaces ...` or URL-based | process + http |
| GitHub MCP | `npx @modelcontextprotocol/server-github` or `npx @mcp/github` | process |
| Filesystem MCP | `npx @modelcontextprotocol/server-filesystem` | process |
| Stripe MCP | URL to `mcp.stripe.com` or command | process + http |
| Linear MCP | URL to `mcp.linear.app` (OAuth) | process + http |
| n8n MCP | URL to local n8n instance | process + http |
| Custom MCPs | Any command or URL in config.yaml | varies |

### Plugins

Hermes plugins live in `~/.hermes/plugins/` or `.hermes/plugins/` (project-local). Each plugin may host additional tools or platform adapters.

**How to discover:**
- `ls ~/.hermes/plugins/ 2>/dev/null`
- Check for `PLUGIN.yaml` files in each plugin directory

### Webhook Subscriptions

Configured via `hermes webhook setup` or in `config.yaml`. Hermes exposes an HTTP endpoint that external services can POST to, triggering agent sessions.

**How to discover:**
- Check `~/.hermes/webhook_subscriptions.json` for registered webhook routes
- Check `~/.hermes/config.yaml` for `platforms.webhook.extra.routes`
- Check if the webhook server port is listening (`lsof -i :<port>`)

---

## 4. Messaging Platforms (varies per installation)

Hermes can connect to 18+ messaging platforms as a bot. Each platform requires an API key/token and is configured under `gateway.platforms` in config.yaml.

**How to discover:**
- `hermes gateway setup` — interactive setup shows configured platforms
- Read `~/.hermes/config.yaml` for platform configuration blocks
- Check `.env` for platform API keys

**Platform reference (from Hermes docs):**

| Platform | API Key | Probe |
|----------|---------|-------|
| **Telegram** | `TELEGRAM_BOT_TOKEN` | Check process + envoy var presence |
| **Discord** | `DISCORD_BOT_TOKEN` | Check process + envoy var presence |
| **Slack** | `SLACK_BOT_TOKEN` | Check process + envoy var presence |
| **WhatsApp** | `WHATSAPP_TOKEN` | Check envoy var presence |
| **Signal** | Signal CLI integration | Check process + envoy var presence |
| **Email** | SMTP/IMAP credentials | Check envoy var presence |
| **Mattermost** | `MATTERMOST_TOKEN` | Check process + envoy var presence |
| **Matrix** | `MATRIX_TOKEN` | Check process + envoy var presence |
| **Feishu/Lark** | `FEISHU_APP_ID/SECRET` | Check envoy var presence |
| **Microsoft Teams** | `TEAMS_APP_ID/KEY` | Check process + envoy var presence |
| **LINE** | `LINE_CHANNEL_TOKEN` | Check envoy var presence |
| **Yuanbao** | Yuanbao API key | Check envoy var presence |
| **BlueBubbles (iMessage)** | BlueBubbles server URL | Check envoy var presence |
| **QQ** | QQ bot token | Check envoy var presence |
| **DingTalk** | DingTalk token | Check envoy var presence |
| **WeCom** | WeCom token | Check envoy var presence |
| **ntfy** | ntfy topic/token | Check envoy var presence |

**Note:** For messaging platform probes, checking whether the process exists and the env var is set is usually sufficient. The gateway itself is the critical path — individual platform adapters failing don't crash the gateway, they just pause that adapter (circuit breaker).

---

## 5. External Integrations (varies per installation)

### Home Assistant

**How to discover:**
- Env var: `HASS_TOKEN` or `HASS_SERVER` is set
- Config: `~/.hermes/config.yaml` has `homeassistant:` section
- Process: check if Home Assistant is running alongside Hermes

**Probe:** HTTP check on Home Assistant's port (typically :8123)
**Refs:** `ha_list_entities` built-in tool

### Web Search Backends

Hermes uses one of: Firecrawl, Parallel, Tavily, Exa. Not standalone services — they're API keys used by Hermes' built-in tools. No separate process to monitor.

**Config check:** `web.backend` in `config.yaml` or presence of `FIRECRAWL_API_KEY` / `PARALLEL_API_KEY` / `TAVILY_API_KEY` / `EXA_API_KEY` in `.env`

### AI Inference Providers

Hermes can use OpenRouter, Nous Portal, OpenAI, Anthropic, Google, Mistral, Groq, xAI, DeepSeek, and local models. These are API calls, not local processes — no probes needed unless there's a local inference server (e.g., Ollama, vLLM).

**If local inference is running:**
- Ollama: `docker ps | grep ollama` or `ps aux | grep ollama`
- vLLM: `ps aux | grep vllm`
- Check port :11434 (Ollama default) or :8000 (vLLM default)

### Voice & TTS

TTS providers (Edge TTS, ElevenLabs, etc.) are API-based. No local process to monitor unless using local faster-whisper for speech-to-text.

### Browser Automation

Hermes supports Playwright and Puppeteer for browser automation. Check if the `agent-browser` package is installed or if a browser daemon is running:

```bash
lsof -iTCP -sTCP:LISTEN -P -n | grep -E '9222|3000'  # DevTools / agent-browser ports
```

---

## 6. Docker Containers

Docker containers that support Hermes infrastructure.

**How to discover:**
```bash
docker ps -a --format '{{.Names}} {{.Status}} {{.Image}} {{.Ports}}'
```

**Common container patterns:**

| Container | Image | Purpose | Port |
|-----------|-------|---------|------|
| Hindsight memory | `ghcr.io/vectorize-io/hindsight` | Persistent memory backend | :8888 |
| Ollama | `ollama/ollama` | Local LLM inference | :11434 |
| vLLM | `vllm/vllm-openai` | High-throughput LLM serving | :8000 |
| ComfyUI | Various | Image generation UI | :8188 |
| Qdrant | `qdrant/qdrant` | Vector database | :6333 |
| Chroma | `chromadb/chroma` | Vector database | :8000 |
| PostgreSQL | `postgres` | Database (Hindsight, etc.) | :5432 |
| Redis | `redis` | Cache/message broker | :6379 |
| Nginx/Caddy | Various | Reverse proxy | :80/:443 |

**Important:** Don't blindly add probes for every Docker container. Only add probes for containers that are part of the Hermes infrastructure. Let the catalog.loal.yaml and user approval Phase 7 handle additional services.

---

## 7. Unsupervised Processes

Long-running processes on the machine that look like MCP servers or supporting infrastructure but aren't managed by a process supervisor (launchd/systemd) or Docker.

**How to discover:**
```bash
# Long-running Python processes
ps aux | grep -E 'python.*server|python.*mcp|python.*gateway' | grep -v grep

# Long-running Node processes (npx-based MCPs)
ps aux | grep -E 'npx.*mcp|node.*server' | grep -v grep

# Anything listening on a port
lsof -iTCP -sTCP:LISTEN -P -n
```

**Cross-reference:** For each unsupervised process, check if it's already covered by an MCP server entry in config.yaml or by a launchd/systemd service. If it is, skip it (it's already monitored). If it isn't, it may need a `process` probe.

**Common unsupervised process patterns:**
- MCP servers started manually (not in config.yaml)
- Development/test servers
- One-off scripts with `nohup` or background

---

## Introspection Workflow

When loading this reference during Phase 2, follow this sequence:

```
1. System layer (DNS, disk, supervisor)
   → These are always present. Add system-dns, system-disk, system-supervisor.

2. Read config.yaml
   → Parse gateways, mcp_servers, platforms, memory, plugins, webhooks
   → Add probes for each gateway, each MCP server

3. Check process table (ps aux)
   → Cross-reference against services already discovered
   → Find any unsupervised processes that need probes

4. Check Docker (if available)
   → List containers, check against discovered services
   → Add probes for Hermes-infrastructure containers

5. Check cron (hermes cron list)
   → Add probe that cron scheduler is active

6. Check boot-time supervisors
   → launchctl on macOS, systemctl on Linux
   → Find Hermes-managed services

7. Generate catalog.local.yaml
   → Write all discovered services with concrete probes
   → Set dry_run: true
```

---

## Anti-Patterns

- **Adding probes for every Docker container.** Only probe containers that are part of the Hermes stack. The user can add more later.
- **Probing every port.** Only probe ports that your discovered services are supposed to be on. Random port scanning is noisy and wastes tokens.
- **Assuming all machines have Docker.** Check if `docker` is on `PATH` before trying to probe containers.
- **Adding probes for cloud APIs.** Cloud inference providers and web search backends are not local services — they don't need health probes.
- **Scanning the entire filesystem.** Only look at `~/.hermes/`, `/Library/LaunchDaemons/` (macOS), `/etc/systemd/system/` (Linux), and known locations.
- **Probing messaging platforms individually.** The gateway manages them — if the gateway is healthy, platform adapters are handled by the circuit breaker.
