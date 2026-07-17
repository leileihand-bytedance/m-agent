"""Thread-safe metrics shared by all review model stages."""

from __future__ import annotations

from collections import defaultdict
from threading import Lock


class ReviewRunMetrics:
    """Record model calls, failures, degradation, and stage duration."""

    def __init__(self) -> None:
        self._model_calls = 0
        self._model_failures = 0
        self._model_calls_by_stage: dict[str, int] = defaultdict(int)
        self._model_failures_by_stage: dict[str, int] = defaultdict(int)
        self._model_elapsed_ms_by_stage: dict[str, float] = defaultdict(float)
        self._degraded_stages: set[str] = set()
        self._lock = Lock()

    @property
    def model_calls(self) -> int:
        with self._lock:
            return self._model_calls

    def record_model_call(self, stage: str = "") -> None:
        normalized = stage.strip()
        with self._lock:
            self._model_calls += 1
            if normalized:
                self._model_calls_by_stage[normalized] += 1

    @property
    def model_calls_by_stage(self) -> dict[str, int]:
        with self._lock:
            return dict(self._model_calls_by_stage)

    @property
    def model_failures(self) -> int:
        with self._lock:
            return self._model_failures

    def record_model_failure(self, stage: str = "") -> None:
        normalized = stage.strip()
        with self._lock:
            self._model_failures += 1
            if normalized:
                self._model_failures_by_stage[normalized] += 1

    @property
    def model_failures_by_stage(self) -> dict[str, int]:
        with self._lock:
            return dict(self._model_failures_by_stage)

    def record_model_elapsed(self, stage: str, elapsed_ms: float) -> None:
        normalized = stage.strip()
        if not normalized:
            return
        with self._lock:
            self._model_elapsed_ms_by_stage[normalized] += max(0.0, elapsed_ms)

    @property
    def model_elapsed_ms_by_stage(self) -> dict[str, float]:
        with self._lock:
            return dict(self._model_elapsed_ms_by_stage)

    @property
    def degraded_stages(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._degraded_stages))

    def record_degraded_stage(self, stage: str) -> None:
        normalized = stage.strip()
        if not normalized:
            return
        with self._lock:
            self._degraded_stages.add(normalized)
