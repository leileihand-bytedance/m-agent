from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.platform.app import PlatformApp, PreparedPlatformJob
from app.platform.conversation import ConversationStore
from app.platform.delivery_state import DeliveryOutcome
from app.platform.identity import AccessPolicy
from app.platform.models import PlatformResult, RoutedRequest, UploadedFile
from app.platform.model_reliability import ModelCallError
from app.platform.storage import JobStore
from app.platform.registry import SkillRegistry
from app.platform.task_execution import (
    ClaimLimits,
    PersistentTaskExecutor,
    SafeTaskError,
    TaskRepository,
)
from app.writing.task_execution import (
    QUEUEABLE_WRITING_SKILLS,
    WRITING_TASK_TYPE_BY_SKILL,
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
    attachment_sender=None,
    result_finalizer=None,
    failure_notifier=None,
) -> WritingTaskService:
    job_store = JobStore(workspace_root)

    def prepare_text(**kwargs) -> PreparedPlatformJob:
        skill_id = "direct_report" if "直报" in kwargs["text"] else "writer1"
        is_revision = "继续改稿" in kwargs["text"]
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
                inputs={
                    "text": kwargs["text"],
                    "urls": [],
                    "files": [],
                    "revision": is_revision,
                },
            ),
            job=job,
            user_text=kwargs["text"],
            ack_message=kwargs.get("ack_message", ""),
            logical_task_id="logical-task-001" if is_revision else "",
            task_relation="continue" if is_revision else "new_task",
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

    async def default_attachment_sender(
        _recipient: str,
        _path: Path,
        _task_dir: Path,
        _skill_id: str,
    ) -> bool:
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
        attachment_sender=attachment_sender or default_attachment_sender,
        result_finalizer=result_finalizer or default_finalizer,
        failure_notifier=failure_notifier or default_failure,
    )


def test_shenyinxie_news_is_registered_as_queueable_writing_skill():
    assert "shenyinxie_news" in QUEUEABLE_WRITING_SKILLS
    assert WRITING_TASK_TYPE_BY_SKILL["shenyinxie_news"] == "writing_shenyinxie_news"


def test_internal_weekly_is_registered_as_queueable_writing_skill():
    assert "internal_weekly" in QUEUEABLE_WRITING_SKILLS
    assert WRITING_TASK_TYPE_BY_SKILL["internal_weekly"] == "writing_internal_weekly"


def test_research_synthesis_is_registered_as_queueable_writing_skill():
    assert "research_synthesis" in QUEUEABLE_WRITING_SKILLS
    assert (
        WRITING_TASK_TYPE_BY_SKILL["research_synthesis"]
        == "writing_research_synthesis"
    )


def test_writer2_is_not_registered_as_queueable_writing_skill():
    assert "writer2" not in QUEUEABLE_WRITING_SKILLS
    assert "writer2" not in WRITING_TASK_TYPE_BY_SKILL
    assert "writing_writer2" not in WRITING_TASK_TYPES


def test_direct_report_word_delivery_keeps_chat_draft_and_sends_attachment(
    tmp_path: Path,
):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    delivered: list[tuple[str, str]] = []

    async def processor(workspace):
        output_path = workspace.task_dir / "output" / "直报正式文档.docx"
        output_path.write_bytes(b"word-result")
        return PlatformResult(
            skill_id="direct_report",
            output={
                "title": "直报标题",
                "body": "直报正文",
                "sources": [],
                "output_file": str(output_path),
            },
            needs_clarification=False,
            message="已生成直报 Word 文档。",
        )

    async def text_sender(_recipient: str, text: str) -> bool:
        delivered.append(("text", text))
        return True

    async def attachment_sender(
        _recipient: str,
        path: Path,
        task_dir: Path,
        skill_id: str,
    ) -> bool:
        assert path == task_dir / "output" / "直报正式文档.docx"
        assert skill_id == "direct_report"
        delivered.append(("attachment", path.name))
        return True

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=text_sender,
        attachment_sender=attachment_sender,
    )
    submission = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-direct-report-word-001",
        skill_id="direct_report",
        text="请写直报并输出Word",
    )

    result = asyncio.run(service.handle(submission.task))

    assert result.status == "completed"
    assert delivered == [
        ("text", "直报标题\n\n直报正文"),
        ("attachment", "直报正式文档.docx"),
    ]


