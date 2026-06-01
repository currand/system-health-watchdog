# Agent Guidance: Two-Context Pattern for Webhook Skills

A reusable pattern for any Hermes skill that can be triggered in **two distinct contexts**: automatically via webhook (failure triage) and on-demand by the user (setup, reconfig, repair).

## The Problem

Skills triggered by webhook subscriptions load the same SKILL.md as user-initiated sessions. Without explicit guidance, the agent wastes tokens loading reference docs intended for the wrong context. Example: loading a 22KB onboarding plan during a routine Docker container triage.

## The Pattern

Add an **Agent Guidance** section near the top of SKILL.md (after the reference table, before the main body) that splits usage into two explicit contexts:

### Context A — Automated / Webhook-Triggered

**You are here if:** This session was started by a webhook POST. The payload contains specific data (e.g., `failed_services`) for the agent to act on.

**Your job:** Process the payload — triage failures, run diagnosis, apply fixes, report.

**Reference loading rules:**
- Load only failure-specific reference docs matching the payload's failure types
- **DO NOT load** the onboarding/setup reference — it's large and irrelevant
- Use the SKILL.md body for operational rules (risk tiers, circuit breakers, safety)

### Context B — User-Initiated (Setup / Reconfig / Repair)

**You are here if:** The user directly asked you to set up, reconfigure, or fix the skill itself. There is no failure payload.

**Your job:** Onboard, configure, or repair the skill infrastructure.

**Reference loading rules:**
- Load the onboarding/configuration reference — it contains the complete plan
- Load individual references when specific phases call for them

## Implementation in SKILL.md

Place this section right after the reference table and before the `---` delimiter that separates the frontmatter content from the main body:

```markdown
## Agent Guidance: Reference Loading Contexts

### Context A — Failure Triage (webhook-triggered)

**You are here if:** ...

**Your job:** ...

**Reference loading rules:**
- ...
- **DO NOT load** ...

### Context B — Setup / Reconfig / Repair (user-initiated)

**You are here if:** ...

**Your job:** ...

**Reference loading rules:**
- ...
```

## Identifying the Context

The key question for the agent: **Is there a failure payload?**
- Yes → Context A
- No → Context B

For webhook-triggered skills, the webhook subscription's prompt template typically injects the payload as `{payload}`. The agent can check this to determine context.

## When to Use This Pattern

Apply this to any Hermes skill that:
1. Has a webhook subscription that auto-loads it on event
2. Can also be loaded by a user asking for setup/reconfig/repair
3. Has reference docs that are large and context-specific (not universally useful)

Do NOT use this pattern for skills that are:
- Only user-initiated (no webhook trigger)
- Only webhook-triggered (users never directly ask for it)
- Small enough that all references fit in the active token budget

## See Also

- The `system-health-watchdog` skill's SKILL.md for a real-world implementation of this pattern
- `hermes-agent-skill-authoring` for general skill authoring conventions
