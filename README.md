# System Health Watchdog

Keeps an eye on your Hermes infrastructure. If something breaks, it figures out what went wrong, tries to fix it, and lets you know. Costs nothing to run when everything's fine.

## How the Two Stages Work

A cron job fires a Python script every 15 minutes. The script pokes all your services (gateways, MCP servers, Docker containers, whatever you told it to watch). If everything passes, it prints `[SILENT]` and the cron scheduler tosses that output in the trash. No delivery, no LLM, no tokens spent.

When something fails, the script POSTs the failure details to a local webhook on your Hermes gateway. That webhook spins up an agent session, which loads this skill, pings you with a "🚨 hey I'm on it" message, and starts working through the problem.

```
 cron: */15 * * * *
       │
       ▼
┌─────────────────────────────────────┐  Pure Python, no agent
│  STAGE 1: health-scan.py            │  Zero tokens if healthy
│                                     │
│  Runs probes, all green?            │
│  → prints [SILENT] → cron swallows  │
│  → any failures? POST JSON + HMAC   │
└──────────┬──────────────────────────┘
           │ webhook POST
           ▼
┌─────────────────────────────────────┐  Agent session, POST-only
│  STAGE 2: Triage Agent              │
│                                     │
│  Loads skill → notifies user →      │
│  triages failures → applies fixes   │
│  → verifies → reports final status  │
│                                     │
│  ⚠️ Terminal tools need explicit    │
│     config — see below              │
└─────────────────────────────────────┘
```

## What You Actually Want to Know

### The Agent Tells You When It's Working

The first thing the triage agent does is fire off a 🚨 **Health Watchdog: Triaging N failure(s)** message to wherever your webhook delivers (Discord, Telegram, wherever). That way you see something happening before it's done. When it finishes, or gets stuck on something it can't fix, you get a follow-up.

### Self-healing needs explicit permission for tools

Webhook agent sessions are read-only by default. Webhooks can receive untrusted data (public PR comments, random pings from the internet), so Hermes doesn't hand out terminal access to webhook agents unless you specifically say so.

Out of the box, the triage agent can use `web_search`, `web_extract`, `vision_analyze`, and `clarify`. It can figure out what's wrong and tell you the fix. It just can't run the fix.

To let it actually do things, add this to `~/.hermes/config.yaml`:

```yaml
platform_toolsets:
  webhook:
  - hermes-cli
```

Only do this if your gateway is loopback only (127.0.0.1). If it's public facing, leave the default and have the agent tell you what commands to run.

### Why This Setup Costs Nothing When Things Are Good

The pre-scanner is a plain Python script. No LLM, no agent loop, no token spend. When everything passes, it prints `[SILENT]` and exits. The cron scheduler sees that, shrugs, and moves on. Stage 2 doesn't have a cron job at all. It only fires when the pre-scanner POSTs a failure. So healthy hours cost exactly nothing.

The SKILL.md links to separate reference files for Docker failures, cron debugging, HMAC wiring, probe design, and so on. The agent loads only the one it needs for whatever broke. No point pulling in the Docker container recovery guide when an MCP server just crashed.

---

## Installation and Maintenance via Agent Prompt

Your agent knows how to set this up — the SKILL.md covers the details. Just say what you need.

### Install from scratch

> Install the system-health-watchdog skill. Discover my services and set up the full pipeline.

