#!/usr/bin/env python3
"""
Self-Healing Pre-Scanner — Stage 1 of the 2-stage pipeline.

Reads catalog.local.yaml (falls back to templates/catalog.default.yaml), runs
every service's health probes, and outputs either [SILENT] (all green) or a
JSON payload with failure details for Stage 2 (LLM heartbeat) to consume.

Platform-agnostic by design: all OS-specific commands live in the catalog,
not this script. HTTP health checks use Python stdlib (no `curl` commands)
to avoid tirith false positives. Process probes gracefully skip commands
that don't exist on the current platform so one catalog can serve macOS,
Linux, and Windows.

Usage:
    python3 scripts/health-scan.py
    python3 scripts/health-scan.py --catalog templates/catalog.default.yaml
    python3 scripts/health-scan.py --dry-run
"""

import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

# Resolve the skill directory (script is in <skill>/scripts/)
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(SKILL_DIR, "state")  # inside skill dir, per repo structure in SKILL.md
STATE_FILE = os.path.join(STATE_DIR, "health_state.json")


# ── Platform Helpers ───────────────────────────────────────────────────────

def get_platform_info():
    """Return a dict describing the current platform.

    Keys: system (Darwin/Linux/Windows), release, machine, python_version.
    Useful for cross-platform catalog probing and downstream Stage 2
    decision-making.
    """
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
    }


def _first_command_token(shell_cmd):
    """Extract the first real binary name from a shell command string.

    Strips common shell setup prefixes (cd, export, etc.), then returns
    the first token that might be a binary. Returns None if the command
    is clearly a shell builtin-only chain.
    """
    cmd = shell_cmd.strip()

    # Strip chained execution prefixes
    for prefix in ("cd ", "export ", "source ", ". "):
        while cmd.startswith(prefix):
            # Skip to next command after `&&` or `;`
            rest = cmd[len(prefix):]
            for sep in ("&&", ";"):
                idx = rest.find(sep)
                if idx != -1:
                    rest = rest[idx + len(sep):].lstrip()
            cmd = rest.strip()

    # Take the first word before any pipe, redirect, or space
    token = cmd.split(" ")[0].split("|")[0].split(">")[0].strip()
    if not token:
        return None
    # Skip shell builtins and special chars
    if token in ("cd", "export", "source", ".", ":", "echo", "test", "exit", "true", "false"):
        return None
    if token.startswith(("$", "<", '"', "'", "-")):
        return None
    return token


def command_available(shell_cmd):
    """Check if the first real binary in a shell command exists on PATH.

    Returns True if unavailable — don't crash, just gracefully report the
    probe as a platform-skip. This lets one catalog serve macOS, Linux,
    and Windows without probe failures for non-native commands.
    """
    token = _first_command_token(shell_cmd)
    if token is None:
        return True  # can't determine, let it run
    return shutil.which(token) is not None


# ── Probe Runners ──────────────────────────────────────────────────────────

def run_probe_process(probe):
    """Run a shell command and check its output against passes_if.

    If the binary isn't available on this platform, the probe is gracefully
    skipped rather than reported as a health failure — enabling cross-platform
    catalogs with OS-specific probes side by side.
    """
    cmd = probe["command"]
    if not command_available(cmd):
        token = _first_command_token(cmd) or "(unknown)"
        return {
            "passed": True,
            "status": "skipped",
            "output": f"skipped — '{token}' not found on {platform.system()}",
            "error": None,
        }
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=15
        )
        out = result.stdout.strip()
    except subprocess.TimeoutExpired:
        return {"passed": False, "status": "failed", "output": "(timeout)", "error": "command timed out"}

    passed = evaluate_passes_if(out, probe["passes_if"])
    return {
        "passed": passed,
        "status": "passed" if passed else "failed",
        "output": out[:500] if len(out) > 500 else out,
        "error": result.stderr[:200] if result.stderr else None,
    }


def run_probe_http(probe):
    """Make an HTTP GET request (Python stdlib, no curl) and check response."""
    url = probe["url"]
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")[:500]
    except urllib.error.HTTPError as e:
        status = e.code
        body = str(e)
    except (urllib.error.URLError, socket.timeout, ConnectionRefusedError) as e:
        return {"passed": False, "status": "failed", "output": str(e), "error": "connection failed"}

    passes_if = probe["passes_if"]
    ctx = {"status": status, "body": body}
    passed = evaluate_passes_if_expr(passes_if, ctx)
    return {
        "passed": passed,
        "status": "passed" if passed else "failed",
        "output": f"HTTP {status}" if passed else f"HTTP {status}: {body[:200]}",
        "error": None if passed else f"expected {passes_if}, got status={status}",
    }