def test_shenyinxie_delivery_checkpoints_text_and_word_separately(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    delivered: list[tuple[str, str]] = []

    async def processor(workspace):
        output_path = workspace.task_dir / "output" / "深银协动态.docx"
        output_path.write_bytes(b"word-result")
        return PlatformResult(
            skill_id="shenyinxie_news",
            output={"output_file": str(output_path)},
            needs_clarification=False,
            message="本期已整理 2 篇报道。",
        )

    async def text_sender(_recipient: str, text: str) -> bool:
        delivered.append(("text", text))
        return True

    async def attachment_sender(
        _recipient: str,
        path: Path,
        task_dir: Path,
        skill_id: str,
    ) -> bool:
        assert path == task_dir / "output" / "深银协动态.docx"
        assert skill_id == "shenyinxie_news"
        delivered.append(("attachment", path.name))
        return True

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=text_sender,
        attachment_sender=attachment_sender,
    )
    submission = service.submit_structured(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-shenyinxie-001",
        skill_id="shenyinxie_news",
        text="生成7月上半月深银协动态",
        material_text="",
        urls=(),
        files=(),
    )

    async def scenario():
        first = await service.handle(submission.task)
        second = await service.handle(submission.task)
        return first, second

    first, second = asyncio.run(scenario())

    assert first.status == "completed"
    assert second.status == "completed"
    assert delivered == [
        ("text", "本期已整理 2 篇报道。"),
        ("attachment", "深银协动态.docx"),
    ]
    checkpoint = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["status"] for item in checkpoint["delivery_items"]] == [
        "confirmed_delivered",
        "confirmed_delivered",
    ]


def test_internal_weekly_checkpoints_text_review_and_manifest_separately(
    tmp_path: Path,
):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    delivered: list[tuple[str, str]] = []

    async def processor(workspace):
        review_path = workspace.task_dir / "output" / "内参周报-内容核对稿.md"
        manifest_path = workspace.task_dir / "output" / "内参周报-溯源清单.json"
        review_path.write_text("# 内参周报", encoding="utf-8")
        manifest_path.write_text("{}", encoding="utf-8")
        return PlatformResult(
            skill_id="internal_weekly",
            output={
                "output_file": str(review_path),
                "manifest_file": str(manifest_path),
            },
            needs_clarification=False,
            message="已生成内容核对稿和溯源清单，请完成人工核对。",
        )

    async def text_sender(_recipient: str, text: str) -> bool:
        delivered.append(("text", text))
        return True

    async def attachment_sender(
        _recipient: str,
        path: Path,
        task_dir: Path,
        skill_id: str,
    ) -> bool:
        assert path.parent == task_dir / "output"
        assert skill_id == "internal_weekly"
        delivered.append(("attachment", path.name))
        return True

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=text_sender,
        attachment_sender=attachment_sender,
    )
    submission = service.submit_structured(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-internal-weekly-001",
        skill_id="internal_weekly",
        text="生成本周内参周报",
        material_text="",
        urls=(),
        files=(),
    )

    result = asyncio.run(service.handle(submission.task))

    assert result.status == "completed"
    assert delivered == [
        ("text", "已生成内容核对稿和溯源清单，请完成人工核对。"),
        ("attachment", "内参周报-内容核对稿.md"),
        ("attachment", "内参周报-溯源清单.json"),
    ]
    checkpoint = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["item_id"] for item in checkpoint["delivery_items"]] == [
        "text-1",
        "attachment-1",
        "attachment-2",
    ]
    assert [item["status"] for item in checkpoint["delivery_items"]] == [
        "confirmed_delivered",
        "confirmed_delivered",
        "confirmed_delivered",
    ]


def test_research_synthesis_checkpoints_completion_and_word_separately(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    delivered: list[tuple[str, str]] = []

    async def processor(workspace):
        output_path = workspace.task_dir / "output" / "综合调研初稿.docx"
        output_path.write_bytes(b"word-result")
        return PlatformResult(
            skill_id="research_synthesis",
            output={"output_file": str(output_path)},
            needs_clarification=False,
            message="综合调研初稿已生成。",
        )

    async def text_sender(_recipient: str, text: str) -> bool:
        delivered.append(("text", text))
        return True

    async def attachment_sender(
        _recipient: str,
        path: Path,
        task_dir: Path,
        skill_id: str,
    ) -> bool:
        assert path == task_dir / "output" / "综合调研初稿.docx"
        assert skill_id == "research_synthesis"
        delivered.append(("attachment", path.name))
        return True

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=text_sender,
        attachment_sender=attachment_sender,
    )
    submission = service.submit_structured(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-research-synthesis-001",
        skill_id="research_synthesis",
        text="开始写综合调研",
        material_text="",
        urls=(),
        files=(),
    )

    result = asyncio.run(service.handle(submission.task))

    assert result.status == "completed"
    assert delivered == [
        ("text", "综合调研初稿已生成。"),
        ("attachment", "综合调研初稿.docx"),
    ]
    checkpoint = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert [item["status"] for item in checkpoint["delivery_items"]] == [
        "confirmed_delivered",
        "confirmed_delivered",
    ]
    delivery_log = capsys.readouterr().out
    assert "item_id=text-1 kind=text status=confirmed_delivered" in delivery_log
    assert (
        "item_id=attachment-1 kind=attachment status=confirmed_delivered"
        in delivery_log
    )
    assert "综合调研初稿.docx" not in delivery_log


