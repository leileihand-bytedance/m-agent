from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Mapping


PROCESSING_STATUSES = frozenset(
    {"processing", "completed", "needs_input", "failed", "incomplete"}
)
DELIVERY_STATUSES = frozenset({"unknown", "delivered", "failed", "not_applicable"})


def classify_writing_result(result: Mapping[str, object]) -> str:
    if bool(result.get("needs_clarification", False)):
        return "needs_input"
    output = result.get("output")
    if isinstance(output, Mapping):
        for key in ("title", "body", "text", "revised_text", "content"):
            if str(output.get(key, "") or "").strip():
                return "completed"
    return "failed"


def write_task_status(
    task_dir: Path,
    *,
    processing_status: str,
    delivery_status: str = "unknown",
    source: str = "runtime",
) -> Path:
    if processing_status not in PROCESSING_STATUSES:
        raise ValueError(f"不支持的任务处理状态：{processing_status}")
    if delivery_status not in DELIVERY_STATUSES:
        raise ValueError(f"不支持的任务交付状态：{delivery_status}")

    task_dir.mkdir(parents=True, exist_ok=True)
    status_path = task_dir / "status.json"
    temporary_path = task_dir / ".status.json.tmp"
    temporary_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "processing_status": processing_status,
                "delivery_status": delivery_status,
                "source": source,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(status_path)
    return status_path
