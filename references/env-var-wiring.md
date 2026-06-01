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

## The Three-Way Match

The webhook HMAC secret must be the same value in all three places:

1. **`~/.hermes/.env`** — `WEBHOOK_SECRET=<value>`
2. **`~/.hermes/config.yaml`** — `webhook.extra.secret: <value>`
3. **`~/.hermes/webhook_subscriptions.json`** — `system-health-alerts.secret: <value>`

If any differs, the gateway returns **401 Unauthorized** and the pre-scanner logs `Webhook POST failed: HTTP Error 401`.

## Script History

The original `health-scan.sh` had `HEALTH_WEBHOOK_SECRET` hardcoded with a fallback default. During standardization, this was consolidated to `WEBHOOK_SECRET` (the standard Hermes webhook env var name). The script now fails loudly with `ERROR: WEBHOOK_SECRET not set in .env` if the env var is missing.

If migrating an older setup: rename any `HEALTH_WEBHOOK_SECRET` or `DEFAULT_WEBHOOK_SECRET` entries in your `.env` and wrapper scripts to `WEBHOOK_SECRET`.

See `activity-new-ride-watchdog/references/profile-webhook-secrets.md` for the full convention across all profiles.
