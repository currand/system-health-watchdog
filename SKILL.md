---
name: system-health-watchdog
category: devops
description: "Two-stage health watchdog: silent pre-scanner + LLM triage/remediation."
version: 1.2.0
triggers:
  - set up health watchdog
  - system health check
  - my service is failing
  - add service to watchdog
tags: [devops, sre, monitoring, self-healing, watchdog]
---

# System Health Watchdog

Two-stage pipeline: **Stage 1** (`health-scan.py`, `no_agent=True`) runs probes from `catalog.local.yaml`, outputs `[SILENT]` when healthy or JSON on failure. **Stage 2** (this skill, agent-driven) triages failures, applies fixes from catalog, verifies, reports.

## Token Optimization (Design Principle)

Watchdog skills are cron-driven and run frequently. Token waste compounds fast: a job that runs every 15 minutes and burns 5K tokens per tick costs ~480K tokens/day. Design every watchdog skill with tokens as a first-class concern:

1. **Minimal SKILL.md** — keep the loaded skill doc under 4K tokens. Move deep reference material (error catalogs, remediation playbooks, provider docs) into `references/` files that are loaded conditionally, not unconditionally.
2. **`context_from` is expensive** — only use `context_from` when the downstream job genuinely needs the upstream's full output. If the upstream outputs verbose JSON or logs, the downstream inherits all of it as prompt context every tick. Prefer writing a compact summary (or a file on disk) over piping raw stdout between jobs.
3. **`[SILENT]` protocol** — the pre-scanner must print `[SILENT]` (and nothing else) when healthy. The heartbeat job checks for this string and skips the LLM call entirely when found. This is the single biggest token saver: zero tokens on healthy ticks.
4. **`no_agent=true` when possible** — if the job's logic is pure script (watchdog pattern: check condition → print message or print nothing), set `no_agent=true`. The script's stdout is delivered verbatim; no LLM is invoked.
5. **Conditional reference loading** — load `references/` files only when the pre-scanner detected a failure. Healthy path stays near-zero tokens; failure path gets full context.

**Token math (this skill, every 15 min):**
| Configuration | Pre-Scanner | Heartbeat Agent | Daily Cost |
|---|---|---|---|
| Before (monolithic SKILL.md + context_from) | ~500 | ~6,000 | ~576,000 |
| After (minimal SKILL.md + [SILENT] + conditional refs) | ~500 | ~200 (healthy) / ~4,000 (failure) | ~100,000 |

---

## Context Format (from `context_from`)

The pre-scanner writes JSON to stdout. On healthy systems: `[SILENT]` (8 bytes). On failure:

```json
{"status": "failures", "failed": 2, "failed_services": ["gateway-default", "hindsight"], "results": {"gateway-default": {"name": "Default Gateway", "passed": false, "probes": [{"name": "process_running", "passed": false, "output": "..."}]}}}
```

Load this context. If `[SILENT]` → output `[SILENT]` and exit. Otherwise triage each `failed_services` entry.

## Safety Rules (NEVER SKIP)

1. **Log scan false positive** — service passes process + HTTP probes but fails `log_scan` only → historical artifact, DO NOT restart
2. **Self-healed (launchd)** — `LastExitStatus=256` but process running + endpoint 200 → launchd already recovered, DO NOT restart
3. **Self-healed (Docker)** — container was down at scan time but `docker inspect` shows running + `RestartCount=0` → Docker already recovered, DO NOT restart
4. **Timing window** — always re-run probes before applying ANY fix. If `[SILENT]` on re-scan → self-healed, report only
5. **No restart on false positive** — restarts drop sessions (Discord offset, Telegram spool), force re-indexing, risk data loss. Log artifacts are never worth this cost

## Risk Tiers

| Risk | Behavior |
|------|----------|
| `safe` | Auto-apply silently, verify, report summary |
| `caution` | Apply + report details |
| `hands_off` | Never auto-apply, report + explain |

## Circuit Breaker

Max **3 auto-remediations** per run. Max **3 retries** per `safe` fix, **1** for `caution`. Escalate to user after exhausted retries regardless of risk tier.

## Cascading Failures

Correlate errors within ±60s:
- `network_down`: DNS fail + multiple MCP connection fails → flush DNS (run diagnosis first)
- `gateway_crash`: Gateway exit + downstream failures → check gateway logs
- `auth_expired`: 401 across multiple services → check OAuth tokens
- `disk_full`: Write failures across services → `df -h`

Fix dependencies first (DNS before MCP, system before services).

## Reference Library (load ONLY when relevant)

| Reference | Load when... |
|-----------|-----------------|
| `onboarding.md` | User asks to set up watchdog |
| `discovery.md` | Building/updating `catalog.local.yaml` |
| `catalog-schema.md` | Need YAML schema or examples |
| `adding-services.md` | Adding a probe or service |
| `probe-debugging.md` | Probe config bug (grep flags, HTTP 406, wrong port) |
| `error-classes.md` | Failure matches: connection/dns/auth/crash/config/rate_limit |
| `docker-container-recovery.md` | Docker service failed |
| `state-fingerprints.md` | `seen_count > 1`, need error history |
| `pre-scanner.md` | Debugging `health-scan.py` itself |
| `cron-pitfalls.md` | Debugging cron wiring |

Load only the files relevant to the failure types in the current context. Example: Docker failure → load `docker-container-recovery.md` only.

## Dry-Run Mode

When catalog has `dry_run: true`: run diagnosis + report, but **never** apply fixes or run terminal() for remediation. Respect this flag.

## Report Modes

| Mode | Behavior |
|------|----------|
| `silent` (default) | `[SILENT]` when green, summary when failures fixed |
| `summary` | "3 of 12 services failed. 2 auto-fixed. 1 needs attention." |
| `verbose` | Full pass/fail table with probe results, diagnosis, fix trace |

Check `context_from` JSON `report_mode` field. Default to `silent`.

## What This Skill Does NOT Do

- Discovery (use `discovery.md` when user asks)
- Probe debugging (use `probe-debugging.md`)
- Onboarding (use `onboarding.md`)
- Catalog schema reference (use `catalog-schema.md`)