@pytest.mark.parametrize("skill_id", ["research_synthesis", "shenyinxie_news"])
def test_attachment_only_writing_skills_do_not_report_success_without_output(
    tmp_path: Path,
    skill_id: str,
):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    delivered: list[str] = []

    async def processor(_workspace):
        return PlatformResult(
            skill_id=skill_id,
            output={"output_file": ""},
            needs_clarification=False,
            message="结果已经生成。",
        )

    async def text_sender(_recipient: str, text: str) -> bool:
        delivered.append(text)
        return True

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=text_sender,
    )
    submission = service.submit_structured(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id=f"message-{skill_id}-missing-output",
        skill_id=skill_id,
        text="开始生成",
        material_text="",
        urls=(),
        files=(),
    )

    with pytest.raises(SafeTaskError) as error:
        asyncio.run(service.handle(submission.task))

    assert error.value.safe_error_code == "writing_processing_failed"
    assert delivered == []


def test_internal_weekly_restart_does_not_repeat_confirmed_delivery_items(
    tmp_path: Path,
):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    calls = {"process": 0, "text": 0, "attachments": []}
    failures: list[str] = []

    async def processor(workspace):
        calls["process"] += 1
        review_path = workspace.task_dir / "output" / "内参周报-内容核对稿.md"
        manifest_path = workspace.task_dir / "output" / "内参周报-溯源清单.json"
        review_path.write_text("# 内参周报", encoding="utf-8")
        manifest_path.write_text("{}", encoding="utf-8")
        return PlatformResult(
            skill_id="internal_weekly",
            output={
                "output_file": str(review_path),
                "manifest_file": str(manifest_path),
            },
            needs_clarification=False,
            message="已生成内容核对稿和溯源清单。",
        )

    async def text_sender(_recipient: str, _text: str) -> bool:
        calls["text"] += 1
        return True

    async def interrupted_attachment(
        _recipient: str,
        path: Path,
        _task_dir: Path,
        skill_id: str,
    ) -> bool:
        assert skill_id == "internal_weekly"
        calls["attachments"].append(path.suffix)
        if path.suffix == ".json":
            raise asyncio.CancelledError
        return True

    async def failure_notifier(_recipient: str, code: str, _task_id: str) -> None:
        failures.append(code)

    first_service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=text_sender,
        attachment_sender=interrupted_attachment,
        failure_notifier=failure_notifier,
    )
    submission = first_service.submit_structured(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-internal-weekly-002",
        skill_id="internal_weekly",
        text="生成本周内参周报",
        material_text="",
        urls=(),
        files=(),
    )

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await first_service.handle(submission.task)

        async def must_not_process(_workspace):
            raise AssertionError("恢复交付时不应重新生成内参周报")

        async def must_not_send_text(_recipient: str, _text: str) -> bool:
            raise AssertionError("已确认发送的说明文字不应重复发送")

        async def must_not_send_attachment(
            _recipient: str,
            _path: Path,
            _task_dir: Path,
            _skill_id: str,
        ) -> bool:
            raise AssertionError("发送状态不确定的附件不应自动重发")

        restarted = _service(
            repository=repository,
            workspace_root=tmp_path / "workspaces",
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
    assert calls == {"process": 1, "text": 1, "attachments": [".md", ".json"]}
    assert failures == ["delivery_status_uncertain"]


def test_shenyinxie_restart_does_not_repeat_delivered_text_or_uncertain_attachment(
    tmp_path: Path,
):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    calls = {"process": 0, "text": 0, "attachment": 0}
    failures: list[str] = []

    async def processor(workspace):
        calls["process"] += 1
        output_path = workspace.task_dir / "output" / "深银协动态.docx"
        output_path.write_bytes(b"word-result")
        return PlatformResult(
            skill_id="shenyinxie_news",
            output={"output_file": str(output_path)},
            needs_clarification=False,
            message="本期已整理 1 篇报道。",
        )

    async def text_sender(_recipient: str, _text: str) -> bool:
        calls["text"] += 1
        return True

    async def interrupted_attachment(
        _recipient: str,
        _path: Path,
        _task_dir: Path,
        _skill_id: str,
    ) -> bool:
        calls["attachment"] += 1
        raise asyncio.CancelledError

    async def failure_notifier(_recipient: str, code: str, _task_id: str) -> None:
        failures.append(code)

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=text_sender,
        attachment_sender=interrupted_attachment,
        failure_notifier=failure_notifier,
    )
    submission = service.submit_structured(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-shenyinxie-002",
        skill_id="shenyinxie_news",
        text="生成7月下半月深银协动态",
        material_text="",
        urls=(),
        files=(),
    )

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await service.handle(submission.task)

        async def must_not_process(_workspace):
            raise AssertionError("恢复时不应重新生成深银协动态")

        async def must_not_send_text(_recipient: str, _text: str) -> bool:
            raise AssertionError("已发送的说明文字不应重复发送")

        async def must_not_send_attachment(
            _recipient: str,
            _path: Path,
            _task_dir: Path,
            _skill_id: str,
        ) -> bool:
            raise AssertionError("发送状态不确定的附件不应自动重发")

        restarted = _service(
            repository=repository,
            workspace_root=tmp_path / "workspaces",
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


def test_previous_draft_revision_can_enter_persistent_queue(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")

    async def processor(_workspace):
        return _result()

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
    )
    submission = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="revision-message-001",
        skill_id="writer1",
        text="继续改稿：把第二段压缩一点",
    )

    assert submission.created is True
    request_path = Path(str(submission.task.payload["task_dir"])) / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["task_relation"] == "continue"
    assert request["route"]["inputs"]["revision"] is True


