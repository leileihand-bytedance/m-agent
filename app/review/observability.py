"""Content-free observability records for review tasks."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .capabilities import ReviewCapability
from .core.metrics import ReviewRunMetrics


def write_review_run_observability(
    task_dir: Path,
    *,
    capability: ReviewCapability,
    metrics: ReviewRunMetrics | None,
    elapsed_ms: float,
    finding_count: int,
) -> None:
    """Merge run metrics into meta.json without storing document content."""
    meta_path = task_dir / "meta.json"
    meta: dict[str, object] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (OSError, json.JSONDecodeError):
            meta = {}

    meta["capability_id"] = capability.id
    meta["capability_name"] = capability.name
    meta["observability"] = {
        "schema_version": 1,
        "elapsed_ms": round(max(0.0, elapsed_ms), 2),
        "model_calls": metrics.model_calls if metrics is not None else 0,
        "model_failures": metrics.model_failures if metrics is not None else 0,
        "model_calls_by_stage": metrics.model_calls_by_stage if metrics is not None else {},
        "model_failures_by_stage": (
            metrics.model_failures_by_stage if metrics is not None else {}
        ),
        "model_elapsed_ms_by_stage": (
            {
                stage: round(value, 2)
                for stage, value in metrics.model_elapsed_ms_by_stage.items()
            }
            if metrics is not None
            else {}
        ),
        "degraded_stages": list(metrics.degraded_stages) if metrics is not None else [],
        "finding_count": max(0, int(finding_count)),
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    temporary = task_dir / f".meta.{uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(meta_path)


__all__ = ["write_review_run_observability"]
