from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.delivery_recovery import (  # noqa: E402
    DeliveryRecoveryError,
    DeliveryRecoveryService,
    build_default_service,
)
from app.platform.ops.events import OpsEventLogger, read_ops_events  # noqa: E402
from app.platform.task_execution import ClaimLimits, TaskRepository  # noqa: E402
from app.platform.task_status import read_task_status, write_task_status  # noqa: E402


def _failed_delivery_task(
    tmp_path: Path,
    *,
    checkpoint_status: str,
    safe_error_code: str,
) -> tuple[TaskRepository, str, Path]:
    task_dir = tmp_path / "tasks" / "task-one"
    task_dir.mkdir(parents=True)
    checkpoint = {
        "schema_version": 2,
        "processing_status": "completed",
        "finalization_status": "completed",
        "delivery_status": checkpoint_status,
        "delivery_items": [
            {
                "item_id": "text-1",
                "kind": "text",
                "text": "已经生成的结果",
                "file": "",
                "status": checkpoint_status,
                "attempt_id": "attempt-local",
                "attempted_at": "2026-07-20T09:00:00+08:00",
                "evidence": "sdk_ack_timeout",
                "safe_error_code": safe_error_code,
                "correlation_id": "",
            }
        ],
        "result": {
            "skill_id": "writer1",
            "output": {"title": "简报", "body": "正文"},
            "needs_clarification": False,
            "message": "",
        },
    }
    (task_dir / "execution.json").write_text(
        json.dumps(checkpoint, ensure_ascii=False),
        encoding="utf-8",
    )
    write_task_status(
        task_dir,
        processing_status="failed",
        delivery_status="unknown" if checkpoint_status == "delivery_unknown" else "failed",
        source="test",
    )

    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    task = repository.submit(
        idempotency_key=f"key-{checkpoint_status}",
        channel="wecom",
        user_id="user-1",
        task_type="writing_writer1",
        cost_class="writing_llm",
        payload={"task_dir": str(task_dir)},
        max_attempts=3,
        resumable=True,
    )
    claimed = repository.claim_next(
        ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="worker-1",
        lease_duration=timedelta(seconds=30),
    )
    assert claimed is not None
    failed = repository.fail(
        task.task_id,
        worker_id="worker-1",
        lease_token=claimed.lease_token or "",
        safe_error_code=safe_error_code,
        retryable=False,
    )
    return repository, failed.task_id, task_dir


def _service(tmp_path: Path, repository: TaskRepository) -> DeliveryRecoveryService:
    return DeliveryRecoveryService(
        repositories={"writing": repository},
        allowed_task_roots=(tmp_path / "tasks",),
        ops_event_logger=OpsEventLogger(tmp_path / "ops-events"),
    )