def test_model_failure_uses_safe_code_without_rerunning_whole_task(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    notifications: list[str] = []

    async def processor(_workspace):
        raise ModelCallError("model_timeout", attempts=2, retryable=True)

    async def notify(_recipient: str, error_code: str, _task_id: str) -> None:
        notifications.append(error_code)

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        failure_notifier=notify,
    )
    submission = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="model-timeout-message",
        skill_id="writer1",
        text="写简报：https://example.com",
    )

    with pytest.raises(SafeTaskError) as captured:
        asyncio.run(service.handle(submission.task))

    assert captured.value.safe_error_code == "model_timeout"
    assert captured.value.retryable is False
    assert notifications == ["model_timeout"]


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


def test_writing_sender_outcome_persists_unknown_delivery_evidence(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")
    failures: list[str] = []

    async def processor(_workspace):
        return _result()

    async def sender(_recipient: str, _text: str):
        return DeliveryOutcome(
            status="delivery_unknown",
            evidence="sdk_ack_timeout",
            safe_error_code="delivery_ack_timeout",
        )

    async def failure_notifier(_recipient: str, code: str, _task_id: str) -> None:
        failures.append(code)

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=sender,
        failure_notifier=failure_notifier,
    )
    submission = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-outcome-unknown",
        skill_id="writer1",
        text="写简报：https://example.com",
    )

    with pytest.raises(SafeTaskError) as error:
        asyncio.run(service.handle(submission.task))

    assert error.value.safe_error_code == "delivery_status_uncertain"
    checkpoint = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["schema_version"] == 2
    assert checkpoint["delivery_status"] == "delivery_unknown"
    item = checkpoint["delivery_items"][0]
    assert item["status"] == "delivery_unknown"
    assert item["evidence"] == "sdk_ack_timeout"
    assert item["safe_error_code"] == "delivery_ack_timeout"
    assert item["attempt_id"].startswith("delivery-")
    status = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "status.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["delivery_status"] == "unknown"
    assert failures == ["delivery_status_uncertain"]


def test_writing_confirmed_rejection_is_recoverable_not_delivered(tmp_path: Path):
    repository = TaskRepository(tmp_path / "runtime" / "writing.sqlite3")

    async def processor(_workspace):
        return _result()

    async def sender(_recipient: str, _text: str):
        return DeliveryOutcome(
            status="confirmed_not_delivered",
            evidence="sdk_ack_rejected",
            safe_error_code="wecom_rejected",
        )

    service = _service(
        repository=repository,
        workspace_root=tmp_path / "workspaces",
        processor=processor,
        text_sender=sender,
    )
    submission = service.submit_text(
        channel="wecom",
        sender_userid="user-1",
        sender_name="User One",
        message_id="message-outcome-rejected",
        skill_id="writer1",
        text="写简报：https://example.com",
    )

    with pytest.raises(SafeTaskError) as error:
        asyncio.run(service.handle(submission.task))

    assert error.value.safe_error_code == "delivery_not_delivered"
    checkpoint = json.loads(
        (Path(str(submission.task.payload["task_dir"])) / "execution.json").read_text(
            encoding="utf-8"
        )
    )
    assert checkpoint["delivery_status"] == "confirmed_not_delivered"
    assert checkpoint["delivery_items"][0]["evidence"] == "sdk_ack_rejected"


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
    assert checkpoint["delivery_status"] == "confirmed_delivered"
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
