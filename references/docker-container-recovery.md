# Docker Container Recovery Diagnosis

Patterns for diagnosing Docker containers that fail pre-scanner probes but
recover before the heartbeat runs.

## The Timing Window Problem

Docker containers with `restart: unless-stopped` or `restart: always` can
exit and auto-restart between the pre-scanner and heartbeat stages. The
pre-scanner captures a point-in-time snapshot; if the container was briefly
down, the probe fails. By the time the heartbeat runs (up to 15 minutes
later), Docker has already recovered it.

### Symptom

Pre-scanner reports:
```json
{
  "service": "my-container",
  "failed_probes": ["container_running", "docker_socket"],
  "fingerprint": "error: no such object: <container>"
}
```

But when the heartbeat checks:
```bash
docker inspect <container> --format '{{.State.Running}}'
# → true
docker ps --filter name=<container> --format '{{.Names}} {{.Status}}'
# → <container> Up X minutes
```

### Diagnosis Workflow

```
1. Check if container exists and is running:
   docker inspect <container> --format '{{.State.Running}}'
   → true / false

2. Get timing and restart info:
   docker inspect <container> --format \
     '{{.State.StartedAt}} {{.State.FinishedAt}} {{.RestartCount}}'
   → "2026-05-28T19:07:28Z 0001-01-01T00:00:00Z 0"
     StartedAt = when it started
     FinishedAt = 0001-01-01 (zero value) = never stopped
     RestartCount = 0 = never restarted since creation

3. Get current status:
   docker ps --filter name=<container> --format '{{.Names}} {{.Status}}'
   → "<container> Up 8 minutes"

4. Check logs for health:
   docker logs <container> --tail 20
   → Active worker logs, no crash traces

5. Cross-reference with scan time:
   Pre-scanner ran at 19:03
   Container StartedAt = 19:07
   → Container didn't exist at scan time, started 4 min later
   → Docker's restart policy already handled it
```

### Key Inferences

| Observation | Conclusion |
|-------------|-----------|
| Container running, RestartCount=0, StartedAt *after* scan time | Container freshly started/created after scan — self-healed |
| Container running, RestartCount>0, StartedAt after scan time | Docker restarted it between scan and heartbeat — self-healed |
| Container running, RestartCount=0, StartedAt *before* scan time | Probe was wrong (naming mismatch, docker socket issue) — fix the probe |
| Container NOT running, RestartCount>0 | Container keeps crashing — Docker restart policy failing — escalate |
| Container NOT running, RestartCount=0 | Container never started or was stopped — needs `docker compose up -d` |

### Do NOT Restart

If the container is running and producing logs, DO NOT:
- `docker compose up -d` — this force-recreates the container, dropping in-flight memory operations
- `docker restart <container>` — same issue, kills the healthy process
- `docker start <container>` — fails if container already exists with that name

Instead, re-run `health-scan.py` to confirm `[SILENT]`, then report the self-heal.

### When to Actually Restart

Only restart when diagnosis confirms the container is genuinely down:
```bash
docker inspect <container> --format '{{.State.Running}}'
# → false
docker inspect <container> --format '{{.State.Status}}'
# → "exited" or "created" (not "running")
```

In that case, the fix from the catalog (typically `docker compose up -d` in
the project directory) is appropriate, with `risk: caution` tier because it
affects a running application.
