from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
from pathlib import Path
from typing import Mapping
from uuid import uuid4


PROCESSING_STATUSES = frozenset(
    {
        "queued",
        "running",
        "processing",
        "completed",
        "needs_input",
        "failed",
        "cancelled",
        "incomplete",
    }
)
DELIVERY_STATUSES = frozenset({"unknown", "delivered", "failed", "not_applicable"})


class StaleTaskStatusVersionError(RuntimeError):
    """拒绝让较旧的任务状态覆盖已经落盘的新状态。"""


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
    state_version: int | None = None,
) -> Path:
    if processing_status not in PROCESSING_STATUSES:
        raise ValueError(f"不支持的任务处理状态：{processing_status}")
    if delivery_status not in DELIVERY_STATUSES:
        raise ValueError(f"不支持的任务交付状态：{delivery_status}")

    task_dir.mkdir(parents=True, exist_ok=True)
    with _status_write_lock(task_dir):
        current = read_task_status(task_dir)
        resolved_version = _resolve_state_version(current, state_version)
        return _write_task_status_unlocked(
            task_dir,
            processing_status=processing_status,
            delivery_status=delivery_status,
            source=source,
            state_version=resolved_version,
        )


def _write_task_status_unlocked(
    task_dir: Path,
    *,
    processing_status: str,
    delivery_status: str,
    source: str,
    state_version: int,
) -> Path:
    status_path = task_dir / "status.json"
    temporary_path = task_dir / f".status.json.{uuid4().hex}.tmp"
    temporary_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "processing_status": processing_status,
                "delivery_status": delivery_status,
                "source": source,
                "state_version": state_version,
                "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_path.replace(status_path)
    return status_path


def read_task_status(task_dir: Path) -> dict[str, object]:
    status_path = task_dir / "status.json"
    if not status_path.is_file():
        return {}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def update_task_status(
    task_dir: Path,
    *,
    processing_status: str | None = None,
    delivery_status: str | None = None,
    source: str = "runtime",
    state_version: int | None = None,
) -> Path:
    task_dir.mkdir(parents=True, exist_ok=True)
    with _status_write_lock(task_dir):
        current = read_task_status(task_dir)
        resolved_processing = processing_status or str(current.get("processing_status", ""))
        resolved_delivery = delivery_status or str(current.get("delivery_status", ""))
        if not resolved_processing or not resolved_delivery:
            raise ValueError("首次写入任务状态时必须同时提供处理状态和交付状态")
        if resolved_processing not in PROCESSING_STATUSES:
            raise ValueError(f"不支持的任务处理状态：{resolved_processing}")
        if resolved_delivery not in DELIVERY_STATUSES:
            raise ValueError(f"不支持的任务交付状态：{resolved_delivery}")
        resolved_version = _resolve_state_version(current, state_version)
        return _write_task_status_unlocked(
            task_dir,
            processing_status=resolved_processing,
            delivery_status=resolved_delivery,
            source=source,
            state_version=resolved_version,
        )


def _resolve_state_version(
    current: Mapping[str, object],
    requested: int | None,
) -> int:
    current_value = current.get("state_version", 0)
    if not isinstance(current_value, int) or isinstance(current_value, bool) or current_value < 0:
        current_version = 0
    else:
        current_version = current_value
    if requested is None:
        return current_version
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0:
        raise ValueError("state_version 必须是非负整数")
    if requested < current_version:
        raise StaleTaskStatusVersionError(
            f"旧版本状态不能覆盖当前版本：{requested} < {current_version}"
        )
    return requested


@contextmanager
def _status_write_lock(task_dir: Path):
    lock_path = task_dir / ".status.lock"
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
