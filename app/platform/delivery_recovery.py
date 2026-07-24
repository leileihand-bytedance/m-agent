from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.platform.config import parse_env_file
from app.platform.data_paths import DataPaths
from app.platform.delivery_state import (
    CONFIRMED_DELIVERED,
    CONFIRMED_NOT_DELIVERED,
    DELIVERY_UNKNOWN,
    DELIVERY_UNKNOWN_CLOSED,
    PENDING,
    aggregate_delivery_status,
    normalize_checkpoint_status,
)
from app.platform.ops.events import OpsEventLogger
from app.platform.runtime_environment import (
    prepare_runtime_environment,
    validate_bot_startup,
)
from app.platform.task_execution import TaskRecord, TaskRepository
from app.platform.task_status import update_task_status


RecoveryAction = Literal["retry", "confirm-delivered", "close"]


class DeliveryRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeliveryRecoveryResult:
    task_id: str
    action: RecoveryAction
    queue_status: str
    delivery_status: str
    occurred_at: str
    occurred_on: date


@dataclass(frozen=True)
class DeliveryRecoveryInspection:
    task_id: str
    queue_status: str
    delivery_status: str
    safe_error_code: str
    items: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class DeliveryRecoveryCandidate:
    source: str
    task_id: str
    task_type: str
    updated_at: str
    queue_status: str
    delivery_status: str
    safe_error_code: str
    item_count: int


