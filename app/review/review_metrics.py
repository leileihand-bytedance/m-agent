"""审核运行统计。"""

from __future__ import annotations

from threading import Lock


class ReviewRunMetrics:
    """记录一次审核中的真实模型请求次数。"""

    def __init__(self) -> None:
        self._model_calls = 0
        self._model_failures = 0
        self._degraded_stages: set[str] = set()
        self._lock = Lock()

    @property
    def model_calls(self) -> int:
        with self._lock:
            return self._model_calls

    def record_model_call(self) -> None:
        with self._lock:
            self._model_calls += 1

    @property
    def model_failures(self) -> int:
        with self._lock:
            return self._model_failures

    def record_model_failure(self) -> None:
        with self._lock:
            self._model_failures += 1

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
