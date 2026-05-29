# Template: Adding a Service to the Catalog

Copy this into `catalog.local.yaml` and fill in your values.

## Health Probes

A service needs at least one health probe. Each probe is a unit test for "good":

`process` — Check that a process is running
`http` — Check that an HTTP endpoint responds
`log_scan` — Check that a log file has no recent errors
`command` — Run an arbitrary command and check output

```yaml
services:
  your-service-name:
    name: "Human Readable Name"
    description: "What this service does"
    
    # Dependencies — these services must be healthy first
    depends_on: []
    
    health_probes:
      - name: "description_of_probe"
        type: process
        command: "launchctl list com.example.label"
        passes_if: '"PID" in out'
      
      - name: "api_responding"
        type: http
        url: "http://localhost:8080/health"
        passes_if: "status == 200"
        skip_if: "process_running fails"
      
      - name: "no_recent_errors"
        type: log_scan
        path: "~/.hermes/logs/service-name.log"
        since: "15m"
        passes_if: "no ERROR|CRITICAL patterns"
        skip_if: "process_running fails"
    
    # Diagnosis — what to check when a probe fails
    diagnosis:
      hypothesis_template: "Service {name} is {status} because {likely_cause}"
      tests:
        - "tail -30 ~/.hermes/logs/service-name.error.log"
        - "launchctl list com.example.label"
    
    # Fixes — at least one per risk tier you want
    fixes:
      - name: "restart-service"
        risk: safe
        max_retries: 3
        action: "launchctl kickstart system/com.example.label"
        verify: "launchctl list com.example.label | grep PID"
      
      - name: "full-reload"
        risk: caution
        max_retries: 1
        action: "launchctl bootout system/com.example.label && launchctl bootstrap system /Library/LaunchDaemons/com.example.plist"
        verify: "launchctl list com.example.label"
      
      - name: "config-change"
        risk: hands_off
        max_retries: 0
        action: ""
```

## Risk Tier Reference

| Risk Level | Auto-apply? | Example | Notes |
|------------|------------|---------|-------|
| `safe` | ✅ Auto-apply, report after | Process restart, service kickstart | Must pass tirith + cron_mode: deny |
| `caution` | 🔧 Apply + report | Sudo-level restart, config reload | Report what happened and the result |
| `hands_off` | 🔴 Report only | Config edits, env var changes, secrets | Explain why it can't be auto-fixed |

## What NOT to put in fixes

Commands that will be blocked by `cron_mode: deny`:
- `sudo ...` — flagged by built-in pattern detection
- `curl | bash`, `python -c "..."` — source→sink, blocked by tirith
- `chmod 777`, `mkfs`, `dd` — destructive patterns
- `rm -rf /` — hard-blocked
- Reading/modifying `.env` — tirith may flag as credential access

For health checks that need HTTP: use Python `urllib.request` in the pre-scanner script, not `curl` in a terminal command. The pre-scanner (Stage 1, no_agent) runs outside tirith's scope.