class DeliveryRecoveryService:
    """只供本机运维使用的交付恢复入口，不接受企业微信用户调用。"""

    def __init__(
        self,
        *,
        repositories: Mapping[str, TaskRepository],
        allowed_task_roots: Sequence[str | Path],
        ops_event_logger: OpsEventLogger,
    ) -> None:
        if not repositories:
            raise ValueError("至少需要一个任务仓库")
        self._repositories = dict(repositories)
        self._allowed_task_roots = tuple(
            Path(path).resolve(strict=False) for path in allowed_task_roots
        )
        self._ops_event_logger = ops_event_logger

    def inspect(self, task_id: str) -> DeliveryRecoveryInspection:
        _repository, task = self._find_task(task_id)
        checkpoint = self._read_checkpoint(self._task_dir(task))
        self._normalize_checkpoint(checkpoint)
        safe_items = tuple(
            {
                "item_id": str(item.get("item_id", "")),
                "kind": str(item.get("kind", "result")),
                "status": str(item.get("status", "")),
                "evidence": str(item.get("evidence", "")),
                "safe_error_code": str(item.get("safe_error_code", "")),
                "attempted_at": str(item.get("attempted_at", "")),
                "correlation_id": str(item.get("correlation_id", "")),
            }
            for item in self._items(checkpoint)
        )
        return DeliveryRecoveryInspection(
            task_id=task.task_id,
            queue_status=task.status,
            delivery_status=str(checkpoint.get("delivery_status", "")),
            safe_error_code=task.safe_error_code or "",
            items=safe_items,
        )

    def list_pending(self, *, limit: int = 50) -> tuple[DeliveryRecoveryCandidate, ...]:
        """List recoverable delivery failures without user content or local paths."""

        bounded_limit = max(1, min(int(limit), 200))
        candidates: list[DeliveryRecoveryCandidate] = []
        for source, repository in self._repositories.items():
            for task in repository.list_failed_deliveries(limit=bounded_limit):
                try:
                    inspection = self.inspect(task.task_id)
                except DeliveryRecoveryError:
                    continue
                candidates.append(
                    DeliveryRecoveryCandidate(
                        source=source,
                        task_id=task.task_id,
                        task_type=task.task_type,
                        updated_at=task.updated_at,
                        queue_status=inspection.queue_status,
                        delivery_status=inspection.delivery_status,
                        safe_error_code=inspection.safe_error_code,
                        item_count=len(inspection.items),
                    )
                )
        candidates.sort(key=lambda item: (item.updated_at, item.task_id), reverse=True)
        return tuple(candidates[:bounded_limit])

    def recover(
        self,
        task_id: str,
        *,
        action: RecoveryAction,
        confirm_unknown_not_delivered: bool = False,
        operator: str = "local-operator",
    ) -> DeliveryRecoveryResult:
        if action not in {"retry", "confirm-delivered", "close"}:
            raise DeliveryRecoveryError("不支持的交付恢复操作")
        repository, task = self._find_task(task_id)
        if task.status != "failed":
            raise DeliveryRecoveryError("当前任务无需恢复")
        task_dir = self._task_dir(task)
        checkpoint = self._read_checkpoint(task_dir)
        previous_status = self._normalize_checkpoint(checkpoint)

        now = datetime.now().astimezone()
        if action == "retry":
            self._prepare_retry(
                checkpoint,
                confirm_unknown_not_delivered=confirm_unknown_not_delivered,
                now=now,
            )
            queue_status = "queued"
            task_delivery_status = "unknown"
        elif action == "confirm-delivered":
            queue_status = self._confirm_delivered(checkpoint, now=now)
            task_delivery_status = (
                "delivered" if queue_status == "completed" else "unknown"
            )
        else:
            self._close_unknown(checkpoint, now=now)
            queue_status = "completed"
            task_delivery_status = "unknown"

        self._write_checkpoint(task_dir, checkpoint)
        update_task_status(
            task_dir,
            delivery_status=task_delivery_status,
            source="delivery_recovery",
        )
        recovered = repository.recover_failed_delivery(
            task.task_id,
            to_status="queued" if queue_status == "queued" else "completed",
        )
        self._record_event(
            task=task,
            action=action,
            previous_status=previous_status,
            resulting_status=str(checkpoint.get("delivery_status", "")),
            operator=operator,
            now=now,
        )
        return DeliveryRecoveryResult(
            task_id=task.task_id,
            action=action,
            queue_status=recovered.status,
            delivery_status=str(checkpoint.get("delivery_status", "")),
            occurred_at=now.isoformat(timespec="seconds"),
            occurred_on=now.date(),
        )

    def _find_task(self, task_id: str) -> tuple[TaskRepository, TaskRecord]:
        for repository in self._repositories.values():
            try:
                return repository, repository.get_task(task_id)
            except KeyError:
                continue
        raise DeliveryRecoveryError("未找到任务")

    def _task_dir(self, task: TaskRecord) -> Path:
        raw = task.payload.get("task_dir")
        if not isinstance(raw, str) or not raw.strip():
            raise DeliveryRecoveryError("任务没有安全的结果目录")
        task_dir = Path(raw).resolve(strict=True)
        if not task_dir.is_dir() or not any(
            task_dir != root and task_dir.is_relative_to(root)
            for root in self._allowed_task_roots
        ):
            raise DeliveryRecoveryError("任务结果目录不在允许范围内")
        return task_dir

    @staticmethod
    def _read_checkpoint(task_dir: Path) -> dict[str, object]:
        path = task_dir / "execution.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeliveryRecoveryError("任务交付检查点损坏") from exc
        if not isinstance(payload, dict) or payload.get("processing_status") != "completed":
            raise DeliveryRecoveryError("任务结果尚未生成，不能走交付恢复")
        return payload

    @staticmethod
    def _normalize_checkpoint(checkpoint: dict[str, object]) -> str:
        raw_items = checkpoint.get("delivery_items")
        if isinstance(raw_items, list) and raw_items:
            for index, raw_item in enumerate(raw_items, start=1):
                if not isinstance(raw_item, dict):
                    raise DeliveryRecoveryError("任务交付项格式无效")
                raw_item.setdefault("item_id", f"item-{index}")
                raw_item["status"] = normalize_checkpoint_status(raw_item.get("status"))
            checkpoint["delivery_status"] = aggregate_delivery_status(raw_items)
        else:
            delivery_status = normalize_checkpoint_status(
                checkpoint.get("delivery_status")
            )
            checkpoint["delivery_status"] = delivery_status
            checkpoint["status"] = delivery_status
        checkpoint["schema_version"] = 2
        return str(checkpoint["delivery_status"])

    @staticmethod
    def _items(checkpoint: dict[str, object]) -> list[dict[str, object]]:
        raw_items = checkpoint.get("delivery_items")
        if isinstance(raw_items, list) and raw_items:
            return [item for item in raw_items if isinstance(item, dict)]
        return [checkpoint]

    def _prepare_retry(
        self,
        checkpoint: dict[str, object],
        *,
        confirm_unknown_not_delivered: bool,
        now: datetime,
    ) -> None:
        items = self._items(checkpoint)
        statuses = {str(item.get("status", "")) for item in items}
        if CONFIRMED_DELIVERED in statuses and statuses == {CONFIRMED_DELIVERED}:
            raise DeliveryRecoveryError("当前任务无需恢复")
        if DELIVERY_UNKNOWN in statuses and not confirm_unknown_not_delivered:
            raise DeliveryRecoveryError("送达未知，必须先人工确认未送达才能重发")
        recoverable = {CONFIRMED_NOT_DELIVERED}
        if confirm_unknown_not_delivered:
            recoverable.add(DELIVERY_UNKNOWN)
        changed = False
        for item in items:
            if item.get("status") not in recoverable:
                continue
            self._append_history(item, now=now, action="retry")
            item.update(
                {
                    "status": PENDING,
                    "attempt_id": "",
                    "attempted_at": "",
                    "evidence": "manual_retry_approved",
                    "safe_error_code": "",
                    "correlation_id": "",
                }
            )
            changed = True
        if not changed:
            raise DeliveryRecoveryError("当前任务没有可恢复的未送达结果")
        checkpoint["delivery_status"] = PENDING

    def _confirm_delivered(
        self,
        checkpoint: dict[str, object],
        *,
        now: datetime,
    ) -> str:
        items = self._items(checkpoint)
        changed = False
        for item in items:
            if item.get("status") != DELIVERY_UNKNOWN:
                continue
            self._append_history(item, now=now, action="confirm-delivered")
            item.update(
                {
                    "status": CONFIRMED_DELIVERED,
                    "attempted_at": now.isoformat(timespec="seconds"),
                    "evidence": "manual_confirmed_delivered",
                    "safe_error_code": "",
                }
            )
            changed = True
        if not changed:
            if str(checkpoint.get("delivery_status")) == CONFIRMED_DELIVERED:
                raise DeliveryRecoveryError("当前任务无需恢复")
            raise DeliveryRecoveryError("当前任务没有送达未知的结果")
        aggregate = aggregate_delivery_status(list(items))
        checkpoint["delivery_status"] = aggregate
        return "completed" if aggregate == CONFIRMED_DELIVERED else "queued"

    def _close_unknown(self, checkpoint: dict[str, object], *, now: datetime) -> None:
        items = self._items(checkpoint)
        if not any(item.get("status") == DELIVERY_UNKNOWN for item in items):
            raise DeliveryRecoveryError("当前任务没有送达未知的结果")
        for item in items:
            if item.get("status") == DELIVERY_UNKNOWN:
                self._append_history(item, now=now, action="close")
                item["status"] = DELIVERY_UNKNOWN_CLOSED
                item["evidence"] = "manual_closed_unknown"
        checkpoint["delivery_status"] = DELIVERY_UNKNOWN_CLOSED

    @staticmethod
    def _append_history(
        item: dict[str, object],
        *,
        now: datetime,
        action: str,
    ) -> None:
        history = item.setdefault("delivery_history", [])
        if not isinstance(history, list):
            history = []
            item["delivery_history"] = history
        history.append(
            {
                "status": str(item.get("status", "")),
                "attempt_id": str(item.get("attempt_id", "")),
                "attempted_at": str(item.get("attempted_at", "")),
                "evidence": str(item.get("evidence", "")),
                "safe_error_code": str(item.get("safe_error_code", "")),
                "correlation_id": str(item.get("correlation_id", "")),
                "recovery_action": action,
                "recovered_at": now.isoformat(timespec="seconds"),
            }
        )

    @staticmethod
    def _write_checkpoint(task_dir: Path, checkpoint: dict[str, object]) -> None:
        path = task_dir / "execution.json"
        temporary = task_dir / f".execution.{uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(checkpoint, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _record_event(
        self,
        *,
        task: TaskRecord,
        action: str,
        previous_status: str,
        resulting_status: str,
        operator: str,
        now: datetime,
    ) -> None:
        operator_ref = hashlib.sha256(operator.encode("utf-8")).hexdigest()[:12]
        self._ops_event_logger.record(
            source="delivery_recovery",
            severity="warning",
            subject="人工恢复企业微信交付",
            detail=(
                f"操作: {action}\n"
                f"原状态: {previous_status}\n"
                f"新状态: {resulting_status}\n"
                f"操作人标识: {operator_ref}"
            ),
            skill_id=task.task_type,
            job_id=task.task_id,
            created_at=now,
        )