def _parse_since(since_str):
    """Parse 'since' field like '15m', '1h', '2h30m' into minutes.

    Returns int (minutes) or None if not parseable.
    """
    if not since_str or not isinstance(since_str, str):
        return None
    since_str = since_str.strip()
    total = 0
    m = re.match(r"(?:(\d+)h)?\s*(?:(\d+)m)?", since_str)
    if m:
        hours = int(m.group(1)) if m.group(1) else 0
        mins = int(m.group(2)) if m.group(2) else 0
        total = hours * 60 + mins
    if total <= 0:
        # Try plain number as minutes
        try:
            total = int(since_str.rstrip("m"))
        except ValueError:
            return None
    return total if total > 0 else None


_LOG_TIMESTAMP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})"
)


def _ts_to_epoch(line):
    """Try to extract a YYYY-MM-DD HH:MM:SS prefix and convert to epoch.

    Supports both '2026-05-28 01:10:39' and '2026-05-28T01:10:39' formats.
    Returns float epoch or None if no parseable timestamp found.
    """
    m = _LOG_TIMESTAMP_RE.match(line)
    if not m:
        return None
    timestr = f"{m.group(1)} {m.group(2)}"
    try:
        return time.mktime(time.strptime(timestr, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def run_probe_log_scan(probe):
    """Scan a log file for error patterns, filtering by time when possible.

    Uses the 'since' field from the probe config (e.g. '15m', '1h') to
    filter lines by their YYYY-MM-DD HH:MM:SS timestamp prefix. Falls back
    to scanning the last 500 lines when:
      - No 'since' field is specified
      - Log lines lack parseable timestamp prefixes
    """
    path = os.path.expanduser(probe.get("path", ""))
    passes_if = probe["passes_if"]
    since_raw = probe.get("since", "")

    if not os.path.exists(path):
        return {"passed": False, "output": f"log not found: {path}", "error": "file not found"}

    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
    except PermissionError:
        return {"passed": False, "output": "permission denied", "error": "permission denied"}

    since_minutes = _parse_since(since_raw)

    if since_minutes is not None:
        # Try time-based filtering first
        now = time.time()
        cutoff = now - (since_minutes * 60)
        timed_lines = []
        untimed_lines = []

        for line in lines:
            ts = _ts_to_epoch(line)
            if ts is not None:
                if ts >= cutoff:
                    timed_lines.append(line)
            else:
                untimed_lines.append(line)

        scan_lines = timed_lines if timed_lines else lines[-500:]
        # Note: untimed (no-timestamp) lines within the scan window
        # are included as they may appear between timestamped lines.
        # Fallback is only triggered when NO lines have timestamps at all.
        if not timed_lines and untimed_lines:
            scan_lines = untimed_lines[-500:]
    else:
        scan_lines = lines[-500:]

    # "no ERROR|TRACEBACK" means assert absence
    if passes_if.startswith("no "):
        pattern = passes_if[3:].strip()
        matches = [l for l in scan_lines if re.search(pattern, l)]
        passed = len(matches) == 0
        return {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "output": f"scanned {len(scan_lines)} lines, 0 matches"
                       if passed else
                       f"scanned {len(scan_lines)} lines, {len(matches)} matches: {matches[-3:]}",
            "error": None if passed else f"found {len(matches)} occurrences of '{pattern}' in {len(scan_lines)} recent lines",
        }
    else:
        pattern = passes_if
        matches = [l for l in scan_lines if re.search(pattern, l)]
        passed = len(matches) > 0
        return {
            "passed": passed,
            "status": "passed" if passed else "failed",
            "output": f"scanned {len(scan_lines)} lines, {len(matches)} matches"
                       if passed else
                       f"scanned {len(scan_lines)} lines, 0 matches for '{pattern}'",
            "error": None if passed else f"expected to find '{pattern}' in {len(scan_lines)} lines",
        }


def run_probe_file_check(probe):
    """Check file existence, size, or age."""
    path = os.path.expanduser(probe.get("path", ""))
    passes_if = probe["passes_if"]

    if not os.path.exists(path):
        return {"passed": False, "status": "failed", "output": "file not found", "error": "file not found"}

    stat = os.stat(path)
    ctx = {
        "exists": True,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "age_seconds": time.time() - stat.st_mtime,
    }
    passed = evaluate_passes_if_expr(passes_if, ctx)
    return {
        "passed": passed,
        "status": "passed" if passed else "failed",
        "output": f"size={stat.st_size}, age={time.time()-stat.st_mtime:.0f}s",
        "error": None if passed else f"expected '{passes_if}'",
    }


# ── Expression Evaluator ───────────────────────────────────────────────────

PROBE_RUNNERS = {
    "process": run_probe_process,
    "http": run_probe_http,
    "log_scan": run_probe_log_scan,
    "file_check": run_probe_file_check,
}


def evaluate_passes_if(out, expr):
    """Simple DSL for passes_if expressions on string output.

    Supported patterns:
      '"PID" in out'              -> substring match
      'int(out) >= 1'             -> numeric comparison (>=, <=, >, <, ==, !=)
      'len(lines) >= 1'           -> line count comparison
      '"LastExitStatus" = 0'      -> fallback: checks quoted strings in output
      'status == 200'             -> HTTP status check (delegated to HTTP runner)
    """
    expr = expr.strip()

    m = re.search(r'"(.+?)"\s+in\s+out', expr)
    if m:
        return m.group(1) in out

    m = re.search(r'int\(out\)\s*([><=!]+)\s*(\d+)', expr)
    if m:
        try:
            val = int(out.strip())
        except ValueError:
            return False
        op, num = m.group(1), int(m.group(2))
        if op == ">=": return val >= num
        if op == "<=": return val <= num
        if op == ">":  return val > num
        if op == "<":  return val < num
        if op == "==": return val == num
        if op == "!=": return val != num

    m = re.search(r'len\(lines\)\s*([><=!]+)\s*(\d+)', expr)
    if m:
        lines = out.split("\n") if out else []
        val = len(lines)
        op, num = m.group(1), int(m.group(2))
        if op == ">=": return val >= num
        if op == "<=": return val <= num
        if op == ">":  return val > num
        if op == "<":  return val < num
        if op == "==": return val == num
        if op == "!=": return val != num

    m = re.search(r'len\(out\.strip\(\)\)\s*([><=!]+)\s*(\d+)', expr)
    if m:
        val = len(out.strip())
        op, num = m.group(1), int(m.group(2))
        if op == ">=": return val >= num
        if op == "<=": return val <= num
        if op == ">":  return val > num
        if op == "<":  return val < num
        if op == "==": return val == num
        if op == "!=": return val != num

    # Fallback: extract all quoted strings and check they appear in output
    quoted = re.findall(r'"([^"]+)"', expr)
    if len(quoted) >= 1:
        return all(q in out for q in quoted)

    return expr in out


def evaluate_passes_if_expr(expr, ctx):
    """Evaluate passes_if expressions against a context dict.

    Used by HTTP and file_check probes where 'out' string is not available.
    """
    expr = expr.strip()

    m = re.search(r'status\s*([><=!]+)\s*(\d+)', expr)
    if m:
        status = ctx.get("status")
        op, num = m.group(1), int(m.group(2))
        if op == "==": return status == num
        if op == "!=": return status != num
        if op == ">=": return status >= num
        if op == "<=": return status <= num
        if op == ">": return status > num
        if op == "<": return status < num

    if expr == "exists":
        return ctx.get("exists", False)

    m = re.search(r'size\s*>\s*(\d+)', expr)
    if m:
        return ctx.get("size", 0) > int(m.group(1))

    m = re.search(r'age_seconds\s*<\s*(\d+)', expr)
    if m:
        return ctx.get("age_seconds", 999999) < int(m.group(1))

    return False


# ── State Persistence (Error Fingerprint Deduplication) ────────────────────

def _ensure_state_dir():
    """Create state directory if it doesn't exist."""
    os.makedirs(STATE_DIR, exist_ok=True)


def load_state():
    """Load the health_state.json fingerprint database.

    Returns dict with 'fingerprints' key, or empty state dict.
    """
    _ensure_state_dir()
    if not os.path.exists(STATE_FILE):
        return {"fingerprints": {}, "last_scan_time": None, "version": 1}
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, OSError):
        return {"fingerprints": {}, "last_scan_time": None, "version": 1}


def save_state(state):
    """Persist state dict to health_state.json."""
    _ensure_state_dir()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fingerprint_error(service_name, error_text):
    """Generate a stable SHA256 fingerprint for an error.

    Uses first 200 chars of error text + service name.
    Same error on same service = same fingerprint across runs.
    """
    import hashlib
    raw = f"{error_text[:200]}:{service_name}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def update_state_with_findings(state, results, failed_services, scan_time):
    """Update fingerprint state from scan results.

    Mark fingerprints as 'seen' with a retry counter for
    already-observed failures.
    """
    now_epoch = time.time()
    fps = state.setdefault("fingerprints", {})

    for svc_name in failed_services:
        svc_result = results.get(svc_name, {})
        for probe in svc_result.get("probes", []):
            if probe.get("passed", True):
                continue
            error_text = probe.get("error") or probe.get("output", "")
            fp = fingerprint_error(svc_name, error_text)
            if fp in fps:
                fps[fp]["seen_count"] += 1
                fps[fp]["last_seen"] = now_epoch
            else:
                fps[fp] = {
                    "service": svc_name,
                    "probe": probe["name"],
                    "error_snippet": error_text[:100],
                    "first_seen": now_epoch,
                    "last_seen": now_epoch,
                    "seen_count": 1,
                }
            # Prune old fingerprints (>7 days stale)
    cutoff = now_epoch - (7 * 86400)
    stale = [k for k, v in fps.items() if v.get("last_seen", 0) < cutoff]
    for k in stale:
        del fps[k]

    state["last_scan_time"] = scan_time
    save_state(state)


# ── Main Scan Logic ────────────────────────────────────────────────────────

def load_catalog(catalog_path=None):
    """Load service catalog from YAML file."""
    import yaml

    if catalog_path is None:
        local = os.path.join(SKILL_DIR, "catalog.local.yaml")
        template = os.path.join(SKILL_DIR, "templates", "catalog.default.yaml")
        catalog_path = local if os.path.exists(local) else template

    if not os.path.exists(catalog_path):
        print(f"[ERROR] Catalog not found: {catalog_path}")
        sys.exit(1)

    with open(catalog_path) as f:
        return yaml.safe_load(f)


def run_health_scan(services, dry_run):
    """Run all health probes and return aggregated results."""
    results = {}
    failed_services = []

    for svc_name, svc_config in services.items():
        probes = svc_config.get("health_probes", [])
        if not probes:
            continue

        svc_result = {
            "name": svc_config.get("name", svc_name),
            "description": svc_config.get("description", ""),
            "probes": [],
            "passed": True,
        }

        for probe in probes:
            probe_name = probe.get("name", "unnamed")
            probe_type = probe.get("type", "process")
            runner = PROBE_RUNNERS.get(probe_type)

            if not runner:
                svc_result["probes"].append({
                    "name": probe_name,
                    "status": "failed",
                    "passed": False,
                    "output": f"unknown probe type: {probe_type}",
                })
                svc_result["passed"] = False
                continue

            probe_result = runner(probe)

            probe_entry = {
                "name": probe_name,
                "type": probe_type,
                "status": probe_result.get("status", "passed" if probe_result["passed"] else "failed"),
                "passed": probe_result["passed"],
                "output": probe_result["output"],
                "error": probe_result.get("error"),
            }
            svc_result["probes"].append(probe_entry)

            if not probe_result["passed"]:
                svc_result["passed"] = False

        results[svc_name] = svc_result
        if not svc_result["passed"]:
            failed_services.append(svc_name)

    return results, failed_services


def rotate_large_logs(log_dir=None):
    """Safely truncate oversized logs (no rm -rf, no shell commands).

    Args:
        log_dir: Path to scan for log files. Defaults to ~/.hermes/logs.
    """
    if log_dir is None:
        log_dir = os.path.expanduser("~/.hermes/logs")
    if not os.path.exists(log_dir):
        return 0

    rotated = 0
    for fname in os.listdir(log_dir):
        fpath = os.path.join(log_dir, fname)
        if not os.path.isfile(fpath) or not fname.endswith(".log"):
            continue
        size = os.path.getsize(fpath)
        if size > 50 * 1024 * 1024:
            try:
                with open(fpath, "r", errors="replace") as f:
                    lines = f.readlines()
                with open(fpath, "w") as f:
                    f.writelines(lines[-5000:])
                rotated += 1
            except (OSError, PermissionError):
                pass
    return rotated


# ── Webhook Callback ──────────────────────────────────────────────────────────


def _fire_webhook(url, payload, secret=""):
    """POST the failure payload as JSON to the webhook URL with HMAC-SHA256.

    Accepts any 2xx response (the Hermes webhook adapter returns 202).
    Silently ignores empty/missing URLs. Logs failures to stderr so they
    show up in cron logs but never interfere with the [SILENT] protocol.
    """
    if not url:
        return
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={sig}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                print(f"webhook returned {resp.status}", file=sys.stderr)
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, OSError) as e:
        print(f"webhook failed: {e}", file=sys.stderr)


