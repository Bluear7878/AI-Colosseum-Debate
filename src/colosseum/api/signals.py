"""Stateful coordination primitives for streaming run control endpoints."""

from __future__ import annotations

import asyncio


class RunSignalRegistry:
    """Track per-run skip and cancel signals for active SSE debates."""

    def __init__(self) -> None:
        self._skip_signals: dict[str, asyncio.Event] = {}
        self._cancel_signals: dict[str, asyncio.Event] = {}

    def register_skip(self, run_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._skip_signals[run_id] = event
        return event

    def register_cancel(self, run_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._cancel_signals[run_id] = event
        return event

    def get_skip(self, run_id: str) -> asyncio.Event | None:
        return self._skip_signals.get(run_id)

    def get_cancel(self, run_id: str) -> asyncio.Event | None:
        return self._cancel_signals.get(run_id)

    def cleanup(self, run_id: str) -> None:
        self._skip_signals.pop(run_id, None)
        self._cancel_signals.pop(run_id, None)


run_signal_registry = RunSignalRegistry()
