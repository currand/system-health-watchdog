# Webhook `--deliver` Format (vs Cronjob `deliver`)

## The Problem

These two Hermes features use **different** delivery target formats, and mixing them up produces "Unknown deliver type" errors Using Discord as an example (but other delivery targets may vary):

| Feature | Parameter | Format | Example |
|---------|-----------|--------|---------|
| `hermes webhook subscribe` | `--deliver` + `--deliver-chat-id` | Platform name only, chat ID separate | `--deliver discord --deliver-chat-id 12345` |
| `hermes cron create` / cronjob tool `deliver` | `deliver` (single param) | Combined `platform:chat_id` | `discord:12345` |

## Mechanism

The webhook pipeline stores delivery info in `~/.hermes/webhook_subscriptions.json` as:
```json
{
  "deliver": "discord",
  "deliver_extra": {"chat_id": "12345"}
}
```

The cronjob tool's `deliver` parameter (`platform:chat_id`) is parsed internally — the colons split the platform from the chat ID. The webhook CLI does **not** parse colons; it stores `--deliver` literally, so `--deliver discord:12345` becomes `deliver: "discord:12345"` which the gateway rejects as an unknown platform.

## Symptoms

- Webhook POST succeeds (200), but the agent session's output goes nowhere
- Gateway log has: `[webhook] Unknown deliver type: discord:12345`
- The cron pre-scanner's failure JSON arrives in chat (because `no_agent=true` cron delivers to the home channel), but the triage agent never responds

## Fix

Re-create the webhook subscription with the correct two-parameter form:

```bash
hermes webhook remove system-health-alerts 2>/dev/null
hermes webhook subscribe system-health-alerts \
    --secret INSECURE_NO_AUTH \
    --deliver discord \
    --deliver-chat-id 12345 \
    --description "Health pre-scanner failures" \
    --skills system-health-watchdog
```

## Why `--deliver origin` Also Doesn't Work

Webhook-triggered sessions have **no conversation origin** — there's no Discord channel or Telegram group where the webhook "came from." Setting `--deliver origin` silently falls back to the Discord home channel (if one is configured), but this is accidental behavior, not a contract. Always set an explicit delivery target.

The cronjob tool's `deliver` parameter supports `origin` because cron jobs **do** have a conversation context (the chat where they were created). Webhooks don't.