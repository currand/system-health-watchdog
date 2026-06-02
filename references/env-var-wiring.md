# Env Var Wiring

The pre-scanner chain reads the webhook HMAC secret from `WEBHOOK_SECRET` env var.

## Chain

```
health-scan.sh (shell wrapper)
  → sources .env (WEBHOOK_SECRET must be set)
  → calls health-scan.py

health-scan.py (Python pre-scanner)
  → reads WEBHOOK_SECRET from os.environ
  → uses it to sign HMAC-SHA256 on webhook POSTs
  → POSTs to localhost:<GATEWAY_PORT>/webhooks/system-health-alerts  # default :8644

Gateway (default profile)
  → reads signature from X-Hub-Signature-256 header
  → verifies against subscription secret in webhook_subscriptions.json
  → if match: fires LLM session with system-health-watchdog skill
```

## Preferred: Loopback-Only (No Secret Required)

When the gateway binds to **127.0.0.1** (default for most profiles), nothing external can reach it. In that case, skip HMAC validation entirely:

```bash
hermes webhook subscribe system-health-alerts \
    --secret INSECURE_NO_AUTH \
    --deliver origin \
    --description "Health pre-scanner failures" \
    --skills system-health-watchdog
```

This is the preferred pattern for local-only webhooks because:
- No shared secret to keep in sync between `.env` and `webhook_subscriptions.json`
- No drift risk (silent 401s that silently skip triage)
- No security downgrade — the gateway is unreachable from outside the machine

The pre-scanner still signs its POSTs with `WEBHOOK_SECRET` from `.env` — the gateway just doesn't validate the signature when `secret: INSECURE_NO_AUTH`.

## Fallback: External Gateway (HMAC-Protected)

If the gateway binds to a non-loopback address (e.g. `0.0.0.0` for LAN access), HMAC validation is required. The secret must match in two places:

1. **`~/.hermes/.env`** — `WEBHOOK_SECRET=<value>` (read by the pre-scanner script)
2. **`~/.hermes/webhook_subscriptions.json`** — `system-health-alerts.secret: <value>` (read by the gateway to validate)

**Importantly, the gateway stores the secret as a literal string — there is no env var expansion** (the webhook handler reads `route_config.get("secret", ...)` directly from the JSON, line 386 of `gateway/platforms/webhook.py`). `${WEBHOOK_SECRET}` in the subscription JSON would be treated as the literal string, not expanded.

The config.yaml `webhook.extra.secret` is only a fallback for subscriptions that don't specify their own `secret` field — not a required location for dynamic subscriptions.

## Secret Drift (The Silent Failure)

If the two values differ, the gateway returns **401 Unauthorized** and the pre-scanner logs `Invalid signature for route system-health-alerts` to **agent.log**. The stdout (the failure JSON) still gets delivered to the chat via the cron job's `deliver: origin`, so you see the problem report but no triage session fires. The webhook failure is silently swallowed.

**Diagnosis:** Check the gateway log for `Invalid signature`:
```
grep 'Invalid signature.*system-health' ~/.hermes/logs/agent.log
```

**Fix (external gateway):** Remove and re-create the subscription with the matching secret. Use shell expansion from `.env` — never hardcode the value in a command or display it in chat:

```bash
source ~/.hermes/.env 2>/dev/null || true
hermes webhook remove system-health-alerts 2>/dev/null
hermes webhook subscribe system-health-alerts \
    --secret "$WEBHOOK_SECRET" \
    --deliver origin \
    --description "Health pre-scanner failures" \
    --skills system-health-watchdog
```

**Security:** The `--secret "$WEBHOOK_SECRET"` form reads from the environment variable at shell expansion time — the literal value goes into `webhook_subscriptions.json` (required for the gateway to validate HMACs), but is never displayed in chat or logged in plaintext by the hermes CLI. Never hardcode a secret value in a command string or display it in a message.

## Script History

The original `health-scan.sh` had `HEALTH_WEBHOOK_SECRET` hardcoded with a fallback default. During standardization, this was consolidated to `WEBHOOK_SECRET` (the standard Hermes webhook env var name). The script now fails loudly with `ERROR: WEBHOOK_SECRET not set in .env` if the env var is missing.

If migrating an older setup: rename any `HEALTH_WEBHOOK_SECRET` or `DEFAULT_WEBHOOK_SECRET` entries in your `.env` and wrapper scripts to `WEBHOOK_SECRET`.

See `activity-new-ride-watchdog/references/profile-webhook-secrets.md` for the full convention across all profiles.
