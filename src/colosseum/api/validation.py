"""Shared validation helpers for API entrypoints."""

from __future__ import annotations

from colosseum.core.models import JudgeMode, RunCreateRequest


def validate_run_request(orchestrator, request: RunCreateRequest) -> None:
    """Apply shared provider/judge validation for both blocking and streaming APIs."""
    orchestrator.provider_runtime.validate_agents_selectable(request.agents)
    if request.judge.mode == JudgeMode.AI:
        if not request.judge.provider:
            raise ValueError("AI judge mode requires a judge provider.")
        orchestrator.provider_runtime.validate_provider_selectable(
            request.judge.provider, "AI judge"
        )
