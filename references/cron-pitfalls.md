# Cron / launchd Pitfalls

Reference for debugging cron-launched Hermes jobs and wrapper scripts. Load when a cron job fails to start, can't find files, or has mysterious environment behavior.

## `${HOME}` Is Unreliable

launchd (macOS) does **not** set `HOME` the way an interactive shell does. It may be unset, resolve to a wrong path, or reflect the launchd context instead of the user's home.

**Wrong:**
```bash
SKILL_DIR="${HOME}/.hermes/skills/devops/system-health-watchdog"
```

**Right — derive from script location:**
```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="$(cd "${SCRIPT_DIR}/.." && pwd)"
SKILL_DIR="${HERMES_HOME}/skills/devops/system-health-watchdog"
```

This works identically in interactive shells, `launchd`, and manual runs.

## Python: System vs. Venv

`/usr/bin/python3` (Command Line Tools Python) has **no** PyYAML, `requests`, or any third-party package. Never hardcode it in wrapper scripts.

**Wrong:**
```bash
PYTHON="/usr/bin/python3"
```

**Right — resolve from known venv location:**
```bash
HERMES_AGENT_DIR="${HERMES_HOME}/hermes-agent"
if [ -x "${HERMES_AGENT_DIR}/venv/bin/python3" ]; then
    PYTHON="${HERMES_AGENT_DIR}/venv/bin/python3"
elif [ -x "${HERMES_AGENT_DIR}/.venv/bin/python3" ]; then
    PYTHON="${HERMES_AGENT_DIR}/.venv/bin/python3"
else
    echo "ERROR: no suitable Python found" >&2; exit 1
fi
```

## `PATH` Is Minimal

launchd (macOS) `PATH` is typically `/usr/bin:/bin:/usr/sbin:/sbin`. Homebrew (`/opt/homebrew/bin`) and `~/.local/bin` are absent. On Linux, systemd services also have restricted PATHs that lack common user binaries.

Always prepend expected paths in the wrapper — but do NOT hardcode a specific path. Use a pattern that resolves the Hermes venv and common tool locations relative to the user's home:

```bash
# Prepend common tool paths — adjust per platform
export PATH="${HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH}"
# macOS Homebrew
[ -d "/opt/homebrew/bin" ] && export PATH="/opt/homebrew/bin:${PATH}"
```

## `set -euo pipefail` Interactions

The pre-scanner wrapper uses `set -euo pipefail`. Any unset variable or non-zero exit in a pipeline aborts the script silently. This is correct for a cron job — but means variable typos (e.g. `$HOME` expanding to nothing) cause mysterious failures with no error message pointing to the right line.

**Debug tip:** temporarily add `set -x` at the top of the script and check `cron stderr` / `launchd` logs:
```bash
grep cron <(log show --predicate 'process == "cron"' --last 1h)
```

## `no_agent=true` Jobs: Stdout Is the Entire Output

For `no_agent=true` cron jobs, the script's stdout is delivered verbatim as the "agent response." There is no LLM post-processing.

Rules:
- Print `[SILENT]` (and nothing else) when healthy — cron job exits cleanly, zero tokens consumed
- Print valid JSON on failure — the webhook POST sends this JSON to the gateway, which triggers the agent
- Never print diagnostic messages to stdout — use stderr for debug output

## Wrapper Script Location

The `system-health-watchdog` skill's Python scripts live in the skill dir (`scripts/health-scan.py`), but the **shell wrapper** (`health-scan.sh`) lives in `~/.hermes/scripts/` so it can be called directly by the cron scheduler without path resolution issues.

If you move or rename `health-scan.sh`, update the cron job definition accordingly.
