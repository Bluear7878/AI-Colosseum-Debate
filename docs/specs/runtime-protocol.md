# Runtime Protocol Specification

## Run Lifecycle

States:
- `pending`
- `planning`
- `debating`
- `awaiting_human_judge`
- `completed`
- `failed`

Allowed transitions:
- `pending -> planning`
- `planning -> awaiting_human_judge`
- `planning -> debating`
- `debating -> awaiting_human_judge`
- `debating -> completed`
- `planning -> completed`
- `* -> failed`

## Streaming API Contract

The `/runs/stream` endpoint emits Server-Sent Events with a stable `phase` field.

Primary phases:
- `init`
- `context`
- `planning`
- `agent_planning`
- `plan_ready`
- `plan_failed`
- `plans_ready`
- `human_required`
- `judge_decision`
- `debate_round`
- `agent_thinking`
- `agent_message`
- `round_skipped`
- `round_cancelled`
- `round_complete`
- `judging`
- `synthesizing_report`
- `complete`
- `cancelled`
- `error`

Wire-format shaping is centralized in `colosseum.api.sse`. Internal refactors must preserve event names and ordering unless the UI protocol is explicitly versioned.

## Context Bundle Rules

- Binary attachments are preserved in the bundle but omitted from text prompts.
- Prompt rendering may truncate fragments for budget control.
- Checksums are used for traceability, not for cryptographic trust guarantees.

## Human Judge Protocol

Actions:
- `request_round`
- `select_winner`
- `merge_plans`
- `request_revision`

Validation:
- `select_winner` requires at least one plan id.
- `merge_plans` requires at least two plan ids.
- `request_round` and `request_revision` may carry free-form instructions.

## Quota and Fallback Rules

- Paid provider selection can be blocked before execution.
- Runtime exhaustion may fail, switch to free fallback, or wait for reset depending on `PaidProviderPolicy`.
- Runtime events are appended to the run artifact for auditability.
