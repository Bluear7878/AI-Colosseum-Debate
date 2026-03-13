# Architecture Overview

## Purpose

Colosseum is a provider-neutral orchestration runtime for running the same task through multiple agents, freezing a shared context bundle, and producing a traceable verdict through bounded debate.

This document is the canonical architectural overview. Product framing lives in [`README.md`](../../README.md); implementation contracts live under [`docs/specs/`](../specs/).

## Layered Model

1. Interface layer
- `colosseum.main`: FastAPI app factory and static asset mounting.
- `colosseum.api.*`: HTTP routes, streaming protocol, persona APIs, quota APIs.
- `colosseum.cli`: Terminal workflows and live debate execution UX.

2. Application layer
- `colosseum.services.orchestrator.ColosseumOrchestrator`: run lifecycle composition.
- `colosseum.services.debate.DebateEngine`: round execution and prompt assembly.
- `colosseum.services.judge.JudgeService`: plan scoring, agenda selection, adjudication, verdicts.
- `colosseum.services.report_synthesizer.ReportSynthesizer`: final report generation.

3. Domain model layer
- `colosseum.core.models`: typed runtime artifacts, requests, budgets, persona metadata, and lifecycle helpers.

4. Infrastructure layer
- `colosseum.services.repository.FileRunRepository`: file-backed persistence.
- `colosseum.services.provider_runtime.ProviderRuntimeService`: provider execution and quota recovery.
- `colosseum.services.context_bundle.ContextBundleService`: frozen bundle construction and prompt rendering.

## Core Invariants

- A run must have at least one unique agent.
- Planning only begins after a context bundle has been frozen.
- Debate only begins after plans exist.
- Human-judge actions must satisfy their own payload requirements before orchestration.
- Binary attachments remain out of text prompts and are only referenced through summarized metadata.
- Runtime status changes must refresh `updated_at`.

## Runtime Flow

1. `RunCreateRequest` enters through API or CLI.
2. The orchestrator validates provider selectability and judge configuration.
3. Context sources are frozen into a deterministic bundle with checksums.
4. Every agent generates an independent plan from the same bundle.
5. The judge either finalizes or schedules bounded rounds with an explicit agenda.
6. Debate rounds produce adjudication artifacts and update the budget ledger.
7. The judge finalizes a winner or merged plan and the report synthesizer emits the final report.
8. All artifacts are persisted under `.colosseum/runs/<run_id>/`.

## Refactor Boundaries

The current codebase intentionally centralizes contracts and splits composition at the following seams:

- `api/validation.py`: shared request validation for blocking and streaming APIs.
- `api/signals.py`: lifecycle-safe skip/cancel signal registry.
- `api/sse.py`: streaming payload serialization.
- `personas/registry.py`: typed persona metadata, legacy Markdown parsing, optional frontmatter support.
- `services/context_media.py`: shared image extraction and prompt-safe summarization.

## Extension Points

- Add new provider types by extending `ProviderType`, `ProviderConfig`, and `providers/factory.py`.
- Add new debate policy or judge heuristics in `JudgeService` and `DebateEngine` while keeping `RunCreateRequest` stable.
- Add new personas through the registry contract described in [`docs/specs/persona-authoring.md`](../specs/persona-authoring.md).
- Add agent governance fields by extending the models and the contract in [`docs/specs/agent-governance.md`](../specs/agent-governance.md).
