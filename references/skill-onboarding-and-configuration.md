# Skill Onboarding & Configuration

**When to load this reference:**
- First-time setup of the watchdog skill on a machine
- Reconfiguration after machine migration or profile changes
- Repair/diagnosis when the skill itself isn't working (cron silent, webhook missing, catalog empty)
- User explicitly asks "set up the watchdog" or "onboard the skill"

**Do NOT load this for:** routine failure triage (use the individual reference docs), adding a single probe (use `probe-design.md`), or investigating a specific service that's down.

---

## Overview: The Onboarding Plan

Onboarding follows an 8-phase plan. Each phase builds on the previous one. Never skip a phase. Never jump to production.

| Phase | What | Gate |
|-------|------|------|
| 1 | Skill install | Files exist, skill registered in Hermes |
| 2 | Introspection | Discovered services written to catalog |
| 3 | Webhook creation | Endpoint registered, HMAC secret wired |
| 4 | Cron creation | Pre-scanner running every 15 min |
| 5 | Probe creation | Probes written to catalog, all pass |
| 6 | Testing | Probe failures catch real problems, not config bugs |
| 7 | User approval | User signs off on probes, risk tiers, report mode |
| 8 | Production | `dry_run: false`, monitor for 24h |

**Critical rule:** Do not proceed past Phase 6 until all probes PASS when the service is healthy. A probe that fails on a healthy service is a config bug, not a real outage. Fix the probe first.

---

## Phase 1 — Skill Install

### Goal
Get the skill files into the agent skills directory so the agent can find them.

### Steps

1. **Confirm source location** — the skill lives in the agent skill directory in the appropriate category. Verify the directory exists and contains the expected structure:

   ```
   system-health-watchdog/
   ├── SKILL.md
   ├── catalog.local.yaml        # may not exist yet; will generate in Phase 2
   ├── templates/
   │   ├── catalog.default.yaml
   │   └── new-service.md
   ├── scripts/
   │   ├── health-scan.py
   │   ├── test-server.py
   │   └── cron-health-check.py
   ├── references/
   │   ├── docker-container-recovery.md
   │   ├── cron-pitfalls.md
   │   ├── probe-design.md
   │   ├── probe-onboarding.md
   │   ├── env-var-wiring.md
   │   ├── probe-debugging.md
   │   └── skill-onboarding-and-configuration.md
   └── state/                     # auto-created by health-scan.py
   ```

2. **Verify the agent can see it** — run `hermes skills list` or check `~/.hermes/config.yaml` for disabled/external paths. If the skill is in the disabled list, enable it.

3. **Check dependencies** — the pre-scanner needs Python 3.9+ with PyYAML:
   ```bash
   python3 -c "import yaml; print('PyYAML OK')"
   ```
   If missing, install: `pip3 install pyyaml` or `uv pip install pyyaml` depending on the Hermes venv.

4. **Verify no stale config** — if `catalog.local.yaml` exists from a previous install, check that it's not a stale copy. Old probes that reference non-existent services produce false-positive failures. When in doubt, move it aside: `mv catalog.local.yaml catalog.local.yaml.bak`.

### Outcome
- Skill directory present with all files
- Hermes can load the skill (not disabled)
- Python dependencies available
- No stale catalog polluting probes

---

## Phase 2 — Introspection

### Goal
Discover what services exist on this machine and write `catalog.local.yaml` with real probes.

### Approach: Discovery, Not Configuration
The watchdog is designed to **discover** services, not require a manual manifest. Use the following tools to find what's running:

#### What to discover

| Source | What to look for | Probe type |
|--------|------------------|------------|
| Supervised daemons (`/Library/LaunchDaemons/` on macOS, `/etc/systemd/system/` on Linux) | Hermes service files | `process` — `launchctl list <label>` (macOS) or `systemctl is-active <name>` (Linux) |
| Hermes config (`~/.hermes/config.yaml`) | `gateways.*`, `mcp_servers.*` | `process` + `http` |
| Docker (`docker ps -a`) | Running containers with Hermes infrastructure | `process` — `docker inspect` |
| Process table (`ps aux`) | Python/Node MCP servers not managed by a supervisor | `process` — `ps aux` grep |
| Cron jobs (`hermes cron list`) | Any active watchdog or maintenance jobs | `process` — `hermes cron list` |
| System services | DNS, disk, memory | `process` or `file_check` |

