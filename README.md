# System Health Watchdog

Autonomous health monitoring and self-healing for AI agent infrastructure. A two-stage pipeline that periodically scans all running services, triages failures, applies safe auto-remediation, and learns from new error patterns — all while minimizing LLM tokens when everything is healthy.

---

## Architecture (2-Stage Pipeline)

```
 cron: */15 * * * *
       │
       ▼
┌──────────────────────────────────┐  no_agent=True, script only
│  STAGE 1: Pre-scanner Script     │  Zero tokens when healthy
│  (health-scan.py)                │  Reads catalog.yaml for service
│                                  │  definitions + probes
│  Runs probes → all green?        │
│  → [SILENT] output, no delivery  │
│  → JSON failures → passed as     │
│    context to Stage 2            │
└──────────┬───────────────────────┘
           │ context_from=job_id
           ▼
┌──────────────────────────────────┐  no_agent=False, LLM-driven
│  STAGE 2: LLM Heartbeat          │  Only fires when failures exist
│  Loads catalog → triages →       │  Fix commands go through
│  applies fixes → verifies →      │  safety guardrails
│  learns new patterns             │
└──────────────────────────────────┘
```

| Stage | What it does | Token cost | Safety |
|-------|-------------|-----------|--------|
| **1: Pre-scanner** (no_agent) | Runs health probes from catalog file via Python script | **Zero** when healthy — `[SILENT]` | Curated script on disk, not dynamic |
| **2: LLM Heartbeat** (agent) | Triages failures, applies fixes, verifies, saves new patterns | Only when failures exist | Every `terminal()` call scanned by guardrails |

---

## How It Works

1. **Minimal `SKILL.md`** — Contains only what's needed every tick:
   - 2-stage architecture
   - `context_from` JSON format
   - 5 safety rules
   - Risk tiers table
   - Circuit breaker + cascading failure detection
   - Reference loading table ("load X when Y")
   - Dry-run + report modes

2. **Conditional Reference Loading** — 12 reference files loaded **only** when relevant failures appear (see `SKILL.md` for the complete reference table).

3. **`[SILENT]` Optimization** — When all probes pass, the pre-scanner outputs `[SILENT]`

---

### Design Principles

1. **Discovery through introspection, not shell scripts.** The agent discovers what services are running by reading config files, inspecting system/Docker/process tables using its own tools.

2. **Stage 1 is observe-only.** The pre-scanner must not modify state, rotate logs, or change configuration. It detects, aggregates, and reports — nothing else.

3. **Safe by design.** Fix commands are curated to pass safety guardrails. No `curl | bash`, no `sudo`, no dangerous patterns. The guardrails stay intact.

---

## Quick Start

### 1. Agent-Driven Discovery

When you ask your agent to set up the watchdog, it introspects the environment:

```
Agent reads config.yaml       → finds MCP servers (command-based, URL-based)
Agent checks launchd          → finds managed daemons (gateways, dashboard, webui)
Agent checks Docker           → finds running containers
Agent checks running processes → finds unsupervised MCP servers
Agent checks cron jobs        → finds active job schedules
Agent prompts you             → asks about any additional services
Agent writes catalog.yaml     → with probes, diagnosis, and fixes for each
```

### 2. Validate

```bash
python3 scripts/health-scan.py
# → [SILENT] if all probes pass
# → JSON output if failures found
```

### 3. Validate the Pipeline

```bash
# Start test server
python3 scripts/test-server.py &

# Run scanner — should be [SILENT]
python3 scripts/health-scan.py

# Kill test server
kill $(cat /tmp/self-heal-test.pid)

# Run scanner — should show failure JSON
python3 scripts/health-scan.py

# Restart and verify recovery
python3 scripts/test-server.py &
python3 scripts/health-scan.py  # → [SILENT] again
```

### 4. Cron Jobs and Webhooks

A cron job that runs tests. On failure, fires a webhook to start triage and repair (if possible and allowed)

---

## Catalog Schema

Services are defined in a `catalog.local.yaml` file. Each service has probes (how to check health), diagnosis (what to check when it fails), and fixes (how to recover it).

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

| `type` | Purpose | `passes_if` examples |
|--------|---------|---------------------|
| `process` | Run a shell command, check output | `'"PID" in out'`, `"int(out) >= 1"`, `"len(lines) >= 1"` |
| `http` | HTTP GET to a health endpoint | `"status == 200"` (only `==` supported) |
| `log_scan` | Scan a log for recent error patterns | `"no ERROR\|CRITICAL"` (diagnosis only) |
| `file_check` | Check file existence, size, or age | `"exists"`, `"size > 100"` |

