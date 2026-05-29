# Probe Design: Concrete Unit Tests

Every health probe in the catalog must be a **concrete, observable unit test**.
A yes/no question about the current state of a running system. Not an
interpretation of past events.

## Concrete (good)

```yaml
- name: "process_running"
  type: process
  command: "launchctl list ai.hermes.gateway"
  passes_if: '"PID" in out'
```

```yaml
- name: "http_responding"
  type: http
  url: "http://localhost:8888/health"
  passes_if: "status == 200"
```

```yaml
- name: "container_running"
  type: process
  command: "docker inspect hindsight --format='{{.State.Running}}'"
  passes_if: '"true" in out'
```

```yaml
- name: "disk_space"
  type: process
  command: "df -h / | tail -1 | awk '{print $5}' | tr -d '%'"
  passes_if: "int(out) < 90"
```

## Assumption-based (avoid)

```yaml
# ❌ Looks at log content and tries to infer health
# Historical errors produce false positives.
# Move to diagnosis.tests instead.
- name: "recent_errors"
  type: log_scan
  path: "~/.hermes/logs/gateway.log"
  passes_if: "no ERROR|CRITICAL patterns"
```

## Why

Logs are a diagnosis tool, not a health probe. A log entry saying "ERROR" from
15 hours ago doesn't mean the service is unhealthy right now. Every probe must
test something you can physically observe at the moment it runs:

| Observable | How to test | Gotcha |
|---|---|---|
| Is the process running? | `launchctl list` / `ps aux` | — |
| Did the process exit cleanly last time? | `launchctl list → LastExitStatus` | — |
| Does the HTTP endpoint respond? | `urllib.request → status code` | ⚠️ **SSH tunnels** on the same port intercept the probe — it checks the remote service, not local |
| | | ⚠️ **Wrong path** — `/api/v1/health` may be `/health`. Always curl-test the exact probe URL |
| | | ⚠️ **Interface binding** — service may listen on Tailscale IP, not `localhost`. Use `lsof -i :PORT` to check |
| | | ⚠️ **SSE/streaming endpoints** (like MCP `/mcp`) reject plain HTTP GET with 406. Use `/` or `/health` instead |
| Is the Docker container up? | `docker inspect → State.Running` | — |
| Is disk space available? | `df -h → percentage` | — |
| Does DNS resolve? | `host example.com → has address` | — |
| Is the cron scheduler active? | `hermes cron list → Next run` | — |
| **What's actually listening on a port?** | `lsof -iTCP -sTCP:LISTEN -P -n` | Essential diagnostic when an HTTP probe shows inconsistent results |

## What about logs?

Log analysis moves to `diagnosis.tests` — the heartbeat reads them when
triaging a failure, but the probe itself should never depend on log content
for its pass/fail decision.

```yaml
health_probes:
  - name: "process_running"
    # concrete — checks current state

diagnosis:
  tests:
    - "tail -30 ~/.hermes/logs/gateway.log"
    # logs are for diagnosis, not health checks
```

## Practical Probe Validation Workflow

When onboarding the watchdog on a new machine, or adding a new HTTP probe, follow
this validation workflow to catch the common pitfalls:

### Step 1: Check what's listening on the port

```bash
lsof -iTCP -sTCP:LISTEN -P -n | grep :8644
# python3.1  314  user   25u  IPv4 ... TCP *:8644 (LISTEN)      ← OK — listens on all interfaces
# python3.1  312  user    4u  IPv4 ... TCP 100.x.x.x:8787      ← Tailscale IP — NOT localhost
# ssh        932  user    ... 127.0.0.1:8888 (LISTEN)           ← SSH tunnel — probe checks remote
```

If the address is a specific IP (not `*` or `127.0.0.1`), the probe must use that IP
or the service must be reconfigured. If SSH is on the port, pair with a process-level probe
or relocate the tunnel.

### Step 2: Test the exact endpoint path

```bash
# Check multiple candidate paths
for path in / /health /api/v1/health /status; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:PORT$path" 2>&1)
  echo "$path → $code"
done
```

### Step 3: Check for content-type restrictions

Some endpoints are SSE-only or WebSocket-only:

```bash
curl -s -D- http://localhost:8000/mcp 2>&1 | head -5
# → HTTP/1.1 406 Not Acceptable
# → {"error":{"message":"Not Acceptable: Client must accept text/event-stream"}}
```

If you see 406, the endpoint is not usable as an HTTP health probe. Find another
endpoint (`/` or `/health` on the same port) that returns JSON.

### Step 4: Cross-reference with process-level probe

An HTTP probe alone is insufficient — always pair it with a process check:

```yaml
health_probes:
  - name: "process_running"       # ← cross-reference
    type: process
    command: "launchctl list com.example.service"
    passes_if: '"PID" in out'
  - name: "http_responding"       # ← the actual endpoint check
    type: http
    url: "http://localhost:PORT/health"
    passes_if: "status == 200"
```

If the process is running but HTTP probe fails → probe misconfiguration (wrong path,
wrong interface, SSH tunnel, SSE). If both fail → genuine outage.

### Step 5: Don't use inequality in `passes_if`

The `evaluate_passes_if_expr()` function in `health-scan.py` only handles
`status == N` for HTTP probes. Expressions like `status < 500`, `status > 200`,
or `status != 404` all fall through to `return False`, and the probe is always
reported as failed. Use only exact status matches (`status == 200`) for HTTP probes.
If you need a range check, supplement with a `process` or `file_check` probe
that checks the same condition another way.
