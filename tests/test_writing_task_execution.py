from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.platform.app import PlatformApp, PreparedPlatformJob
from app.platform.conversation import ConversationStore
from app.platform.identity import AccessPolicy
from app.platform.models import PlatformResult, RoutedRequest, UploadedFile
from app.platform.storage import JobStore
from app.platform.registry import SkillRegistry
from app.platform.task_execution import (
    ClaimLimits,
    PersistentTaskExecutor,
    SafeTaskError,
    TaskRepository,
)
from app.writing.task_execution import (
    WRITING_TASK_TYPES,
    WritingTaskService,
)


def _result(*, clarification: bool = False) -> PlatformResult:
    return PlatformResult(
        skill_id="writer1",
        output={"title": "测试标题", "body": "测试正文", "sources": []},
        needs_clarification=clarification,
        message="请补充可读取素材。" if clarification else "已生成简报初稿。",
    )


def _service(
    *,
    repository: TaskRepository,
    workspace_root: Path,
    processor,
    text_sender=None,
    result_finalizer=None,
    failure_notifier=None,
) -> WritingTaskService:
    job_store = JobStore(workspace_root)

    def prepare_text(**kwargs) -> PreparedPlatformJob:
        skill_id = "direct_report" if "直报" in kwargs["text"] else "writer1"
        job = job_store.create_job(
            channel=kwargs["channel"],
            sender_userid=kwargs["sender_userid"],
            sender_name=kwargs["sender_name"],
            message=kwargs["text"],
            processing_status="queued",
        )
        return PreparedPlatformJob(
            channel=kwargs["channel"],
            sender_userid=kwargs["sender_userid"],
            sender_name=kwargs["sender_name"],
            route=RoutedRequest(
                skill_id=skill_id,
                confidence=1.0,
                needs_clarification=False,
                message="",
                inputs={"text": kwargs["text"], "urls": [], "files": []},
            ),
            job=job,
            user_text=kwargs["text"],
            ack_message=kwargs.get("ack_message", ""),
        )

    def prepare_structured(**kwargs) -> PreparedPlatformJob:
        job = job_store.create_job(
            channel=kwargs["channel"],
            sender_userid=kwargs["sender_userid"],
            sender_name=kwargs["sender_name"],
            message=kwargs["text"],
            processing_status="queued",
        )
        saved_files = []
        for item in kwargs["files"]:
            target = job.input_dir / item.filename
            target.write_bytes(item.read_bytes())
            saved_files.append(str(target))
        return PreparedPlatformJob(
            channel=kwargs["channel"],
            sender_userid=kwargs["sender_userid"],
            sender_name=kwargs["sender_name"],
            route=RoutedRequest(
                skill_id=kwargs["skill_id"],
                confidence=1.0,
                needs_clarification=False,
                message="",
                inputs={
                    "text": kwargs["text"],
                    "material_text": kwargs["material_text"],
                    "urls": list(kwargs["urls"]),
                    "files": saved_files,
                },
            ),
            job=job,
            user_text=kwargs["text"],
            ack_message="",
        )

    async def default_sender(_recipient: str, _text: str) -> bool:
        return True

    async def default_finalizer(_workspace, _result: PlatformResult) -> None:
        return None

    async def default_failure(_recipient: str, _error_code: str, _task_id: str) -> None:
        return None

    return WritingTaskService(
        repository=repository,
        workspace_root=workspace_root,
        text_preparer=prepare_text,
        structured_preparer=prepare_structured,
        processor=processor,
        text_sender=text_sender or default_sender,
        result_finalizer=result_finalizer or default_finalizer,
        failure_notifier=failure_notifier or default_failure,
    )


def test_text_submission_is_persistent_idempotent_and_keeps_content_out_of_sqlite_payload(
    tmp_path: Path,
):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")

    async def processor(_workspace):
        return _result()

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
    )

    first = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        skill_id="direct_report",
        text="写直报：https://example.com/source",
        ack_message="已受理。",
    )
    duplicate = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        skill_id="direct_report",
        text="写直报：https://example.com/source",
        ack_message="已受理。",
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.task.task_id == first.task.task_id
    assert repository.count_tasks() == 1
    assert "example.com/source" not in json.dumps(first.task.payload, ensure_ascii=False)
    task_dir = Path(str(first.task.payload["task_dir"]))
    request = json.loads((task_dir / "request.json").read_text(encoding="utf-8"))
    assert request["route"]["skill_id"] == "direct_report"
    assert request["user_text"] == "写直报：https://example.com/source"


def test_structured_submission_copies_input_files_into_owned_workspace(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    source = tmp_path / "intake" / "素材.docx"
    source.parent.mkdir()
    source.write_bytes(b"document-content")

    async def processor(_workspace):
        return _result()

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
    )

    submission = service.submit_structured(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-002",
        skill_id="writer1",
        text="突出普惠金融",
        material_text="",
        urls=("https://example.com/source",),
        files=(UploadedFile(filename="素材.docx", stored_path=str(source)),),
    )

    task_dir = Path(str(submission.task.payload["task_dir"]))
    request = json.loads((task_dir / "request.json").read_text(encoding="utf-8"))
    copied = task_dir / request["route"]["inputs"]["files"][0]
    assert copied.read_bytes() == b"document-content"
    assert copied != source
    assert request["route"]["skill_id"] == "writer1"


