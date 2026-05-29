# Probe Onboarding: HTTP Probe Validation Workflow

## The Workflow

When adding or vetting HTTP probes, follow this sequence every time. Skipping
a step guarantees a future dogfooding will find the same bugs again.

### Step 1: Verify the service process is actually running

```bash
# Launchd services
launchctl list <label>
# Expected: shows "PID" in output (not a dash)

# Unsupervised MCPs
ps aux | grep <service-name> | grep -v grep
# Expected: at least one line

# Docker containers
docker inspect <name> --format='{{.State.Running}}'
# Expected: "true"
```

If the process isn't running, the HTTP probe doesn't matter yet — fix the
process first. The probe can't distinguish "wrong URL" from "dead service",
so start with the process.

### Step 2: Find what's actually on the port

```bash
lsof -iTCP -sTCP:LISTEN -P -n | grep :<PORT>
```

**Read the address column.** Three outcomes:

| lsof shows | Meaning | Probe URL |
|-----------|---------|-----------|
| `*:<PORT>` or `0.0.0.0:<PORT>` | All interfaces | `http://localhost:PORT/path` ✅ |
| `127.0.0.1:<PORT>` or `localhost:<PORT>` | Loopback only | `http://localhost:PORT/path` ✅ |
| `100.x.x.x:<PORT>`, `192.168.x.x:<PORT>`, etc. | Specific interface | ❌ `localhost` won't work. Use the bound IP or reconfigure the service. |

### Step 3: Test the exact URL with curl

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:PORT/PATH
```

| Response | Meaning | Action |
|----------|---------|--------|
| `200` | Endpoint works. Use `status == 200` in the probe. | ✅ Done |
| `404` | Path doesn't exist. Find the real health endpoint. | Look for `/health`, `/api/health`, or check the service docs. |
| `406` | Content-type negotiation failure. | The endpoint expects SSE or a specific Accept header. Find a different endpoint (`/` or `/health` on the same port). |
| `000` (curl fails) | Nothing listening on `localhost:PORT`. | Go back to Step 2 — wrong interface. |

### Step 4: Cross-reference with process state

After the probe is written, pair it with a process-level probe. If the process
probe passes but the HTTP probe fails, it's a probe config bug (wrong URL,
wrong interface, or SSH tunnel on the port).

### Step 5: Run the full scanner

```bash
python3 scripts/health-scan.py --catalog catalog.local.yaml
# Expected: [SILENT] meaning all probes pass
```

If it shows failures, go back to Step 1-4. Don't assume the service is down.


## What a Healthy Scan Looks Like

```bash
$ python3 scripts/health-scan.py
[SILENT]
```

If you see JSON with failures, the scanner found a problem — but don't assume
the service is down. First check: is the probe itself correct?