> ⚠️ `type: command` does NOT exist. Use `type: process`.

> ⚠️ HTTP probes only support `status == N`. `status < 500`, `status > 200` always fail.

> ⚠️ The `last_exit_ok` fallback (`'"LastExitStatus" = 0'`) is a substring presence check, not a value check. Rely on `process_running` and `http_responding` for real crash detection.

### Risk Tiers for Fixes

| Risk | Behavior | Example |
|------|----------|---------|
| 🟢 **safe** | Auto-apply silently, verify, report summary | Process restart, launchctl kickstart |
| 🟡 **caution** | Apply + report details | Full bootout/bootstrap restart |
| 🔴 **hands_off** | Never auto-apply, explain why | Config edits, env changes, secrets |

### Circuit Breaker

- Max 3 auto-remediations per run
- Per-fix retries: 3 for safe, 1 for caution
- Exhausted retries → escalate regardless of tier
- Resets when error resolves

---

## The Pre-Scanner Script

`scripts/health-scan.py` is a standalone Python script that:

1. Loads `catalog.local.yaml` (falls back to `templates/catalog.default.yaml`)
2. Runs each service's probes in dependency order
3. Outputs `[SILENT]` if all green, or JSON with failure details
4. Uses Python stdlib for HTTP checks (`urllib.request`) — no `curl`
5. Maintains a persistent error fingerprint state file (`state/health_state.json`)

---

## Error Classes

| Class | Severity | Examples |
|-------|----------|---------|
| connection | Critical | ECONNREFUSED, Connection refused |
| dns | Warning | nodename nor servname provided, gaierror |
| auth | Critical | 401, 403, token expired |
| crash | Critical | Traceback, exit code 1 |
| config | Warning | Config version outdated |
| rate_limit | Low | 429, Too many requests |

---

## Report Modes

```yaml
global:
  report_mode: silent    # silent | summary | verbose
```

| Mode | Behavior |
|------|----------|
| **silent** (default) | `[SILENT]` when green. Summary when failures fixed. |
| **summary** | "3 of 12 failed. 2 auto-fixed. 1 needs attention." |
| **verbose** | Full pass/fail table with probe results and fix trace. |

---

## Cron Wiring Pitfalls

1. **Wrapper path stale on skill move** — use auto-resolving `SCRIPT_DIR` in the wrapper script
2. **Missing +x bit** — invoke via `python3 script.py` not `exec script.py`
3. **Cron's sparse PATH** — export common directories (`${HOME}/.local/bin`, platform-specific paths like `/opt/homebrew/bin`) in the wrapper. See `references/cron-pitfalls.md`.
4. **Python3 without yaml** — use the agent's venv Python path
5. **Forgotten `context_from`** — verify with `cronjob action=list | grep context_from`

---

## Repo Structure

```
system-health-watchdog/
├── README.md                     # This file — human-readable docs
├── SKILL.md                      # Agent-facing instructions (triggers, onboarding flow)
├── .gitignore                    # Ignores catalog.local.yaml, state/
├── scripts/
│   ├── health-scan.py            # Pre-scanner — runs all probes (required)
│   └── test-server.py            # Pipeline validation test
├── references/
│   ├── probe-design.md           # How to write concrete unit tests
│   └── probe-onboarding.md       # HTTP probe validation workflow
└── templates/
    ├── catalog.default.yaml      # Reference catalog schema
    └── new-service.md            # Template for adding services
```

### Why no setup.sh or discover.py?

The watchdog originally shipped with shell scripts that parsed plists, grepped process tables, and ran Docker commands to discover services. These were **brittle** — they assumed macOS launchd, Homebrew paths, and specific log locations. On a different OS or configuration, they would silently fail.

Instead, the agent discovers services by **introspecting its own environment** using its native tools:
- Reading `config.yaml` — finds MCP servers with their command/URL
- Checking `launchctl list` — finds managed daemons
- Running `docker ps` — finds containers
- Scanning `ps aux` — finds unsupervised processes

The agent then builds the catalog itself — no shell scripts needed. This works on any OS the agent can run on, because the agent adapts to what it finds rather than guessing what to find.

---

## License

MIT