# ── Entry Point ────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Self-Healing Pre-Scanner")
    parser.add_argument("--catalog", help="Path to catalog file")
    parser.add_argument("--dry-run", action="store_true", help="Override: force dry-run")
    parser.add_argument("--log-dir", help="Override log directory (default: ~/.hermes/logs)")
    parser.add_argument("--webhook-url", help="URL to POST failure JSON to (or HEALTH_WEBHOOK_URL env)")
    parser.add_argument("--webhook-secret", help="HMAC-SHA256 secret for webhook auth (or WEBHOOK_SECRET env)")
    args = parser.parse_args()

    # Resolve webhook URL: CLI arg > env var > disabled
    webhook_url = args.webhook_url or os.environ.get("HEALTH_WEBHOOK_URL") or ""
    webhook_secret = args.webhook_secret or os.environ.get("WEBHOOK_SECRET") or ""

    try:
        catalog = load_catalog(args.catalog)
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "error": f"Failed to load catalog: {e}",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }))
        sys.exit(1)

    global_config = catalog.get("global", {})
    services = catalog.get("services", {})

    dry_run = args.dry_run or global_config.get("dry_run", True)
    report_mode = global_config.get("report_mode", "summary")

    # Resolve log directory: CLI arg > global config > default
    log_dir = args.log_dir or global_config.get("log_dir")
    if log_dir:
        log_dir = os.path.expanduser(log_dir)

    results, failed_services = run_health_scan(services, dry_run)
    # Log rotation is deliberately NOT an automatic side-effect.
    # The pre-scanner observes state only. If you want rotation, run
    # rotate_large_logs() explicitly or wire a separate cron job.
    rotated = 0

    scan_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Persist error fingerprints to state file for deduplication
    state = load_state()
    update_state_with_findings(state, results, failed_services, scan_time)

    # Build summary results for non-verbose mode
    if report_mode != "verbose":
        summary_results = {
            s: {
                "name": results[s]["name"],
                "passed": results[s]["passed"],
                "failed_probes": [
                    p["name"] for p in results[s]["probes"] if not p["passed"]
                ],
            }
            for s in failed_services
        }
    else:
        summary_results = results

    # Include platform info so Stage 2 can make OS-aware decisions
    platform_info = get_platform_info()

    # Also pass fingerprint context so Stage 2 knows which errors are new
    state = load_state()  # re-read after update
    fps = state.get("fingerprints", {})
    seen_fingerprints = {}
    for svc_name in failed_services:
        svc_result = results.get(svc_name, {})
        for probe in svc_result.get("probes", []):
            if probe.get("passed", True):
                continue
            error_text = probe.get("error") or probe.get("output", "")
            fp = fingerprint_error(svc_name, error_text)
            entry = fps.get(fp, {})
            if entry.get("seen_count", 0) > 1:
                seen_fingerprints[fp] = entry["seen_count"]

    scan_result = {
        "status": "ok" if not failed_services else "failures",
        "timestamp": scan_time,
        "platform": platform_info,
        "dry_run": dry_run,
        "report_mode": report_mode,
        "total_services": len(services),
        "passed": len(services) - len(failed_services),
        "failed": len(failed_services),
        "failed_services": failed_services,
        "logs_rotated": rotated,
        "results": summary_results,
        "state": {
            "fingerprint_count": len(seen_fingerprints),
            "seen_before_fingerprints": seen_fingerprints,
        } if seen_fingerprints else {},
    }

    if not failed_services:
        if rotated > 0 and report_mode != "silent":
            print(json.dumps({"status": "ok", "note": f"{rotated} log(s) rotated", "timestamp": scan_time}))
        else:
            print("[SILENT]")
    else:
        print(json.dumps(scan_result, indent=2))
        _fire_webhook(webhook_url, scan_result, webhook_secret)


if __name__ == "__main__":
    main()