The `templates/catalog.default.yaml` file documents the full YAML schema with example entries. Use it as a reference, not a copy-paste template.

#### Steps

1. **List launchd daemons** — `launchctl list | grep ai.hermes` finds gateway and dashboard processes. Also scan `ls /Library/LaunchDaemons/ai.hermes*.plist`.

2. **Read Hermes config** — load `~/.hermes/config.yaml` and look at `gateways` and `mcp_servers` sections. Each gateway needs a process probe + HTTP probe on its port. Each MCP server with a `command:` field needs a process probe. URL-based MCPs need HTTP probes.

3. **Check Docker** — `docker ps -a --format '{{.Names}} {{.Status}} {{.Image}}'` shows running containers. Focus on Hermes infrastructure containers.

4. **Check cron** — `hermes cron list` shows existing cron jobs. The watchdog needs exactly ONE cron job (the pre-scanner). If old watchdog cron jobs exist from previous attempts, clean them up.

5. **Write catalog.local.yaml** — create `catalog.local.yaml` in the skill directory (`~/.hermes/skills/devops/system-health-watchdog/catalog.local.yaml`). Start with these essential services:

   - `system-dns` — DNS resolution check (everyone depends on this)
   - `gateway-default` — process probe + HTTP probe on the gateway's port (typically `:8644/health`)
   - Any additional gateways (named profiles) on their ports
   - `memory-backend` — if running in Docker, process probe with docker inspect
   - `webui` — if running, HTTP probe (typically on `:8787`)
   - `hermes-cron-jobs` — cron job health probe
   - Any MCP servers discovered in config

6. **Set initial global config:**
   ```yaml
   global:
     report_mode: summary
     dry_run: true              # ← CRITICAL: must be true in intro phase
   ```

### Pitfall: Supervisor vs Process Probe
Don't use `launchctl list` (macOS) or `systemctl` (Linux) for processes that aren't managed by a service supervisor. For unsupervised processes (started by config.yaml with `command:` or run manually), use `ps aux` with a grep pattern instead. Mixing these up yields false-positive failures.

### Pitfall: SSH Tunnel Intercept
When listing HTTP services, run `lsof -iTCP -sTCP:LISTEN -P -n` first. If you see `ssh` listening on a port, that port is a tunnel to a remote service — the HTTP probe will check the remote machine, not localhost. Cross-reference with a process-level probe on the same port to detect this.

### Outcome
- `catalog.local.yaml` exists with real services
- `dry_run: true`
- All probes reference patterns the scanner can evaluate (see `references/probe-design.md`)
- No phantom services from stale configs

---

## Phase 3 — Webhook Creation

### Goal
Register a Hermes webhook subscription so Stage 1 (pre-scanner) failures trigger Stage 2 (agent triage).

### Architecture
The pre-scanner POSTs failure JSON to the default gateway's webhook endpoint. The gateway authenticates the POST with HMAC-SHA256, then launches an agent session that loads this skill.

### Steps

