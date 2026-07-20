from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from datetime import datetime

import pytest

from app.platform.models import UploadedFile
from app.platform.task_execution import (
    ClaimLimits,
    PersistentTaskExecutor,
    SafeTaskError,
    TaskRepository,
)
from app.review.task_execution import (
    GENERAL_HTML_REVIEW_TASK_TYPE,
    GENERAL_TEXT_REVIEW_TASK_TYPE,
    HALF_MONTHLY_REVIEW_TASK_TYPE,
    NEICAN_REVIEW_TASK_TYPE,
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
    PPT_REVIEW_TASK_TYPE,
    REVIEW_FILE_TASK_TYPES,
    REVIEW_TASK_TYPES,
    GeneralReviewTaskService,
    MULTI_FILE_REVIEW_TASK_TYPE,
    MultiFileReviewTaskService,
    PreparedMultiFileReviewDelivery,
    PreparedReviewDelivery,
)
from app.review.bot_logging import log_extra, setup_logging


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


def _multi_service(
    *,
    repository: TaskRepository,
    reviews_root: Path,
    processor,
    text_sender=None,
    attachment_sender=None,
    failure_notifier=None,
) -> MultiFileReviewTaskService:
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

    return MultiFileReviewTaskService(
        repository=repository,
        reviews_root=reviews_root,
        processor=processor,
        text_sender=text_sender or default_text_sender,
        attachment_sender=attachment_sender or default_attachment_sender,
        failure_notifier=failure_notifier or default_failure_notifier,
    )


def test_multi_file_review_is_registered_as_persistent_task_type():
    assert MULTI_FILE_REVIEW_TASK_TYPE == "review_multi_file_docx"
    assert MULTI_FILE_REVIEW_TASK_TYPE in REVIEW_TASK_TYPES
    assert MULTI_FILE_REVIEW_TASK_TYPE not in REVIEW_FILE_TASK_TYPES


def test_multi_file_submission_snapshots_all_inputs_and_is_idempotent(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")

    async def processor(_workspace):
        return PreparedMultiFileReviewDelivery(summary_text="完成")

    service = _multi_service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )
    files = (
        UploadedFile(filename="正文.docx", content=b"main-document"),
        UploadedFile(filename="附件1.docx", content=b"attachment-document"),
    )

    first = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="multi-message-001",
        files=files,
        primary_file_index=0,
        instructions=("同时核对附件引用",),
    )
    duplicate = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="multi-message-001",
        files=files,
        primary_file_index=0,
        instructions=("同时核对附件引用",),
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.task.task_id == first.task.task_id
    assert repository.count_tasks() == 1
    task_dir = Path(str(first.task.payload["task_dir"]))
    snapshotted = sorted((task_dir / "input").glob("*.docx"))
    assert [path.read_bytes() for path in snapshotted] == [
        b"main-document",
        b"attachment-document",
    ]
    assert "main-document" not in json.dumps(first.task.payload, ensure_ascii=False)
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["queue_mode"] == "persistent"
    assert meta["capability_id"] == "multi_file_review"


def test_multi_file_delivery_checkpoints_summary_and_each_attachment(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    delivered: list[tuple[str, str]] = []

    async def processor(workspace):
        first = workspace.task_dir / "output" / "marked_正文.docx"
        second = workspace.task_dir / "output" / "marked_附件1.docx"
        first.write_bytes(b"marked-main")
        second.write_bytes(b"marked-attachment")
        return PreparedMultiFileReviewDelivery(
            summary_text="联合审核完成。",
            attachment_paths=(first, second),
        )

    async def text_sender(_recipient: str, text: str) -> bool:
        delivered.append(("text", text))
        return True

    async def attachment_sender(_recipient: str, path: Path, task_dir: Path) -> bool:
        assert path.is_relative_to(task_dir / "output")
        delivered.append(("attachment", path.name))
        return True

    service = _multi_service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
        text_sender=text_sender,
        attachment_sender=attachment_sender,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="multi-message-002",
        files=(
            UploadedFile(filename="正文.docx", content=b"main"),
            UploadedFile(filename="附件1.docx", content=b"attachment"),
        ),
        primary_file_index=0,
        instructions=(),
    )

    async def scenario():
        first = await service.handle(submission.task)
        second = await service.handle(submission.task)
        return first, second

    first, second = asyncio.run(scenario())

    assert first.status == "completed"
    assert second.status == "completed"
    assert delivered == [
        ("text", "联合审核完成。"),
        ("attachment", "marked_正文.docx"),
        ("attachment", "marked_附件1.docx"),
    ]
    task_dir = Path(str(submission.task.payload["task_dir"]))
    checkpoint = json.loads((task_dir / "execution.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in checkpoint["delivery_items"]] == [
        "delivered",
        "delivered",
        "delivered",
    ]


def test_multi_file_restart_stops_after_uncertain_attachment_without_resending_summary(
    tmp_path: Path,
):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    calls = {"process": 0, "text": 0, "attachment": 0}
    failures: list[str] = []

    async def processor(workspace):
        calls["process"] += 1
        output = workspace.task_dir / "output" / "marked_正文.docx"
        output.write_bytes(b"marked-main")
        return PreparedMultiFileReviewDelivery(
            summary_text="联合审核完成。",
            attachment_paths=(output,),
        )

    async def text_sender(_recipient: str, _text: str) -> bool:
        calls["text"] += 1
        return True

    async def interrupted_attachment(_recipient: str, _path: Path, _task_dir: Path) -> bool:
        calls["attachment"] += 1
        raise asyncio.CancelledError

    async def failure_notifier(_recipient: str, code: str, _task_id: str) -> None:
        failures.append(code)

    service = _multi_service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
        text_sender=text_sender,
        attachment_sender=interrupted_attachment,
        failure_notifier=failure_notifier,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="multi-message-003",
        files=(
            UploadedFile(filename="正文.docx", content=b"main"),
            UploadedFile(filename="附件1.docx", content=b"attachment"),
        ),
        primary_file_index=0,
        instructions=(),
    )

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await service.handle(submission.task)

        async def must_not_process(_workspace):
            raise AssertionError("恢复时不应重新执行联合审核")

        async def must_not_send_text(_recipient: str, _text: str) -> bool:
            raise AssertionError("已发送摘要不应重复发送")

        async def must_not_send_attachment(
            _recipient: str,
            _path: Path,
            _task_dir: Path,
        ) -> bool:
            raise AssertionError("发送状态不确定的附件不应自动重发")

        restarted = _multi_service(
            repository=repository,
            reviews_root=tmp_path / "reviews",
            processor=must_not_process,
            text_sender=must_not_send_text,
            attachment_sender=must_not_send_attachment,
            failure_notifier=failure_notifier,
        )
        with pytest.raises(SafeTaskError) as error:
            await restarted.handle(submission.task)
        return error.value

    error = asyncio.run(scenario())

    assert error.safe_error_code == "delivery_status_uncertain"
    assert calls == {"process": 1, "text": 1, "attachment": 1}
    assert failures == ["delivery_status_uncertain"]


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
    assert meta["capability_id"] == "general_word_review"
    assert meta["capability_name"] == "通用 Word 审核"


