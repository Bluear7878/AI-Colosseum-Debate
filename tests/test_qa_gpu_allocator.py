"""Unit tests for QAGpuAllocator covering all four allocation cases."""

from __future__ import annotations

import pytest

from colosseum.core.models import LocalGpuDevice
from colosseum.services.qa_gpu_allocator import QAGpuAllocationError, QAGpuAllocator


class _StubLocalRuntime:
    """Replaces LocalRuntimeService for deterministic GPU detection."""

    def __init__(
        self,
        devices: list[LocalGpuDevice],
        free_mem: dict[int, int] | None = None,
        busy: set[int] | None = None,
    ) -> None:
        self._devices = devices
        self._free_mem = free_mem or {d.index: 24000 for d in devices}
        self._busy = busy or set()

    def detect_gpu_devices(self):
        return list(self._devices)

    def detect_gpu_free_memory_mb(self):
        return dict(self._free_mem)

    def detect_gpu_compute_processes(self):
        return set(self._busy)


def _devices(*indices: int) -> list[LocalGpuDevice]:
    return [
        LocalGpuDevice(index=i, backend="nvidia", name=f"GPU {i}", memory_total_mb=24576)
        for i in indices
    ]


def test_case_a_clean_partition():
    """N=4 GPUs, G=2 gladiators, slice=2 → exact split."""
    runtime = _StubLocalRuntime(_devices(0, 1, 2, 3))
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, reasons = allocator.detect_eligible_gpus()
    assert eligible == [0, 1, 2, 3]
    assert reasons == {}

    plan = allocator.allocate(
        gladiator_ids=["alpha", "beta"],
        eligible=eligible,
        detected=detected,
        gpus_per_gladiator=2,
    )
    assert plan.mode == "parallel"
    assert plan.allocations == {"alpha": [0, 1], "beta": [2, 3]}
    assert plan.unused_devices == []


def test_case_b_slack_partition_logs_unused():
    """N=5 GPUs, G=2, slice=2 → leftover GPU 4 logged as unused."""
    runtime = _StubLocalRuntime(_devices(0, 1, 2, 3, 4))
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, _ = allocator.detect_eligible_gpus()
    plan = allocator.allocate(
        gladiator_ids=["alpha", "beta"],
        eligible=eligible,
        detected=detected,
        gpus_per_gladiator=2,
    )
    assert plan.allocations == {"alpha": [0, 1], "beta": [2, 3]}
    assert plan.unused_devices == [4]


def test_case_c_oversubscribe_reduces_slice():
    """N=3 GPUs, G=2, slice=4 requested → reduced to 1 each."""
    runtime = _StubLocalRuntime(_devices(0, 1, 2))
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, _ = allocator.detect_eligible_gpus()
    plan = allocator.allocate(
        gladiator_ids=["alpha", "beta"],
        eligible=eligible,
        detected=detected,
        gpus_per_gladiator=4,
    )
    # max_fit = 3 // 2 = 1
    assert plan.allocations == {"alpha": [0], "beta": [1]}
    assert plan.unused_devices == [2]


def test_case_d_under_subscribe_raises():
    """N=1 GPU, G=2 gladiators → hard error."""
    runtime = _StubLocalRuntime(_devices(0))
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, _ = allocator.detect_eligible_gpus()
    with pytest.raises(QAGpuAllocationError):
        allocator.allocate(
            gladiator_ids=["alpha", "beta"],
            eligible=eligible,
            detected=detected,
        )


def test_sequential_opt_in_when_under_subscribed():
    """--sequential allows G > N to proceed with full pool per gladiator."""
    runtime = _StubLocalRuntime(_devices(0))
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, _ = allocator.detect_eligible_gpus()
    plan = allocator.allocate(
        gladiator_ids=["alpha", "beta"],
        eligible=eligible,
        detected=detected,
        sequential=True,
    )
    assert plan.mode == "sequential"
    assert plan.allocations == {"alpha": [0], "beta": [0]}


def test_busy_gpu_excluded_from_eligible():
    """A GPU with an active compute process is filtered out."""
    runtime = _StubLocalRuntime(_devices(0, 1, 2, 3), busy={1, 2})
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, reasons = allocator.detect_eligible_gpus()
    assert eligible == [0, 3]
    assert detected == [0, 1, 2, 3]
    assert 1 in reasons
    assert 2 in reasons


def test_low_memory_gpu_excluded():
    """A GPU below the free-memory threshold is filtered out."""
    runtime = _StubLocalRuntime(
        _devices(0, 1),
        free_mem={0: 1000, 1: 24000},
    )
    allocator = QAGpuAllocator(local_runtime=runtime, min_free_memory_mb=5120)
    eligible, _, reasons = allocator.detect_eligible_gpus()
    assert eligible == [1]
    assert 0 in reasons
    assert "free memory" in reasons[0]


def test_forced_indices_respected():
    """Explicit --gpus 0,2 limits the consideration set."""
    runtime = _StubLocalRuntime(_devices(0, 1, 2, 3))
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, _ = allocator.detect_eligible_gpus(forced_indices=[0, 2])
    assert eligible == [0, 2]
    assert detected == [0, 1, 2, 3]


def test_no_gpus_at_all_returns_empty_allocations():
    """When there are no eligible GPUs (e.g. brief mode on a CPU box),
    the allocator returns empty allocations rather than raising."""
    runtime = _StubLocalRuntime([])
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, _ = allocator.detect_eligible_gpus()
    assert eligible == []
    assert detected == []
    plan = allocator.allocate(
        gladiator_ids=["alpha", "beta"],
        eligible=eligible,
        detected=detected,
    )
    assert plan.allocations == {"alpha": [], "beta": []}
    assert plan.mode == "parallel"


def test_default_slice_size_when_unspecified():
    """When `gpus_per_gladiator` is None, slice = floor(N / G)."""
    runtime = _StubLocalRuntime(_devices(0, 1, 2, 3, 4, 5))
    allocator = QAGpuAllocator(local_runtime=runtime)
    eligible, detected, _ = allocator.detect_eligible_gpus()
    plan = allocator.allocate(
        gladiator_ids=["a", "b", "c"],
        eligible=eligible,
        detected=detected,
    )
    assert plan.allocations == {"a": [0, 1], "b": [2, 3], "c": [4, 5]}