1. **Pick the gateway** — the webhook lives on the default Hermes gateway (typically port `:8644`). If multiple gateways, use the profile you were called from. Verify it's responding:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" http://localhost:<GATEWAY_PORT>/health
   # Must return 200
   ```

2. **Generate or confirm the HMAC secret** — this is a shared secret that must match in three places:
   - `~/.hermes/.env` as `WEBHOOK_SECRET`
   - Gateway config (`~/.hermes/config.yaml` under `webhook.extra.secret`) as ${WEBHOOK_SECRET}
   - `~/.hermes/webhook_subscriptions.json` under the `system-health-alerts` entry

   If no secret exists, generate one:
   ```bash
   openssl rand -hex 32
   ```

3. **Wire the three-way match** — see `references/env-var-wiring.md` for exact locations and formats. Every place must have the same value. A mismatch produces `HTTP Error 401` from the gateway.

4. **Register the subscription** — ensure `~/.hermes/webhook_subscriptions.json` has an entry:
   ```json
   {
     "system-health-alerts": {
       "skill": "system-health-watchdog",
       "deliver": "origin"
     }
   }
   ```

5. **Verify the webhook URL in the pre-scanner** — the shell wrapper (`health-scan.sh` at `~/.hermes/scripts/`) passes `--webhook-url http://localhost:<GATEWAY_PORT>/webhooks/system-health-alerts` to `health-scan.py`. Make sure the URL matches the subscription name exactly. The port is determined by the gateway that handles the subscription — typically `:8644` for the default gateway.