def test_single_review_delivery_logs_keep_capability_and_task_context(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")
    logger = setup_logging(tmp_path / "logs")

    async def processor(_workspace):
        logger.info("processing", extra=log_extra("user-1", "User One"))
        return PreparedReviewDelivery.text("审核完成。")

    async def text_sender(_recipient: str, _text: str) -> bool:
        logger.info("delivery", extra=log_extra("user-1", "User One"))
        return True

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
        text_sender=text_sender,
    )
    submission = service.submit(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-context",
        filename="材料.docx",
        file_bytes=b"docx-content",
    )

    asyncio.run(service.handle(submission.task))

    now = datetime.now()
    capability_log = (
        tmp_path
        / "logs"
        / "review-capabilities"
        / "general_word_review"
        / f"{now:%Y-%m-%d}.log"
    )
    content = capability_log.read_text(encoding="utf-8")
    assert "processing" in content
    assert "delivery" in content
    assert f"capability=general_word_review|task={submission.task.task_id}" in content


def test_ppt_submission_freezes_single_pptx_outside_sqlite_payload(tmp_path: Path):
    database_path = tmp_path / "runtime" / "review.sqlite3"
    repository = TaskRepository(database_path)

    async def processor(_workspace):
        return PreparedReviewDelivery.multipart_text(("PPT审核完成。",))

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )
    secret_body = b"pptx-secret-body"

    submission = service.submit_file(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="ppt-message-001",
        task_type=PPT_REVIEW_TASK_TYPE,
        filename="经营汇报.pptx",
        file_bytes=secret_body,
    )

    assert submission.task.payload["input_kind"] == "pptx"
    assert secret_body not in database_path.read_bytes()
    input_path = Path(str(submission.task.payload["task_dir"])) / str(
        submission.task.payload["input_file"]
    )
    assert input_path.suffix == ".pptx"
    assert input_path.read_bytes() == secret_body
    assert PPT_REVIEW_TASK_TYPE in REVIEW_FILE_TASK_TYPES
    assert PPT_REVIEW_TASK_TYPE in REVIEW_TASK_TYPES


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


def test_html_review_submission_freezes_input_without_sqlite_body(tmp_path: Path):
    db_path = tmp_path / "runtime" / "review.sqlite3"
    repository = TaskRepository(db_path)
    seen = []

    async def processor(workspace):
        seen.append(workspace)
        return PreparedReviewDelivery.text("HTML审核完成。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )
    content = b"<p>unique-html-body-20260716</p>"

    first = service.submit_file(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="html-message-001",
        task_type=GENERAL_HTML_REVIEW_TASK_TYPE,
        filename="report.htm",
        file_bytes=content,
    )
    duplicate = service.submit_file(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="html-message-001",
        task_type=GENERAL_HTML_REVIEW_TASK_TYPE,
        filename="report.htm",
        file_bytes=content,
    )
    result = asyncio.run(service.handle(first.task))

    assert result.status == "completed"
    assert first.created is True
    assert duplicate.created is False
    assert duplicate.task.task_id == first.task.task_id
    assert repository.count_tasks() == 1
    assert first.task.payload["input_kind"] == "html"
    assert content not in db_path.read_bytes()
    task_dir = Path(str(first.task.payload["task_dir"]))
    input_file = task_dir / str(first.task.payload["input_file"])
    assert input_file.name == "report.htm"
    assert input_file.read_bytes() == content
    assert seen[0].input_kind == "html"


def test_html_review_task_rejects_non_html_suffix(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "review.sqlite3")

    async def processor(_workspace):
        return PreparedReviewDelivery.text("HTML审核完成。")

    service = _service(
        repository=repository,
        reviews_root=tmp_path / "reviews",
        processor=processor,
    )

    with pytest.raises(ValueError, match="文件后缀"):
        service.submit_file(
            channel="wecom",
            sender_userid="user-1",
            sender_name="User One",
            message_id="html-message-002",
            task_type=GENERAL_HTML_REVIEW_TASK_TYPE,
            filename="forged.docx",
            file_bytes=b"<p>HTML body</p>",
        )


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
    status = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "status.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["processing_status"] == "completed"
    assert status["delivery_status"] == "delivered"
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