def build_default_service(*, project_root: Path) -> DeliveryRecoveryService:
    values = parse_env_file(project_root / ".env")
    values.update(os.environ)
    runtime = prepare_runtime_environment(values, project_root=project_root)
    paths = DataPaths.from_values(runtime.values, project_root=project_root)
    validate_bot_startup(
        runtime,
        data_paths=(paths.root, paths.writing_jobs, paths.review_tasks, paths.task_queue_db),
        project_root=project_root,
    )
    queue_dir = paths.task_queue_db.parent
    return DeliveryRecoveryService(
        repositories={
            "writing": TaskRepository(queue_dir / "writing.sqlite3"),
            "review": TaskRepository(queue_dir / "review.sqlite3"),
        },
        allowed_task_roots=(paths.writing_jobs, paths.review_tasks),
        ops_event_logger=OpsEventLogger(paths.ops_events),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="恢复企业微信结果交付检查点")
    parser.add_argument("task_id", help="后台任务编号")
    parser.add_argument(
        "action",
        choices=("inspect", "retry", "confirm-delivered", "close"),
        help="查看依据、重发、确认已送达或关闭送达未知任务",
    )
    parser.add_argument(
        "--confirm-unknown-not-delivered",
        action="store_true",
        help="人工已确认送达未知结果实际未送达，允许重发",
    )
    parser.add_argument("--operator", default="local-operator", help="本机操作人标识")
    args = parser.parse_args(argv)

    project_root = Path(__file__).resolve().parents[2]
    service = build_default_service(project_root=project_root)
    if args.action == "inspect":
        inspection = service.inspect(args.task_id)
        print(
            json.dumps(
                {
                    "task_id": inspection.task_id,
                    "queue_status": inspection.queue_status,
                    "delivery_status": inspection.delivery_status,
                    "safe_error_code": inspection.safe_error_code,
                    "items": inspection.items,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    result = service.recover(
        args.task_id,
        action=args.action,
        confirm_unknown_not_delivered=args.confirm_unknown_not_delivered,
        operator=args.operator,
    )
    print(
        json.dumps(
            {
                "task_id": result.task_id,
                "action": result.action,
                "queue_status": result.queue_status,
                "delivery_status": result.delivery_status,
                "occurred_at": result.occurred_at,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