6. **Test end-to-end** — force a probe failure (e.g., check a DNS name that doesn't exist in `system-dns` temporarily) and run the pre-scanner manually. Check the gateway logs for:
   ```
   "received webhook POST" ... "status: 202"
   ```
   If you see 401, the HMAC secret doesn't match somewhere. Go back to step 2.

### Outcome
- Webhook subscription registered on the default gateway
- HMAC secret identical in `.env`, `config.yaml`, and `webhook_subscriptions.json`
- Manual probe failure triggers a 202 from the gateway
- Bad HMAC produces 401, not silent failure

---

## Phase 4 — Cron Creation

### Goal
Schedule the pre-scanner to run every 15 minutes with `no_agent=true`.

### Steps

1. **Create the shell wrapper** — the cron job doesn't call `health-scan.py` directly. It calls a shell wrapper at `~/.hermes/scripts/health-scan.sh` that:
   - Sources the `.env` file for `WEBHOOK_SECRET` and `WEBHOOK_URL`
   - Resolves the Hermes venv Python (see `references/cron-pitfalls.md`)
   - Calls `health-scan.py` with the right arguments
   - Handles PATH resolution (common tool directories, platform-specific paths) since cron/launchd/systemd has a minimal PATH

   If `health-scan.sh` doesn't exist, create it. The wrapper must:
   - Export `WEBHOOK_SECRET` before calling Python
   - Use `set -euo pipefail`
   - Print stderr to logger or a log file (not lost to the void)

2. **Create the cron job** — use `hermes cron` to create a job with these properties:
   ```bash
   hermes cron create system-health-pre-scanner \
     --schedule "*/15 * * * *" \
     --no-agent \
     --command "cd ~/.hermes && bash scripts/health-scan.sh" \
     --workdir ~/.hermes
   ```

   | Parameter | Value | Why |
   |-----------|-------|-----|
   | `--schedule` | `*/15 * * * *` | Poll every 15 min; fast enough for most services, sparse enough to avoid token waste |
   | `--no-agent` | Required | The script output IS the response — no LLM invoked |
   | `--command` | Shell wrapper path | Never call health-scan.py directly (PATH/venv issues) |
   | `--workdir` | `~/.hermes` | Ensures relative paths resolve consistently |

3. **Verify the cron job registered** — `hermes cron list` should show `system-health-pre-scanner` with `status: ok` or a next-run time.

4. **Let one cycle run** — wait for the first actual cron tick, or force it:
   ```bash
   hermes cron run system-health-pre-scanner
   ```
   The output should be `[SILENT]` (all probes pass) or JSON with failures.

5. **Check cron logs** — if output is empty or the job didn't run, load `references/cron-pitfalls.md` and diagnose (likely `$HOME` unset, wrong Python, or minimal PATH).

### Outcome
- Pre-scanner cron job registered, scheduled every 15 minutes
- First run produces `[SILENT]` or valid JSON
- Cron logs show no Python errors or missing env vars

---

## Phase 5 — Probe Creation

### Goal
Refine the probes in `catalog.local.yaml` so every healthy service passes, and every real outage is detected.

### The Golden Rule
> A probe must be a concrete, observable unit test — a yes/no question about the current state of a system. Not an interpretation of past events.

### Steps

1. **Load `references/probe-design.md`** and read it thoroughly before writing a single probe. Every probe design mistake in this repo's history is documented there.

2. **For each service, write three things:**
   - **health_probes:** what to check (the yes/no question)
   - **diagnosis.tests:** how to investigate when it fails (log tails, inspection commands)
   - **fixes:** what to do about it, with `risk:` tier

3. **Assign risk tiers honestly:**
   - `safe` — restarting doesn't drop data or sessions (launchd kickstart, DNS flush)
   - `caution` — restart may drop in-flight work but service recovers cleanly (MCP server restart)
   - `hands_off` — never auto-apply; report and explain (external MCP, Docker containers)

   See `catalog.default.yaml` for tier examples.

4. **Set `depends_on` correctly.** If service A calls service B, A's `depends_on` should include B. This prevents cascading fixes (never try to restart A when B is the root cause).

5. **Write diagnosis tests that produce useful output.** `tail -20` of the relevant log is the gold standard. Generic command dumps (`env`, `ps aux —full`) produce too much noise and waste tokens.

### Pitfall: `$HOME` resolution in probe commands

When you write a probe command that uses `~` (e.g. `python3 ~/.hermes/scripts/cron-health-check.py`), the shell expands `~` to `$HOME`. If `$HOME` is wrong — which can happen in cron or launchd contexts where `HOME` is set to a non-standard path — the probe will fail with "file not found" even though the script exists.

**How to diagnose:** Before assuming the service is down, check:
```bash
echo "\$HOME = $HOME"
ls ~/.hermes/scripts/cron-health-check.py
```
If `$HOME` is wrong but the file exists at the real home path, use the absolute path (e.g., `/Users/<user>/.hermes/scripts/cron-health-check.py`) in the probe command. The `catalog.local.yaml` is per-machine config, so absolute paths are acceptable there.

**When this bites:** Manual testing in a terminal whose `$HOME` was corrupted by a previous background process, or testing while the `$HOME` env var points to a non-standard path. The actual cron job runs with the correct `HOME`, so in-production probes are unaffected — it only bites during agent-led testing.

6. **For supervised services (launchd/systemd):**
   ```yaml
   health_probes:
     - name: "process_running"
       type: process
       command: "launchctl list ai.hermes.gateway"   # macOS; on Linux: systemctl is-active hermes-gateway
       passes_if: '"PID" in out'
   ```
   Do NOT use `grep` with launchctl output — the `PID` field is right there.

7. **For unsupervised MCP servers (command-based):**
   ```yaml
   health_probes:
     - name: "process_running"
       type: process
       command: "ps aux | grep -e 'mcp-server.*serve' | grep -v grep"
       passes_if: "len(lines) >= 1"
   ```
   Use `grep -e` when the pattern starts with `--` to avoid macOS BSD grep's option parsing trap (see `references/probe-debugging.md`).

8. **For HTTP endpoints:**
   Load `references/probe-onboarding.md` and follow the validation workflow — `lsof` to find the bound address, `curl` to test the exact path, then cross-reference. Never guess the URL.

9. **Name services and probes clearly.** `gateway-default` is better than `svc1`. `process_running` is better than `check1`.

### Outcome
- Every service has at least one health probe
- `depends_on` chains are correct (no cycles, no orphans)
- Diagnosis tests provide actionable log snippets
- Fixes have realistic risk tiers
- `dry_run: true` still set

---

## Phase 6 — Testing

### Goal
Verify every probe passes when the service is healthy, and every probe fails correctly when the service is down.

### Steps

1. **Run the pre-scanner manually:**
   ```bash
   python3 scripts/health-scan.py --catalog catalog.local.yaml
   ```
   Expected output: `[SILENT]`

2. **If you see JSON failures** — load `references/probe-debugging.md` and work through:
   - Is the probe command even available? (`shutil.which`)
   - Does the process exist? (`ps aux | grep ...`)
   - Is the HTTP endpoint at the right path/port? (Run `curl` by hand)
   - Is there an SSH tunnel on that port?
   - Is macOS BSD grep eating `--` flags?

   Do NOT assume the service is down. 90% of first-run failures are probe config bugs.

3. **Run full scanner with verbose mode** to see all probes:
   ```bash
   python3 scripts/health-scan.py --catalog catalog.local.yaml 2>&1 | python3 -m json.tool
   ```

4. **Force a failure to test detection** — add a probe that checks a non-existent name:
   ```yaml
   - name: "test_failure_detection"
     type: process
     command: "host this-does-not-exist-12345.com"
     passes_if: '"has address" in out'
   ```
   Run the scanner again. You should see JSON with the test service in `failed_services`. Then remove this probe.

5. **Test webhook delivery** — if you see JSON output, the scanner also POSTed to the webhook. Check the gateway logs to confirm 202. If webhook fails silently (scanner catches `HTTP Error 401`), re-check HMAC secrets.

6. **Test the cron wrapper** — run the shell wrapper the way cron will:
   ```bash
   bash ~/.hermes/scripts/health-scan.sh
   ```
   Expected output: `[SILENT]` (same as running health-scan.py directly).

7. **Test a fix (still in dry-run)** — trigger a probe failure and watch the webhook fire an agent session. The agent should load this skill, run diagnosis, consider fixes, but NOT apply them because `dry_run: true`.

### Outcome
- All probes pass on healthy services: `[SILENT]`
- Probe config bugs fixed (not services restarted)
- Webhook delivers 202 on failure
- Shell wrapper produces same output as direct Python
- Dry-run agent session runs diagnosis but applies no fixes

---

## Phase 7 — User Approval

### Goal
Present the complete catalog to the user for review and sign-off before any auto-remediation goes live.

### Steps

1. **Present the full catalog** — show the user the complete `catalog.local.yaml`. Highlight:

   - **Services monitored:** list of service names and what each checks
   - **Risk tiers:** which services are `safe` (auto-fix), `caution` (fix + report), `hands_off` (report only)
   - **Diagnosis tests:** what the agent will check when investigating a failure
   - **Fix commands:** the exact shell commands that will run on failure

2. **Flag any concerns:**
   - Services where the fix command could be destructive
   - Services with incomplete diagnosis tests
   - Any probe that seems fragile (port numbers, hardcoded paths)
   - Probes on SSH-tunneled ports (the probe checks the remote, not local)

3. **Ask the user to confirm:**
   - Is the list of services complete? Any missing?
   - Are risk tiers acceptable? Should anything be bumped to `hands_off`?
   - Is `report_mode: summary` OK, or prefer `silent`?
   - Any services that should never be restarted automatically?

4. **Offer to add/remove services or change tiers** based on user feedback. Iterate until the user approves the catalog.

5. **Do NOT proceed to Phase 8** without explicit user approval. This is a hard gate.

### Outcome
- User has seen and approved the full catalog
- Risk tiers are confirmed
- Report mode is set
- User knows which services can be auto-remediated
- This is the last phase where `dry_run: true`

---

## Phase 8 — Production

### Goal
Switch from dry-run to live operation. The pre-scanner now triggers real fixes.

### Steps

1. **Switch `dry_run` to `false`** in `catalog.local.yaml`:
   ```yaml
   global:
     report_mode: summary    # or silent, based on user preference
     dry_run: false
   ```

2. **Verify the cron job is still running:**
   ```bash
   hermes cron list
   ```
   Confirm `system-health-pre-scanner` has status `ok` and shows a next-run time.

3. **Run a full scan manually** to confirm `[SILENT]` with dry_run disabled:
   ```bash
   python3 scripts/health-scan.py --catalog catalog.local.yaml
   ```

4. **Monitor for 24 hours** — let the watchdog run through its natural cycle. At the end of 24 hours:

   - Check that the cron job has been running (check `hermes cron list` for `last_run` timestamps)
   - Verify there were no webhook errors (check gateway logs for 4xx on the health endpoint)
   - Confirm no false-positive fires (services incorrectly flagged as down)
   - Confirm no false-negative silence (a transient failure that should have been caught)

5. **Adjust based on monitoring:**
   - If false positives: fix the probe (back to Phase 5-6)
   - If false negatives: add more probes (Phase 5)
   - If auto-fix caused issues: bump risk tier or improve fix command
   - If webhook didn't fire: re-check HMAC wiring (Phase 3)

6. **Set report_mode** per user preference:
   - `silent` — reports only when something fails and is fixed
   - `summary` — brief report on failures and resolutions
   - `verbose` — full details (best for initial days in production)

### Outcome
- `dry_run: false` — fixes now execute automatically
- 24-hour monitoring period with no issues
- User knows how to receive watchdog reports
- Skill is in live production mode

---

## Repair & Diagnostics (When The Skill Itself Is Broken)

Use this section when the watchdog itself isn't working — cron silent, no webhook fires, catalog gone, or phantom failures.

### Symptom: Cron silent, no output at all

1. Check if cron job exists: `hermes cron list | grep system-health-pre-scanner`
2. If missing: go to Phase 4 (cron creation)
3. If exists but `last_status != ok`: load `references/cron-pitfalls.md`
4. Common causes: `$HOME` unset in cron, wrong Python, missing PATH to `docker`/`launchctl`/`host`/`systemctl`, or platform-specific tools not available

### Symptom: Webhook never fires, even on failure

1. Manually run scanner with a forced failure (Phase 6, step 4)
2. If `[SILENT]`: the probe isn't failing — test with a different `passes_if`
3. If JSON printed but no webhook: check `HEALTH_WEBHOOK_URL` env var and `.env` loading in the wrapper script
4. Check gateway logs for `system-health-alerts` route existence
5. If 401: load `references/env-var-wiring.md` and verify the three-way match

### Symptom: All services fail on scan

1. Almost certainly a catalog problem, not infrastructure
2. Check `catalog.local.yaml` exists and is valid YAML: `python3 -c "import yaml; yaml.safe_load(open('catalog.local.yaml'))"`
3. Check if the file fell back to `catalog.default.yaml` (the example entries all fail by design)
4. If the file is corrupted or empty: go to Phase 2 (introspection) to regenerate

### Symptom: One service always fails even when healthy

1. Load `references/probe-debugging.md` and work the checklists
2. Is the probe command available on this platform? (macOS doesn't have `systemctl`)
3. Is `grep` eating `--` flags? (macOS BSD grep quirk)
4. Is the HTTP endpoint an SSE/streaming endpoint returning 406?
5. Is the process bound to a specific interface, not localhost?

### Symptom: Agent session triggers but does nothing useful

1. The webhook is working (good), but the agent isn't loading/correlating the failure data
2. Check the webhook subscription's `skill` field — it must be `system-health-watchdog`
3. If the subscription exists but loads a different skill, the payload label mismatch

---

## Anti-Patterns

- **Creating a second cron job for watchdog.** There is exactly ONE cron job: `system-health-pre-scanner`. Stage 2 is webhook-driven, not cron-driven. Duplicating cron creates race conditions and token waste.
- **Adding probes before Phase 2 (introspection).** Probes for services that don't exist produce phantom failures.
- **Jumping from Phase 1 to Phase 8.** Every skipped phase is a future 3AM debugging session. The phases exist because real deployers found every single shortcut the hard way.
- **Hardcoding absolute paths in probes.** Use `~/.hermes/...` or `$HOME` (with awareness that `$HOME` is unreliable in cron — see `references/cron-pitfalls.md`).
- **Setting `dry_run: false` before Phase 7.** User approval is the safety gate. An unapproved catalog with `dry_run: false` can restart production services at 3AM.