def test_default_recovery_service_reads_project_env_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    project_root = tmp_path / "project"
    project_root.mkdir()
    test_data_root = tmp_path / "isolated-test-data"
    (project_root / ".env").write_text(
        "M_AGENT_RUNTIME_ENV=test\n"
        f"M_AGENT_TEST_DATA_DIR={test_data_root}\n",
        encoding="utf-8",
    )
    for key in ("M_AGENT_RUNTIME_ENV", "M_AGENT_DATA_DIR", "M_AGENT_TEST_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)

    service = build_default_service(project_root=project_root)

    writing_repository = service._repositories["writing"]
    assert writing_repository._db_path == (
        test_data_root / "runtime" / "task-execution" / "writing.sqlite3"
    )
    assert service._allowed_task_roots == (
        test_data_root / "tasks" / "writing",
        test_data_root / "tasks" / "review",
    )


def test_confirmed_not_delivered_can_requeue_original_checkpoint(tmp_path: Path):
    repository, task_id, task_dir = _failed_delivery_task(
        tmp_path,
        checkpoint_status="confirmed_not_delivered",
        safe_error_code="delivery_not_delivered",
    )

    result = _service(tmp_path, repository).recover(task_id, action="retry")

    assert result.action == "retry"
    assert result.queue_status == "queued"
    assert repository.get_task(task_id).status == "queued"
    checkpoint = json.loads((task_dir / "execution.json").read_text(encoding="utf-8"))
    assert checkpoint["processing_status"] == "completed"
    assert checkpoint["delivery_status"] == "pending"
    assert checkpoint["delivery_items"][0]["status"] == "pending"
    assert checkpoint["delivery_items"][0]["text"] == "已经生成的结果"
    assert read_task_status(task_dir)["delivery_status"] == "unknown"


def test_unknown_delivery_cannot_retry_without_manual_non_delivery_confirmation(tmp_path: Path):
    repository, task_id, _task_dir = _failed_delivery_task(
        tmp_path,
        checkpoint_status="delivery_unknown",
        safe_error_code="delivery_status_uncertain",
    )

    with pytest.raises(DeliveryRecoveryError, match="确认未送达"):
        _service(tmp_path, repository).recover(task_id, action="retry")

    assert repository.get_task(task_id).status == "failed"


def test_inspection_exposes_only_safe_delivery_evidence(tmp_path: Path):
    repository, task_id, _task_dir = _failed_delivery_task(
        tmp_path,
        checkpoint_status="delivery_unknown",
        safe_error_code="delivery_status_uncertain",
    )

    inspection = _service(tmp_path, repository).inspect(task_id)

    assert inspection.queue_status == "failed"
    assert inspection.delivery_status == "delivery_unknown"
    assert inspection.items[0]["evidence"] == "sdk_ack_timeout"
    assert "text" not in inspection.items[0]
    assert "file" not in inspection.items[0]
    assert "已经生成的结果" not in str(inspection.items)


def test_unknown_delivery_can_retry_after_explicit_manual_confirmation(tmp_path: Path):
    repository, task_id, task_dir = _failed_delivery_task(
        tmp_path,
        checkpoint_status="delivery_unknown",
        safe_error_code="delivery_status_uncertain",
    )
    service = _service(tmp_path, repository)

    result = service.recover(
        task_id,
        action="retry",
        confirm_unknown_not_delivered=True,
        operator="local-admin",
    )

    assert result.queue_status == "queued"
    checkpoint = json.loads((task_dir / "execution.json").read_text(encoding="utf-8"))
    assert checkpoint["delivery_items"][0]["status"] == "pending"
    events = read_ops_events(tmp_path / "ops-events", result.occurred_on)
    assert len(events) == 1
    assert events[0].subject == "人工恢复企业微信交付"
    assert "local-admin" not in events[0].detail
    assert "retry" in events[0].detail


def test_unknown_delivery_can_be_confirmed_delivered_without_resending(tmp_path: Path):
    repository, task_id, task_dir = _failed_delivery_task(
        tmp_path,
        checkpoint_status="delivery_unknown",
        safe_error_code="delivery_status_uncertain",
    )

    result = _service(tmp_path, repository).recover(
        task_id,
        action="confirm-delivered",
        operator="local-admin",
    )

    assert result.queue_status == "completed"
    assert repository.get_task(task_id).status == "completed"
    checkpoint = json.loads((task_dir / "execution.json").read_text(encoding="utf-8"))
    assert checkpoint["delivery_status"] == "confirmed_delivered"
    assert checkpoint["delivery_items"][0]["status"] == "confirmed_delivered"
    assert read_task_status(task_dir)["delivery_status"] == "delivered"


def test_confirmed_delivered_task_cannot_be_retried(tmp_path: Path):
    repository, task_id, task_dir = _failed_delivery_task(
        tmp_path,
        checkpoint_status="delivery_unknown",
        safe_error_code="delivery_status_uncertain",
    )
    service = _service(tmp_path, repository)
    service.recover(task_id, action="confirm-delivered")

    with pytest.raises(DeliveryRecoveryError, match="无需恢复"):
        service.recover(task_id, action="retry")

    assert json.loads((task_dir / "execution.json").read_text(encoding="utf-8"))[
        "delivery_status"
    ] == "confirmed_delivered"