If you want read-only triage (agent investigates but doesn't auto-fix):

> Install the system-health-watchdog skill, but keep the webhook platform read-only.

### Add a service to monitor

> Add [service name] to the system health watchdog.

### Check status

> What's the status of my system health watchdog?

### Troubleshoot

> The watchdog pre-scanner is reporting failures but no triage agent fires.

> The triage agent fires but just talks and doesn't fix anything.

### Remove a service

> Remove [service name] from the health watchdog catalog.

### Test the pipeline

> Test the watchdog pipeline with a fake failure.

---

## Setup (Manual)

### 1. Create the Webhook Subscription

```bash
hermes webhook subscribe system-health-alerts \
    --secret INSECURE_NO_AUTH \
    --deliver discord \
    --deliver-chat-id <your-channel-id> \
    --description "Health pre-scanner failures" \
    --skills system-health-watchdog
```

`INSECURE_NO_AUTH` is fine here — the gateway binds to 127.0.0.1, nothing external can reach it. If you've got a public-facing gateway, use a real HMAC secret.

### 2. Create the Pre-Scanner Cron Job

```bash
hermes cron create \
    --name system-health-pre-scanner \
    --schedule "*/15 * * * *" \
    --script scripts/health-scan.py \
    --no-agent
```

The `--no-agent` flag is what makes this free to run. Pure Python, no LLM.

### 3. Validate the Scanner

```bash
python3 scripts/health-scan.py
# → [SILENT] if all probes pass
# → JSON blob if something's wrong
```

### 4. Test the Full Pipeline

```bash
hermes webhook test system-health-alerts \
    --payload '{"status":"failures","failed":1,"failed_services":["test"]}'
```

You should see a 🚨 notification in your delivery channel, followed by whatever the triage agent finds.

## Adding Services to Watch

Services live in `catalog.local.yaml`. Each service has probes (how to check if it's healthy), diagnosis (what to look at when it fails), and fixes (how to recover it).

```yaml
services:
  my-service:
    name: "Human Readable Name"
    depends_on: ["system-dns"]

    health_probes:
      - name: "process_running"
        type: process
        command: "launchctl list ai.hermes.my-service"
        passes_if: '"PID" in out'

    diagnosis:
      tests:
        - "tail -20 ~/.hermes/logs/my-service.error.log"

    fixes:
      - name: "kickstart"
        risk: safe
        max_retries: 3
        action: "launchctl kickstart -k system/ai.hermes.my-service"
        verify: "launchctl list ai.hermes.my-service | grep PID || true"
```

### Probe Types

| `type` | What it does | `passes_if` examples |
|--------|-------------|---------------------|
| `process` | Run a shell command, check what it prints | `'"PID" in out'`, `"int(out) >= 1"` |
| `http` | Hit a URL, check status code | `"status == 200"` (only `==`, no `<` or `>`) |
| `log_scan` | Scan a log for error patterns | `"no ERROR\|CRITICAL"` (diagnosis only, not a real probe) |
| `file_check` | Check a file exists, size, or age | `"exists"`, `"size > 100"` |

### Risk Tiers for Fixes

| Risk | What happens | Example |
|------|-------------|---------|
| 🟢 **safe** | Auto-apply, verify, quiet summary | `launchctl kickstart` |
| 🟡 **caution** | Apply, report details | Full bootout/bootstrap |
| 🔴 **hands_off** | Never auto-apply, just explain | Config edits, env changes |

The circuit breaker caps auto-fixes at 3 per run, with 3 retries for safe fixes and 1 for caution. If retries run out, it escalates to you. Resets automatically when the error clears.

## The Pre-Scanner Script

`scripts/health-scan.py` is the piece that actually runs on a schedule. It loads `catalog.local.yaml` (or falls back to `templates/catalog.default.yaml`) and runs each service's probes in dependency order. If DNS is down, there's no point trying to hit APIs yet.

It uses Python's stdlib for HTTP checks (`urllib.request`), so there's no `curl` dependency. If everything passes, it prints `[SILENT]`. If something fails, it prints a JSON blob and POSTs the same blob to the gateway webhook with an HMAC signature. It also keeps a state file at `state/health_state.json` so it can tell whether a problem is new or just the same thing still being broken.

## Quick Reference: Common Problems

| You see | What's probably wrong |
|---------|----------------------|
| Failure JSON shows up in chat but no agent fires | HMAC secret mismatch — check gateway logs for `Invalid signature` |
| Agent fires but just talks, doesn't fix anything | Webhook tool restriction — needs `platform_toolsets.webhook` in config |
| `Unknown deliver type` error in logs | You used `--deliver discord:123` — needs `--deliver discord --deliver-chat-id 123` |
| `[SILENT]` never shows | Pre-scanner cron job might not be running — `hermes cron list` |

## Repo Layout

```
system-health-watchdog/
├── README.md                         # This
├── SKILL.md                          # What the agent reads
├── .gitignore
├── catalog.local.yaml                # Your services (gitignored)
├── scripts/
│   ├── health-scan.py                # The pre-scanner
│   ├── cron-health-check.py          # Probe for checking cron health
│   └── test-server.py                # Pipeline test helper
├── references/
│   ├── skill-onboarding-and-configuration.md
│   ├── service-discovery-catalog.md
│   ├── docker-container-recovery.md
│   ├── cron-pitfalls.md
│   ├── probe-design.md
│   ├── probe-onboarding.md
│   ├── probe-debugging.md
│   ├── env-var-wiring.md
│   └── webhook-delivery-format.md
└── templates/
    ├── catalog.default.yaml
    └── new-service.md
```

## License

MIT