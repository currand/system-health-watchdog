# Process Probe Debugging

Real-world patterns for debugging process probes that fail on first run.
Most pre-scanner first-run failures are probe config bugs, not service outages.

## The `--` Flag Trap (macOS BSD grep)

When a process command line contains arguments starting with `--` (e.g.
`--profile coach`), grep interprets them as its own option flags, not as
search patterns. This is a macOS BSD grep quirk that does NOT happen on GNU
grep (Linux).

### Symptom

```bash
$ ps aux | grep '--profile coach'
grep: unrecognized option `--profile coach'
```

### Fix: use `grep -e`

```bash
# GOOD — -e tells grep the next argument is a pattern, never an option
ps aux | grep -e '--profile coach'

# Also works with -F (fixed string):
ps aux | grep -F -e '--profile coach'
```

The same applies to `grep -v` (invert match):

```bash
# BAD — '--profile' parsed as grep option
ps aux | grep 'hermes_cli.main' | grep -v '--profile'

# GOOD
ps aux | grep 'hermes_cli.main' | grep -v -e '--profile'
```

### When to spot this

Any process probe where the `command` field contains a string starting with
`--`. Common patterns:

| Process has | Wrong probe | Right probe |
|------------|-------------|-------------|
| `--profile coach` | `grep '--profile'` | `grep -e '--profile'` |
| `--transport streamable-http` | `grep '--transport'` | `grep -e '--transport'` |
| `--port 8080` | `grep '--port'` | `grep -e '--port'` |

## Finding a Process by Port

When a service is running (you can `curl` it or `lsof` shows the port), but
`ps aux` doesn't find it because the process argv doesn't contain the name
you're searching for.

### Workflow

```bash
# Step 1: Find what process owns the port
lsof -iTCP -sTCP:LISTEN -P -n | grep ':8000'
# → python3.1  318  user  ... TCP *:8000 (LISTEN)

# Step 2: Get the full command line
ps -p 318 -o pid,command
# →  318 ./.venv/bin/python3 main.py --tools gmail calendar sheets docs --transport streamable-http

# Step 3: Build the right probe command
# The argv doesn't contain "google" — it's a relative path with no unique project name.
# Use the unique flag combination instead:
ps aux | grep 'main.py.*streamable-http' | grep -v grep
```

### Common patterns

| `lsof` shows | Probable cause | What to grep for |
|-------------|---------------|-----------------|
| Process name is `.venv/bin/python3` or similar generic | MCP server using `uv run` | The unique project directory or first meaningful argument |
| Nothing on the port | Service died; or bound to wrong interface | Check `lsof -i :PORT -P` without the TCP filter |
| `ssh` on the port | SSH tunnel forwarding to remote | Not a local service — need process probe for the SSH session, not the tunneled port |

## The Character Class Trick

Standard technique to avoid matching the `grep` process itself in `ps aux` output:

```bash
# Instead of:
ps aux | grep myprocess | grep -v grep

# Use:
ps aux | grep '[m]yprocess'

# The regex [m] matches 'm' but the grep command line shows '[m]yprocess'
# which doesn't match the regex, so grep's own line is excluded.
```

This is reliable and simpler than `grep -v grep`, which can miss lines if
`grep` is in the middle of a long command line.

## Verifying a Probe Before Adding to Catalog

Before writing a probe to `catalog.local.yaml`, test the exact command in the
shell:

```bash
# Test the probe command directly
ps aux | grep -e '--profile coach' | grep -E 'gateway|hermes_cli'
# Should show 1+ lines

# Count lines
ps aux | grep -e '--profile coach' | grep -E 'gateway|hermes_cli' | wc -l
# Should be >= 1

# Simulate what evaluate_passes_if does — does 'len(lines) >= 1' pass?
lines=$(ps aux | grep -e '--profile coach' | grep -E 'gateway|hermes_cli')
if [ "$(echo "$lines" | wc -l)" -ge 1 ]; then echo "PASS"; else echo "FAIL"; fi
```

## Cross-Reference: Process + HTTP

Every HTTP probe should be paired with a process probe:

| Probe A (process) | Probe B (HTTP) | Diagnosis |
|------------------|----------------|-----------|
| PASS | PASS | ✅ Service healthy |
| PASS | FAIL | ❌ Probe misconfig — wrong URL, wrong interface, SSE endpoint |
| FAIL | PASS | 🚨 Wrong process — something else is on that port |
| FAIL | FAIL | 🔴 Genuine outage |

If A passes and B fails, do NOT restart the service — the process is fine,
the probe configuration is wrong. Run Steps 1-3 above to find the real
endpoint.
