from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.platform.task_execution import (
    ClaimLimits,
    PersistentTaskExecutor,
    SafeTaskError,
    TaskRepository,
)
from app.review.task_execution import (
    GeneralReviewTaskService,
    PreparedReviewDelivery,
)


def _service(
    *,
    repository: TaskRepository,
    reviews_root: Path,
    processor,
    text_sender=None,
    attachment_sender=None,
    failure_notifier=None,
) -> GeneralReviewTaskService:
    async def default_text_sender(_recipient: str, _text: str) -> bool:
        return True

    async def default_attachment_sender(
        _recipient: str,
        _path: Path,
        _task_dir: Path,
    ) -> bool:
        return True

    async def default_failure_notifier(
        _recipient: str,
        _error_code: str,
        _task_id: str,
    ) -> None:
        return None

    return GeneralReviewTaskService(
        repository=repository,
        reviews_root=reviews_root,
        processor=processor,
        text_sender=text_sender or default_text_sender,
        attachment_sender=attachment_sender or default_attachment_sender,
        failure_notifier=failure_notifier or default_failure_notifier,
    )


def test_general_review_submission_is_persistent_and_idempotent(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")

    async def processor(_workspace):
        return PreparedReviewDelivery.text("没有发现问题，可以走审批了。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )

    first = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )
    duplicate = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.task.task_id == first.task.task_id
    assert repository.count_tasks() == 1
    task_dir = Path(str(first.task.payload["task_dir"]))
    input_file = task_dir / str(first.task.payload["input_file"])
    assert input_file.read_bytes() == b"docx-content"
    assert len(list((tmp_path / "reviews").rglob("queued-*"))) == 1
    status = json.loads((task_dir / "status.json").read_text(encoding="utf-8"))
    assert status["processing_status"] == "queued"


def test_completed_general_review_is_not_processed_or_delivered_twice(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    calls = {"process": 0, "deliver": 0}

    async def processor(workspace):
        calls["process"] += 1
        output = workspace.task_dir / "output" / "marked_材料.docx"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"marked")
        return PreparedReviewDelivery.attachment(output)

    async def attachment_sender(_recipient: str, path: Path, task_dir: Path) -> bool:
        calls["deliver"] += 1
        assert path.is_relative_to(task_dir)
        return True

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
        attachment_sender=attachment_sender,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )
    executor = PersistentTaskExecutor(
        repository=repository,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="review-worker",
    )
    executor.register_handler("review_general_docx", service.handle)

    async def scenario():
        completed = await executor.run_once()
        repeated = await service.handle(repository.get_task(submission.task.task_id))
        return completed, repeated

    completed, repeated = asyncio.run(scenario())

    assert completed is not None and completed.status == "completed"
    assert repeated.status == "completed"
    assert calls == {"process": 1, "deliver": 1}
    checkpoint = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["processing_status"] == "completed"
    assert checkpoint["delivery_status"] == "delivered"
    meta = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["task_id"] == submission.task.task_id


def test_restart_does_not_resend_when_delivery_status_is_uncertain(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    calls = {"process": 0, "deliver": 0}
    failures: list[tuple[str, str, str]] = []

    async def processor(_workspace):
        calls["process"] += 1
        return PreparedReviewDelivery.text("没有发现问题，可以走审批了。")

    async def interrupted_sender(_recipient: str, _text: str) -> bool:
        calls["deliver"] += 1
        raise asyncio.CancelledError

    async def failure_notifier(recipient: str, error_code: str, task_id: str) -> None:
        failures.append((recipient, error_code, task_id))

    first_service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
        text_sender=interrupted_sender,
        failure_notifier=failure_notifier,
    )
    submission = first_service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )

    async def must_not_process(_workspace):
        raise AssertionError("恢复时不应重复调用审核模型")

    async def must_not_send(_recipient: str, _text: str) -> bool:
        raise AssertionError("发送状态不确定时不应自动重发")

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await first_service.handle(submission.task)
        restarted_service = _service(
            repository=repository,
            reviews_root=tmp_path / "reviews",
            processor=must_not_process,
            text_sender=must_not_send,
            failure_notifier=failure_notifier,
        )
        with pytest.raises(SafeTaskError) as error:
            await restarted_service.handle(submission.task)
        return error.value

    error = asyncio.run(scenario())

    assert error.safe_error_code == "delivery_status_uncertain"
    assert calls == {"process": 1, "deliver": 1}
    assert failures == [
        ("user-1", "delivery_status_uncertain", submission.task.task_id)
    ]


@pytest.mark.parametrize("checkpoint_content", ["not-json", "{}"])
def test_damaged_execution_checkpoint_fails_safely_and_notifies_user(
    tmp_path: Path,
    checkpoint_content: str,
):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    failures: list[tuple[str, str, str]] = []

    async def processor(_workspace):
        raise AssertionError("损坏的执行状态不能重新调用审核模型")

    async def failure_notifier(recipient: str, error_code: str, task_id: str) -> None:
        failures.append((recipient, error_code, task_id))

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
        failure_notifier=failure_notifier,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )
    task_dir = Path(str(submission.task.payload["task_dir"]))
    (task_dir / "execution.json").write_text(checkpoint_content, encoding="utf-8")

    async def scenario():
        with pytest.raises(SafeTaskError) as error:
            await service.handle(submission.task)
        return error.value

    error = asyncio.run(scenario())

    assert error.safe_error_code == "invalid_task_checkpoint"
    assert error.retryable is False
    assert failures == [
        ("user-1", "invalid_task_checkpoint", submission.task.task_id)
    ]


def test_failed_delivery_checkpoint_notifies_after_restart(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    failures: list[tuple[str, str, str]] = []

    async def processor(_workspace):
        raise AssertionError("交付失败恢复时不应重新调用审核模型")

    async def failure_notifier(recipient: str, error_code: str, task_id: str) -> None:
        failures.append((recipient, error_code, task_id))

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
        failure_notifier=failure_notifier,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )
    task_dir = Path(str(submission.task.payload["task_dir"]))
    (task_dir / "execution.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "processing_status": "completed",
                "delivery_status": "failed",
                "result_kind": "text",
                "result_text": "审核完成",
                "result_file": "",
            }
        ),
        encoding="utf-8",
    )

    async def scenario():
        with pytest.raises(SafeTaskError) as error:
            await service.handle(submission.task)
        return error.value

    error = asyncio.run(scenario())

    assert error.safe_error_code == "delivery_failed"
    assert failures == [("user-1", "delivery_failed", submission.task.task_id)]
