from __future__ import annotations

import asyncio
from dataclasses import replace
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
    GENERAL_TEXT_REVIEW_TASK_TYPE,
    HALF_MONTHLY_REVIEW_TASK_TYPE,
    NEICAN_REVIEW_TASK_TYPE,
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
    REVIEW_FILE_TASK_TYPES,
    REVIEW_TASK_TYPES,
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
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["task_type"] == "review_general_docx"


@pytest.mark.parametrize(
    "task_type",
    [
        "review_general_docx",
        NEICAN_REVIEW_TASK_TYPE,
        HALF_MONTHLY_REVIEW_TASK_TYPE,
        OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
    ],
)
def test_single_file_review_types_share_persistent_workspace(
    tmp_path: Path,
    task_type: str,
):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")

    async def processor(_workspace):
        return PreparedReviewDelivery.text("审核完成。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )

    submission = service.submit_file(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id=f"message-{task_type}",
        task_type=task_type,
        filename="材料.docx",
        file_bytes=b"docx-content",
    )

    assert submission.task.task_type == task_type
    assert task_type in REVIEW_FILE_TASK_TYPES
    assert task_type in REVIEW_TASK_TYPES
    assert submission.task.payload["input_kind"] == "docx"


def test_text_review_content_is_snapshotted_outside_sqlite_payload(tmp_path: Path):
    db_path = tmp_path / "runtime" / "review.sqlite3"
    repository = TaskRepository(db_path)
    seen = []

    async def processor(workspace):
        seen.append(workspace)
        return PreparedReviewDelivery.text("文字审核完成。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )
    content = "这是一段需要审核的内部材料，不能写入 SQLite 任务载荷。"

    submission = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="text-message-001",
        text=content,
    )
    result = asyncio.run(service.handle(submission.task))

    assert result.status == "completed"
    assert submission.task.task_type == GENERAL_TEXT_REVIEW_TASK_TYPE
    assert content not in json.dumps(submission.task.payload, ensure_ascii=False)
    assert content.encode("utf-8") not in db_path.read_bytes()
    assert seen[0].input_kind == "text"
    assert seen[0].input_file.read_text(encoding="utf-8") == content


def test_multi_file_task_type_cannot_enter_single_review_service(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")

    async def processor(_workspace):
        return PreparedReviewDelivery.text("审核完成。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )

    with pytest.raises(ValueError, match="不支持的单项审核任务类型"):
        service.submit_file(
            channel="wecom",
            sender_userid="user-1",
            sender_name="User One",
            message_id="multi-message-001",
            task_type="review_multi_docx",
            filename="材料.docx",
            file_bytes=b"docx-content",
        )


def test_forged_multi_file_task_is_rejected_by_single_review_handler(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")

    async def processor(_workspace):
        return PreparedReviewDelivery.text("审核完成。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )
    task = repository.submit(
        idempotency_key="forged-multi-task",
        channel="wecom",
        user_id="user-1",
        task_type="review_multi_docx",
        cost_class="review_llm",
        payload={"files": ["one.docx", "two.docx"]},
        max_attempts=1,
        resumable=True,
    )

    with pytest.raises(SafeTaskError, match="invalid_task_payload"):
        asyncio.run(service.handle(task))


def test_valid_task_type_rejects_multi_file_payload_fields(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    processor_called = False

    async def processor(_workspace):
        nonlocal processor_called
        processor_called = True
        return PreparedReviewDelivery.text("审核完成。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-with-files",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )
    forged = replace(
        submission.task,
        payload={**submission.task.payload, "files": ["one.docx", "two.docx"]},
    )

    with pytest.raises(SafeTaskError, match="invalid_task_payload"):
        asyncio.run(service.handle(forged))
    assert processor_called is False


def test_single_review_input_must_stay_under_input_directory(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")

    async def processor(_workspace):
        return PreparedReviewDelivery.text("审核完成。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-output-path",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )
    task_dir = Path(str(submission.task.payload["task_dir"]))
    output_input = task_dir / "output" / "材料.docx"
    output_input.write_bytes(b"forged")
    forged = replace(
        submission.task,
        payload={**submission.task.payload, "input_file": "output/材料.docx"},
    )

    with pytest.raises(SafeTaskError, match="invalid_task_payload"):
        asyncio.run(service.handle(forged))


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


def test_text_parts_are_sent_in_order_and_not_reprocessed_after_completion(
    tmp_path: Path,
):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    calls = {"process": 0}
    sent: list[str] = []

    async def processor(_workspace):
        calls["process"] += 1
        return PreparedReviewDelivery.multipart_text(("第一段", "第二段"))

    async def sender(_recipient: str, value: str) -> bool:
        sent.append(value)
        return True

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
        text_sender=sender,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-multipart",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )

    first = asyncio.run(service.handle(submission.task))
    repeated = asyncio.run(
        service.handle(repository.get_task(submission.task.task_id))
    )

    assert first.status == repeated.status == "completed"
    assert calls == {"process": 1}
    assert sent == ["第一段", "第二段"]
    checkpoint = json.loads(
        (
            Path(str(submission.task.payload["task_dir"])) / "execution.json"
        ).read_text(encoding="utf-8")
    )
    assert checkpoint["result_kind"] == "text_parts"
    assert checkpoint["result_text_parts"] == ["第一段", "第二段"]


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