def test_completed_writing_task_is_not_processed_or_delivered_twice(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    calls = {"process": 0, "deliver": 0, "finalize": 0}

    async def processor(_workspace):
        calls["process"] += 1
        return _result()

    async def sender(_recipient: str, text: str) -> bool:
        calls["deliver"] += 1
        assert text == "测试标题\n\n测试正文"
        return True

    async def finalizer(_workspace, _result):
        calls["finalize"] += 1

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=sender,
        result_finalizer=finalizer,
    )
    submission = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        skill_id="writer1",
        text="写简报：https://example.com",
    )
    executor = PersistentTaskExecutor(
        repository=repository,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="writing-worker",
    )
    for task_type in WRITING_TASK_TYPES:
        executor.register_handler(task_type, service.handle)

    async def scenario():
        completed = await executor.run_once()
        repeated = await service.handle(repository.get_task(submission.task.task_id))
        return completed, repeated

    completed, repeated = asyncio.run(scenario())

    assert completed is not None and completed.status == "completed"
    assert repeated.status == "completed"
    assert calls == {"process": 1, "deliver": 1, "finalize": 1}


def test_restart_does_not_resend_when_writing_delivery_is_uncertain(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    calls = {"process": 0, "deliver": 0}
    failures: list[tuple[str, str, str]] = []

    async def processor(_workspace):
        calls["process"] += 1
        return _result()

    async def interrupted_sender(_recipient: str, _text: str) -> bool:
        calls["deliver"] += 1
        raise asyncio.CancelledError

    async def failure_notifier(recipient: str, code: str, task_id: str) -> None:
        failures.append((recipient, code, task_id))

    first_service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=interrupted_sender,
        failure_notifier=failure_notifier,
    )
    submission = first_service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        skill_id="direct_report",
        text="写直报：https://example.com",
    )

    async def must_not_process(_workspace):
        raise AssertionError("恢复时不应重复生成初稿")

    async def must_not_send(_recipient: str, _text: str) -> bool:
        raise AssertionError("发送状态不确定时不应自动重发")

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await first_service.handle(submission.task)
        restarted = _service(
            repository=repository,
            workspace_root=tmp_path / "workspaces",
            processor=must_not_process,
            text_sender=must_not_send,
            failure_notifier=failure_notifier,
        )
        with pytest.raises(SafeTaskError) as error:
            await restarted.handle(submission.task)
        return error.value

    error = asyncio.run(scenario())

    assert error.safe_error_code == "delivery_status_uncertain"
    assert calls == {"process": 1, "deliver": 1}
    assert failures == [("user-1", "delivery_status_uncertain", submission.task.task_id)]


def test_clarification_result_is_checkpointed_and_finalized_before_delivery(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    order: list[str] = []

    async def processor(_workspace):
        order.append("process")
        return _result(clarification=True)

    async def finalizer(_workspace, result: PlatformResult):
        assert result.needs_clarification is True
        order.append("finalize")

    async def sender(_recipient: str, text: str) -> bool:
        assert text == "请补充可读取素材。"
        order.append("deliver")
        return True

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=sender,
        result_finalizer=finalizer,
    )
    submission = service.submit_structured(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        skill_id="writer1",
        text="写简报",
        material_text="素材",
        urls=(),
        files=(),
    )

    asyncio.run(service.handle(submission.task))

    assert order == ["process", "finalize", "deliver"]
    checkpoint = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["processing_status"] == "completed"
    assert checkpoint["finalization_status"] == "completed"
    assert checkpoint["delivery_status"] == "delivered"
    assert checkpoint["result"]["needs_clarification"] is True


def test_real_platform_job_runs_from_queue_and_records_active_draft(tmp_path: Path):
    jobs_root = tmp_path / "jobs"
    conversations = ConversationStore(tmp_path / "conversations")
    platform_app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行服务小微企业",
                "text": "微众银行通过数字化方式提升小微企业金融服务可得性。",
                "url": url,
            },
            "llm_writer": lambda payload: (
                {"violations": [], "needs_clarification": False, "message": ""}
                if payload.get("schema_name") == "DirectReportCriticResult"
                else {
                    "title": "微众银行提升小微企业金融服务可得性",
                    "body": "微众银行" + "围绕小微企业融资需求持续完善数字化服务能力。" * 30,
                }
            ),
        },
        job_store=JobStore(jobs_root),
        conversation_store=conversations,
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    delivered: list[tuple[str, str]] = []

    async def processor(workspace):
        return await asyncio.to_thread(
            platform_app.execute_prepared_job,
            workspace.prepared,
        )

    async def sender(recipient: str, text: str) -> bool:
        delivered.append((recipient, text))
        return True

    async def finalizer(_workspace, _result):
        return None

    async def failure(_recipient: str, _code: str, _task_id: str):
        return None

    service = WritingTaskService(
        repository=repository,
        workspace_root=jobs_root,
        text_preparer=platform_app.prepare_text_message,
        structured_preparer=platform_app.prepare_structured_request,
        processor=processor,
        text_sender=sender,
        result_finalizer=finalizer,
        failure_notifier=failure,
    )
    submission = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-001",
        skill_id="direct_report",
        text="写直报：https://example.com/source",
    )
    executor = PersistentTaskExecutor(
        repository=repository,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="writing-worker",
    )
    for task_type in WRITING_TASK_TYPES:
        executor.register_handler(task_type, service.handle)

    completed = asyncio.run(executor.run_once())

    assert completed is not None and completed.status == "completed"
    assert delivered and delivered[0][0] == "user-1"
    assert "微众银行提升小微企业金融服务可得性" in delivered[0][1]
    conversation = conversations.get_active_conversation(
        channel="wecom",
        sender_userid="user-1",
    )
    assert conversation is not None
    task_dir = Path(str(submission.task.payload["task_dir"]))
    assert conversation.current_draft.job_id == task_dir.name
    status = json.loads((task_dir / "status.json").read_text(encoding="utf-8"))
    assert status["processing_status"] == "completed"
    assert status["delivery_status"] == "delivered"
