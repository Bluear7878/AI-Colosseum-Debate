"""Disjoint GPU partitioning for `colosseum qa` gladiators.

The QA ensemble runs multiple Claude (or mediated) gladiators in parallel.
Each gladiator must receive a fixed slice of GPUs via CUDA_VISIBLE_DEVICES so
its sub-agents never collide with other gladiators on the same device.

The allocator follows a four-case truth table:

| case | condition                                       | response                          |
|------|-------------------------------------------------|-----------------------------------|
| A    | gpus_per_glad * G == N                          | clean disjoint partition           |
| B    | gpus_per_glad * G < N                           | partition + log unused devices     |
| C    | gpus_per_glad * G > N AND N >= G                | reduce gpus_per_glad and warn      |
| D    | G > N                                           | hard error unless --sequential     |

`G` = gladiator count, `N` = eligible GPU count.
"""

from __future__ import annotations

from colosseum.core.config import QA_MIN_FREE_GPU_MEMORY_MB
from colosseum.core.models import QAGpuPlan
from colosseum.services.local_runtime import LocalRuntimeService


class QAGpuAllocationError(RuntimeError):
    """Raised when QA mode cannot satisfy a parallel disjoint GPU plan."""


class QAGpuAllocator:
    """Allocate detected GPUs to QA gladiators in disjoint slices."""

    def __init__(
        self,
        local_runtime: LocalRuntimeService | None = None,
        min_free_memory_mb: int = QA_MIN_FREE_GPU_MEMORY_MB,
    ) -> None:
        self.local_runtime = local_runtime or LocalRuntimeService()
        self.min_free_memory_mb = min_free_memory_mb

    def detect_eligible_gpus(
        self,
        forced_indices: list[int] | None = None,
    ) -> tuple[list[int], list[int], dict[int, str]]:
        """Return (eligible, detected, ineligible_reasons).

        When `forced_indices` is provided, only those indices are considered.
        Indices that don't exist on the host or fail eligibility checks land
        in `ineligible_reasons` keyed by index.
        """
        devices = self.local_runtime.detect_gpu_devices()
        detected = [d.index for d in devices]
        if not detected:
            return [], [], {}

        free_mem = self.local_runtime.detect_gpu_free_memory_mb()
        busy = self.local_runtime.detect_gpu_compute_processes()

        candidate_indices = forced_indices if forced_indices is not None else detected
        eligible: list[int] = []
        reasons: dict[int, str] = {}

        for idx in candidate_indices:
            if idx not in detected:
                reasons[idx] = "device not present on host"
                continue
            if idx in busy:
                reasons[idx] = "compute process already running on this GPU"
                continue
            free = free_mem.get(idx)
            if free is None:
                # If we cannot determine free memory, do not block — trust user.
                eligible.append(idx)
                continue
            if free < self.min_free_memory_mb:
                reasons[idx] = (
                    f"free memory {free} MiB below required {self.min_free_memory_mb} MiB"
                )
                continue
            eligible.append(idx)
        return eligible, detected, reasons

    def allocate(
        self,
        gladiator_ids: list[str],
        eligible: list[int],
        detected: list[int] | None = None,
        ineligible_reasons: dict[int, str] | None = None,
        gpus_per_gladiator: int | None = None,
        sequential: bool = False,
        forced_indices: list[int] | None = None,
    ) -> QAGpuPlan:
        """Build a QAGpuPlan for the given gladiators.

        Raises QAGpuAllocationError on case D unless `sequential=True`.
        """
        gladiator_count = len(gladiator_ids)
        if gladiator_count == 0:
            raise QAGpuAllocationError("Cannot allocate GPUs: no gladiators provided.")

        plan = QAGpuPlan(
            detected_devices=list(detected or []),
            eligible_devices=list(eligible),
            ineligible_reasons={str(k): v for k, v in (ineligible_reasons or {}).items()},
            forced_indices=list(forced_indices) if forced_indices is not None else None,
        )

        eligible_count = len(eligible)

        # Sequential mode: every gladiator runs alone with the full eligible set.
        if sequential:
            plan.mode = "sequential"
            plan.allocations = {gid: list(eligible) for gid in gladiator_ids}
            plan.unused_devices = []
            return plan

        # No GPUs at all → emit empty allocations. The orchestrator may still
        # proceed in --brief mode where execution is code-analysis only.
        if eligible_count == 0:
            plan.mode = "parallel"
            plan.allocations = {gid: [] for gid in gladiator_ids}
            plan.unused_devices = []
            return plan

        # Case D: more gladiators than eligible GPUs.
        if gladiator_count > eligible_count:
            raise QAGpuAllocationError(
                f"Need at least one GPU per gladiator: have {eligible_count} eligible "
                f"GPU(s) for {gladiator_count} gladiator(s). Reduce the gladiator count, "
                f"free up GPUs, or pass --sequential to run them one at a time."
            )

        # Decide per-gladiator slice size.
        if gpus_per_gladiator is None or gpus_per_gladiator <= 0:
            slice_size = eligible_count // gladiator_count
        else:
            slice_size = gpus_per_gladiator

        if slice_size <= 0:
            slice_size = 1

        # Case C: requested slice size doesn't fit. Reduce.
        max_fit = eligible_count // gladiator_count
        if slice_size > max_fit:
            slice_size = max(1, max_fit)

        plan.mode = "parallel"
        allocations: dict[str, list[int]] = {}
        cursor = 0
        for gid in gladiator_ids:
            allocations[gid] = list(eligible[cursor : cursor + slice_size])
            cursor += slice_size
        plan.allocations = allocations
        plan.unused_devices = list(eligible[cursor:])
        return plan
