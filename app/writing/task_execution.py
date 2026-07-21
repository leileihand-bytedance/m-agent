from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Awaitable, Callable
from uuid import uuid4

from app.platform.app import PreparedPlatformJob
from app.platform.delivery_state import (
    CHECKPOINT_DELIVERY_STATUSES,
    CONFIRMED_DELIVERED,
    CONFIRMED_NOT_DELIVERED,
    DELIVERY_UNKNOWN,
    DeliveryOutcome,
    aggregate_delivery_status,
    apply_delivery_outcome,
    begin_delivery_attempt,
    normalize_checkpoint_status,
    normalize_delivery_outcome,
)
from app.platform.gateway.wecom import format_text_reply
from app.platform.models import PlatformResult, RoutedRequest, UploadedFile
from app.platform.model_reliability import ModelCallError
from app.platform.storage import JobContext
from app.platform.skill_ids import canonical_skill_id
from app.platform.task_execution import (
    SafeTaskError,
    TaskHandlerResult,
    TaskRecord,
    TaskRepository,
    build_idempotency_key,
)
from app.platform.task_status import update_task_status


QUEUEABLE_WRITING_SKILLS = frozenset(
    {"direct_report", "writer1", "shenyinxie_news"}
)
WRITING_TASK_TYPE_BY_SKILL = {
    "direct_report": "writing_direct_report",
    "writer1": "writing_writer1",
    "shenyinxie_news": "writing_shenyinxie_news",
}
WRITING_TASK_TYPES = tuple(WRITING_TASK_TYPE_BY_SKILL.values())
WRITING_COST_CLASS = "writing_llm"
_WRITING_OUTPUT_SUFFIX_BY_SKILL = {"shenyinxie_news": ".docx"}


@dataclass(frozen=True)
class WritingTaskWorkspace:
    task_id: str
    task_dir: Path
    prepared: PreparedPlatformJob


@dataclass(frozen=True)
class WritingTaskSubmission:
    task: TaskRecord
    created: bool


TextPreparer = Callable[..., PreparedPlatformJob]
StructuredPreparer = Callable[..., PreparedPlatformJob]
WritingProcessor = Callable[[WritingTaskWorkspace], Awaitable[PlatformResult]]
TextSender = Callable[[str, str], Awaitable[DeliveryOutcome | bool]]
AttachmentSender = Callable[[str, Path, Path], Awaitable[DeliveryOutcome | bool]]
ResultFinalizer = Callable[[WritingTaskWorkspace, PlatformResult], Awaitable[None]]
FailureFinalizer = Callable[[WritingTaskWorkspace], Awaitable[None]]
FailureNotifier = Callable[[str, str, str], Awaitable[None]]
TaskStateObserver = Callable[[PreparedPlatformJob, str, str], None]


class WritingTaskService:
    """写作任务的正式 job 快照、持久执行检查点与主动交付。"""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        workspace_root: str | Path,
        text_preparer: TextPreparer,
        structured_preparer: StructuredPreparer,
        processor: WritingProcessor,
        text_sender: TextSender,
        result_finalizer: ResultFinalizer,
        failure_notifier: FailureNotifier,
        attachment_sender: AttachmentSender | None = None,
        failure_finalizer: FailureFinalizer | None = None,
        task_state_observer: TaskStateObserver | None = None,
    ) -> None:
        self._repository = repository
        self._workspace_root = Path(workspace_root).resolve(strict=False)
        self._text_preparer = text_preparer
        self._structured_preparer = structured_preparer
        self._processor = processor
        self._text_sender = text_sender
        self._attachment_sender = attachment_sender
        self._result_finalizer = result_finalizer
        self._failure_notifier = failure_notifier
        self._failure_finalizer = failure_finalizer
        self._task_state_observer = task_state_observer

    def submit_text(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        message_id: str,
        skill_id: str,
        text: str,
        ack_message: str = "",
    ) -> WritingTaskSubmission:
        skill_id = canonical_skill_id(skill_id) or skill_id
        self._validate_submission(skill_id=skill_id, message_id=message_id)
        existing = self._existing_submission(
            channel=channel,
            sender_userid=sender_userid,
            message_id=message_id,
            skill_id=skill_id,
        )
        if existing is not None:
            return WritingTaskSubmission(task=existing, created=False)
        prepared = self._text_preparer(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            text=text,
            ack_message=ack_message,
        )
        return self._submit_prepared(
            prepared=prepared,
            message_id=message_id,
            expected_skill_id=skill_id,
        )

    def submit_structured(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        message_id: str,
        skill_id: str,
        text: str,
        material_text: str,
        urls: tuple[str, ...],
        files: tuple[UploadedFile, ...],
        task_relation: str = "",
        target_task_id: str = "",
        parent_task_id: str = "",
        material_role: str = "",
    ) -> WritingTaskSubmission:
        skill_id = canonical_skill_id(skill_id) or skill_id
        self._validate_submission(skill_id=skill_id, message_id=message_id)
        existing = self._existing_submission(
            channel=channel,
            sender_userid=sender_userid,
            message_id=message_id,
            skill_id=skill_id,
        )
        if existing is not None:
            return WritingTaskSubmission(task=existing, created=False)
        prepared = self._structured_preparer(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            skill_id=skill_id,
            text=text,
            material_text=material_text,
            urls=list(urls),
            files=list(files),
            task_relation=task_relation,
            target_task_id=target_task_id,
            parent_task_id=parent_task_id,
            material_role=material_role,
        )
        return self._submit_prepared(
            prepared=prepared,
            message_id=message_id,
            expected_skill_id=skill_id,
        )

    def has_active_task(self, sender_userid: str) -> bool:
        return self._repository.has_active_task(
            user_id=sender_userid,
            task_types=set(WRITING_TASK_TYPES),
        )

    def _existing_submission(
        self,
        *,
        channel: str,
        sender_userid: str,
        message_id: str,
        skill_id: str,
    ) -> TaskRecord | None:
        return self._repository.find_by_idempotency_key(
            idempotency_key=build_idempotency_key(channel, sender_userid, message_id),
            channel=channel,
            user_id=sender_userid,
            task_type=WRITING_TASK_TYPE_BY_SKILL[skill_id],
            cost_class=WRITING_COST_CLASS,
        )

    def cancel_platform_job(self, *, sender_userid: str, platform_job_id: str) -> str:
        task = self._repository.find_task_by_payload_value(
            user_id=sender_userid,
            key="platform_job_id",
            value=platform_job_id,
            task_types=set(WRITING_TASK_TYPES),
        )
        if task is None:
            return "not_found"
        if task.status in {"queued", "needs_input"}:
            return self._repository.cancel(task.task_id).status
        return task.status

    async def handle(self, task: TaskRecord) -> TaskHandlerResult:
        try:
            workspace = self._workspace_from_task(task)
            checkpoint = self._read_checkpoint(workspace.task_dir)
        except (OSError, ValueError) as exc:
            await self._notify_failure(task.user_id, "invalid_task_payload", task.task_id)
            raise SafeTaskError("invalid_task_payload", retryable=False) from exc

        self._observe_task_state(workspace.prepared, "running", task.task_id)

        if checkpoint["processing_status"] != "completed":
            try:
                result = await self._processor(workspace)
                checkpoint = self._processed_checkpoint(workspace, result)
                self._write_checkpoint(workspace.task_dir, checkpoint)
            except asyncio.CancelledError:
                raise
            except ModelCallError as exc:
                await self._finalize_failure(workspace)
                await self._notify_failure(
                    task.user_id,
                    exc.safe_error_code,
                    task.task_id,
                )
                raise SafeTaskError(exc.safe_error_code, retryable=False) from exc
            except Exception as exc:
                retryable = task.attempts < task.max_attempts
                if not retryable:
                    await self._finalize_failure(workspace)
                    await self._notify_failure(
                        task.user_id,
                        "writing_processing_failed",
                        task.task_id,
                    )
                raise SafeTaskError(
                    "writing_processing_failed",
                    retryable=retryable,
                ) from exc

        result = self._result_from_checkpoint(checkpoint)
        if checkpoint["finalization_status"] != "completed":
            try:
                await self._result_finalizer(workspace, result)
                checkpoint["finalization_status"] = "completed"
                self._write_checkpoint(workspace.task_dir, checkpoint)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retryable = task.attempts < task.max_attempts
                if not retryable:
                    await self._notify_failure(
                        task.user_id,
                        "writing_finalization_failed",
                        task.task_id,
                    )
                raise SafeTaskError(
                    "writing_finalization_failed",
                    retryable=retryable,
                ) from exc

        delivery_items = checkpoint.get("delivery_items")
        if isinstance(delivery_items, list) and delivery_items:
            return await self._deliver_items(task, workspace, checkpoint, delivery_items)

        delivery_status = normalize_checkpoint_status(checkpoint["delivery_status"])
        if delivery_status == CONFIRMED_DELIVERED:
            return TaskHandlerResult.completed()
        if delivery_status == DELIVERY_UNKNOWN:
            checkpoint["delivery_status"] = DELIVERY_UNKNOWN
            self._write_checkpoint(workspace.task_dir, checkpoint)
            update_task_status(
                workspace.task_dir,
                delivery_status="unknown",
                source="writing_task_delivery",
            )
            await self._notify_failure(
                task.user_id,
                "delivery_status_uncertain",
                task.task_id,
            )
            raise SafeTaskError("delivery_status_uncertain", retryable=False)
        if delivery_status == CONFIRMED_NOT_DELIVERED:
            await self._notify_failure(task.user_id, "delivery_not_delivered", task.task_id)
            raise SafeTaskError("delivery_not_delivered", retryable=False)

        attempt_id = begin_delivery_attempt(checkpoint)
        self._write_checkpoint(workspace.task_dir, checkpoint)
        try:
            outcome = normalize_delivery_outcome(
                await self._text_sender(
                    workspace.prepared.sender_userid,
                    format_text_reply(result),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            outcome = DeliveryOutcome(
                status=DELIVERY_UNKNOWN,
                evidence="sender_exception",
                safe_error_code="delivery_ack_unknown",
                attempt_id=attempt_id,
            )
            apply_delivery_outcome(checkpoint, outcome)
            self._write_checkpoint(workspace.task_dir, checkpoint)
            update_task_status(
                workspace.task_dir,
                delivery_status="unknown",
                source="writing_task_delivery",
            )
            await self._notify_failure(
                task.user_id,
                "delivery_status_uncertain",
                task.task_id,
            )
            raise SafeTaskError("delivery_status_uncertain", retryable=False) from exc

        apply_delivery_outcome(checkpoint, outcome)
        self._write_checkpoint(workspace.task_dir, checkpoint)
        if outcome.status == DELIVERY_UNKNOWN:
            update_task_status(
                workspace.task_dir,
                delivery_status="unknown",
                source="writing_task_delivery",
            )
            await self._notify_failure(
                task.user_id,
                "delivery_status_uncertain",
                task.task_id,
            )
            raise SafeTaskError("delivery_status_uncertain", retryable=False)
        if outcome.status == CONFIRMED_NOT_DELIVERED:
            self._write_checkpoint(workspace.task_dir, checkpoint)
            update_task_status(
                workspace.task_dir,
                delivery_status="failed",
                source="writing_task_delivery",
            )
            await self._notify_failure(task.user_id, "delivery_not_delivered", task.task_id)
            raise SafeTaskError("delivery_not_delivered", retryable=False)

        update_task_status(
            workspace.task_dir,
            delivery_status="delivered",
            source="writing_task_delivery",
        )
        return TaskHandlerResult.completed()

    async def _deliver_items(
        self,
        task: TaskRecord,
        workspace: WritingTaskWorkspace,
        checkpoint: dict[str, object],
        delivery_items: list[object],
    ) -> TaskHandlerResult:
        for raw_item in delivery_items:
            if not isinstance(raw_item, dict):
                raise SafeTaskError("invalid_task_payload", retryable=False)
            status = normalize_checkpoint_status(raw_item.get("status", ""))
            raw_item["status"] = status
            if status == CONFIRMED_DELIVERED:
                continue
            if status == DELIVERY_UNKNOWN:
                checkpoint["delivery_status"] = DELIVERY_UNKNOWN
                self._write_checkpoint(workspace.task_dir, checkpoint)
                update_task_status(
                    workspace.task_dir,
                    delivery_status="unknown",
                    source="writing_task_delivery",
                )
                await self._notify_failure(
                    task.user_id,
                    "delivery_status_uncertain",
                    task.task_id,
                )
                raise SafeTaskError("delivery_status_uncertain", retryable=False)
            if status == CONFIRMED_NOT_DELIVERED:
                await self._notify_failure(
                    task.user_id, "delivery_not_delivered", task.task_id
                )
                raise SafeTaskError("delivery_not_delivered", retryable=False)

            attempt_id = begin_delivery_attempt(raw_item)
            checkpoint["delivery_status"] = aggregate_delivery_status(delivery_items)
            self._write_checkpoint(workspace.task_dir, checkpoint)
            try:
                outcome = normalize_delivery_outcome(
                    await self._deliver_item(workspace, raw_item)
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                outcome = DeliveryOutcome(
                    status=DELIVERY_UNKNOWN,
                    evidence="sender_exception",
                    safe_error_code="delivery_ack_unknown",
                    attempt_id=attempt_id,
                )
                apply_delivery_outcome(raw_item, outcome)
                checkpoint["delivery_status"] = DELIVERY_UNKNOWN
                self._write_checkpoint(workspace.task_dir, checkpoint)
                update_task_status(
                    workspace.task_dir,
                    delivery_status="unknown",
                    source="writing_task_delivery",
                )
                await self._notify_failure(
                    task.user_id,
                    "delivery_status_uncertain",
                    task.task_id,
                )
                raise SafeTaskError(
                    "delivery_status_uncertain",
                    retryable=False,
                ) from exc

            apply_delivery_outcome(raw_item, outcome)
            checkpoint["delivery_status"] = aggregate_delivery_status(delivery_items)
            self._write_checkpoint(workspace.task_dir, checkpoint)
            if outcome.status == DELIVERY_UNKNOWN:
                update_task_status(
                    workspace.task_dir,
                    delivery_status="unknown",
                    source="writing_task_delivery",
                )
                await self._notify_failure(
                    task.user_id,
                    "delivery_status_uncertain",
                    task.task_id,
                )
                raise SafeTaskError("delivery_status_uncertain", retryable=False)
            if outcome.status == CONFIRMED_NOT_DELIVERED:
                self._write_checkpoint(workspace.task_dir, checkpoint)
                update_task_status(
                    workspace.task_dir,
                    delivery_status="failed",
                    source="writing_task_delivery",
                )
                await self._notify_failure(
                    task.user_id, "delivery_not_delivered", task.task_id
                )
                raise SafeTaskError("delivery_not_delivered", retryable=False)

        checkpoint["delivery_status"] = CONFIRMED_DELIVERED
        self._write_checkpoint(workspace.task_dir, checkpoint)
        update_task_status(
            workspace.task_dir,
            delivery_status="delivered",
            source="writing_task_delivery",
        )
        return TaskHandlerResult.completed()

    async def _deliver_item(
        self,
        workspace: WritingTaskWorkspace,
        item: dict[str, object],
    ) -> DeliveryOutcome | bool:
        kind = str(item.get("kind", ""))
        if kind == "text":
            return await self._text_sender(
                workspace.prepared.sender_userid,
                str(item.get("text", "")),
            )
        if kind != "attachment" or self._attachment_sender is None:
            raise ValueError("写作任务交付项类型无效")
        relative_path = Path(str(item.get("file", "")))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("写作结果附件引用不安全")
        result_path = (workspace.task_dir / relative_path).resolve(strict=True)
        output_root = (workspace.task_dir / "output").resolve(strict=True)
        if not result_path.is_file() or not result_path.is_relative_to(output_root):
            raise ValueError("写作结果附件超出任务输出目录")
        return await self._attachment_sender(
            workspace.prepared.sender_userid,
            result_path,
            workspace.task_dir,
        )

    def _submit_prepared(
        self,
        *,
        prepared: PreparedPlatformJob,
        message_id: str,
        expected_skill_id: str,
    ) -> WritingTaskSubmission:
        task_dir = prepared.job.job_dir.resolve(strict=True)
        try:
            self._validate_owned_job(prepared, expected_skill_id=expected_skill_id)
            request_path = task_dir / "request.json"
            self._write_request(request_path, prepared)
        except Exception:
            self._remove_owned_workspace(task_dir)
            raise
        payload = {
            "task_dir": str(task_dir),
            "request_file": request_path.name,
            "platform_job_id": prepared.job.job_id,
            "sender_name": prepared.sender_name,
        }
        try:
            task = self._repository.submit(
                idempotency_key=build_idempotency_key(
                    prepared.channel,
                    prepared.sender_userid,
                    message_id,
                ),
                channel=prepared.channel,
                user_id=prepared.sender_userid,
                task_type=WRITING_TASK_TYPE_BY_SKILL[expected_skill_id],
                cost_class=WRITING_COST_CLASS,
                payload=payload,
                max_attempts=2,
                resumable=True,
            )
        except Exception:
            self._remove_owned_workspace(task_dir)
            raise

        created = str(task.payload.get("task_dir", "")) == str(task_dir)
        if not created:
            self._remove_owned_workspace(task_dir)
            return WritingTaskSubmission(task=task, created=False)

        try:
            self._merge_submission_meta(task_dir=task_dir, task=task, message_id=message_id)
        except (OSError, ValueError):
            pass
        self._observe_task_state(prepared, "queued", task.task_id)
        return WritingTaskSubmission(task=task, created=True)

    def _workspace_from_task(self, task: TaskRecord) -> WritingTaskWorkspace:
        if task.task_type not in WRITING_TASK_TYPES:
            raise ValueError("任务类型不是直报或简报写作")
        task_dir = Path(str(task.payload.get("task_dir", ""))).resolve(strict=True)
        if task_dir == self._workspace_root or not task_dir.is_relative_to(self._workspace_root):
            raise ValueError("写作任务目录超出正式写作目录")
        request_name = Path(str(task.payload.get("request_file", "")))
        if request_name.is_absolute() or ".." in request_name.parts:
            raise ValueError("写作任务请求引用不安全")
        request_path = (task_dir / request_name).resolve(strict=True)
        if not request_path.is_file() or not request_path.is_relative_to(task_dir):
            raise ValueError("写作任务请求不存在")
        prepared = self._read_request(task_dir=task_dir, request_path=request_path)
        expected = WRITING_TASK_TYPE_BY_SKILL.get(str(prepared.route.skill_id or ""))
        if expected != task.task_type:
            raise ValueError("写作任务类型与 skill 不一致")
        return WritingTaskWorkspace(
            task_id=task.task_id,
            task_dir=task_dir,
            prepared=prepared,
        )

    def _validate_owned_job(
        self,
        prepared: PreparedPlatformJob,
        *,
        expected_skill_id: str,
    ) -> None:
        task_dir = prepared.job.job_dir.resolve(strict=True)
        if task_dir == self._workspace_root or not task_dir.is_relative_to(self._workspace_root):
            raise ValueError("正式写作任务目录超出允许范围")
        if prepared.route.skill_id != expected_skill_id:
            self._remove_owned_workspace(task_dir)
            raise ValueError("写作路由与提交 skill 不一致")
        if prepared.route.inputs.get("revision") and prepared.task_relation not in {
            "continue",
            "add_material",
            "answer_clarification",
        }:
            self._remove_owned_workspace(task_dir)
            raise ValueError("上一稿改稿不能进入新稿队列")

    @staticmethod
    def _validate_submission(*, skill_id: str, message_id: str) -> None:
        if skill_id not in QUEUEABLE_WRITING_SKILLS:
            raise ValueError(f"不支持排队的写作 skill：{skill_id}")
        if not message_id.strip():
            raise ValueError("message_id 不能为空")

    def _write_request(self, path: Path, prepared: PreparedPlatformJob) -> None:
        inputs = dict(prepared.route.inputs)
        relative_files: list[str] = []
        for raw_path in list(inputs.get("files") or []):
            file_path = Path(str(raw_path)).resolve(strict=True)
            if not file_path.is_file() or not file_path.is_relative_to(prepared.job.input_dir):
                raise ValueError("写作输入文件超出正式 job/input 目录")
            relative_files.append(str(file_path.relative_to(prepared.job.job_dir)))
        inputs["files"] = relative_files
        payload = {
            "schema_version": 2,
            "channel": prepared.channel,
            "sender_userid": prepared.sender_userid,
            "sender_name": prepared.sender_name,
            "user_text": prepared.user_text,
            "ack_message": prepared.ack_message,
            "job_id": prepared.job.job_id,
            "logical_task_id": prepared.logical_task_id,
            "task_relation": prepared.task_relation,
            "parent_task_id": prepared.parent_task_id,
            "route": {
                "skill_id": prepared.route.skill_id,
                "confidence": prepared.route.confidence,
                "needs_clarification": prepared.route.needs_clarification,
                "message": prepared.route.message,
                "inputs": inputs,
            },
        }
        self._write_json_atomic(path, payload)

    @staticmethod
    def _read_request(*, task_dir: Path, request_path: Path) -> PreparedPlatformJob:
        payload = _read_json(request_path, label="写作任务请求")
        if payload.get("schema_version") not in {1, 2}:
            raise ValueError("写作任务请求版本不受支持")
        route_payload = payload.get("route")
        if not isinstance(route_payload, dict):
            raise ValueError("写作任务路由格式错误")
        inputs = route_payload.get("inputs", {})
        if not isinstance(inputs, dict):
            raise ValueError("写作任务输入格式错误")
        restored_inputs = dict(inputs)
        restored_files: list[str] = []
        for raw_path in list(inputs.get("files") or []):
            relative = Path(str(raw_path))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("写作输入文件引用不安全")
            file_path = (task_dir / relative).resolve(strict=True)
            input_dir = (task_dir / "input").resolve(strict=True)
            if not file_path.is_file() or not file_path.is_relative_to(input_dir):
                raise ValueError("写作输入文件不存在或超出 job/input")
            restored_files.append(str(file_path))
        restored_inputs["files"] = restored_files
        job_id = str(payload.get("job_id", "")).strip()
        if not job_id or job_id != task_dir.name:
            raise ValueError("写作 job_id 与任务目录不一致")
        job = JobContext(
            job_id=job_id,
            job_dir=task_dir,
            input_dir=task_dir / "input",
            work_dir=task_dir / "work",
            output_dir=task_dir / "output",
            meta_path=task_dir / "meta.json",
            status_path=task_dir / "status.json",
        )
        return PreparedPlatformJob(
            channel=str(payload.get("channel", "")),
            sender_userid=str(payload.get("sender_userid", "")),
            sender_name=str(payload.get("sender_name", "")),
            route=RoutedRequest(
                skill_id=(
                    str(route_payload["skill_id"])
                    if route_payload.get("skill_id") is not None
                    else None
                ),
                confidence=float(route_payload.get("confidence", 0.0) or 0.0),
                needs_clarification=bool(route_payload.get("needs_clarification", False)),
                message=str(route_payload.get("message", "")),
                inputs=restored_inputs,
            ),
            job=job,
            user_text=str(payload.get("user_text", "")),
            ack_message=str(payload.get("ack_message", "")),
            logical_task_id=str(payload.get("logical_task_id", "")),
            task_relation=str(payload.get("task_relation", "new_task") or "new_task"),
            parent_task_id=str(payload.get("parent_task_id", "")),
        )

    def _observe_task_state(
        self,
        prepared: PreparedPlatformJob,
        status: str,
        execution_task_id: str,
    ) -> None:
        if self._task_state_observer is None:
            return
        try:
            self._task_state_observer(prepared, status, execution_task_id)
        except Exception:
            return

    def _processed_checkpoint(
        self,
        workspace: WritingTaskWorkspace,
        result: PlatformResult,
    ) -> dict[str, object]:
        delivery_items = self._build_delivery_items(workspace, result)
        return {
            "schema_version": 2,
            "processing_status": "completed",
            "finalization_status": "pending",
            "delivery_status": "pending",
            "delivery_items": delivery_items,
            "result": {
                "skill_id": result.skill_id,
                "output": result.output,
                "needs_clarification": result.needs_clarification,
                "message": result.message,
            },
        }

    def _build_delivery_items(
        self,
        workspace: WritingTaskWorkspace,
        result: PlatformResult,
    ) -> list[dict[str, object]]:
        output_file = self._result_output_file(workspace, result)
        if output_file is None:
            return [
                {
                    "kind": "text",
                    "text": format_text_reply(result),
                    "file": "",
                    "status": "pending",
                    "item_id": "text-1",
                }
            ]
        if self._attachment_sender is None:
            raise ValueError("带附件的写作任务未配置附件发送器")
        message = (result.message or "结果文件已生成。").strip()
        return [
            {
                "kind": "text",
                "item_id": "text-1",
                "text": message,
                "file": "",
                "status": "pending",
            },
            {
                "kind": "attachment",
                "item_id": "attachment-1",
                "text": "",
                "file": str(output_file.relative_to(workspace.task_dir)),
                "status": "pending",
            },
        ]

    @staticmethod
    def _result_output_file(
        workspace: WritingTaskWorkspace,
        result: PlatformResult,
    ) -> Path | None:
        suffix = _WRITING_OUTPUT_SUFFIX_BY_SKILL.get(str(result.skill_id or ""))
        if suffix is None or result.needs_clarification:
            return None
        raw_path = str(result.output.get("output_file", "") or "").strip()
        if not raw_path:
            return None
        try:
            path = Path(raw_path).resolve(strict=True)
            output_root = (workspace.task_dir / "output").resolve(strict=True)
            size = path.stat().st_size
        except OSError:
            return None
        if (
            not path.is_file()
            or path.suffix.lower() != suffix
            or not path.is_relative_to(output_root)
            or size <= 0
        ):
            return None
        return path

    @staticmethod
    def _result_from_checkpoint(checkpoint: dict[str, object]) -> PlatformResult:
        result = checkpoint.get("result")
        if not isinstance(result, dict):
            raise ValueError("写作任务结果检查点格式错误")
        output = result.get("output", {})
        if not isinstance(output, dict):
            raise ValueError("写作任务输出检查点格式错误")
        return PlatformResult(
            skill_id=str(result["skill_id"]) if result.get("skill_id") is not None else None,
            output=output,
            needs_clarification=bool(result.get("needs_clarification", False)),
            message=str(result.get("message", "")),
        )

    @staticmethod
    def _read_checkpoint(task_dir: Path) -> dict[str, object]:
        path = task_dir / "execution.json"
        if not path.is_file():
            return {
                "schema_version": 2,
                "processing_status": "pending",
                "finalization_status": "pending",
                "delivery_status": "pending",
                "delivery_items": [],
                "result": {},
            }
        payload = _read_json(path, label="写作任务执行状态")
        if payload.get("schema_version") not in {1, 2}:
            raise ValueError("写作任务执行状态版本不受支持")
        if payload.get("processing_status") != "completed":
            raise ValueError("写作任务处理状态无效")
        if payload.get("finalization_status") not in {"pending", "completed"}:
            raise ValueError("写作任务收尾状态无效")
        payload["delivery_status"] = normalize_checkpoint_status(
            payload.get("delivery_status")
        )
        if payload.get("delivery_status") not in CHECKPOINT_DELIVERY_STATUSES:
            raise ValueError("写作任务交付状态无效")
        delivery_items = payload.get("delivery_items", [])
        if not isinstance(delivery_items, list):
            raise ValueError("写作任务交付清单无效")
        for index, item in enumerate(delivery_items, start=1):
            if not isinstance(item, dict):
                raise ValueError("写作任务交付项无效")
            kind = item.get("kind")
            if kind not in {"text", "attachment"}:
                raise ValueError("写作任务交付项类型无效")
            item["status"] = normalize_checkpoint_status(item.get("status"))
            item.setdefault("item_id", f"item-{index}")
            if item.get("status") not in CHECKPOINT_DELIVERY_STATUSES:
                raise ValueError("写作任务交付项状态无效")
            if kind == "text" and not str(item.get("text", "")).strip():
                raise ValueError("写作任务交付文字为空")
            if kind == "attachment":
                relative_path = Path(str(item.get("file", "")))
                if relative_path.is_absolute() or ".." in relative_path.parts:
                    raise ValueError("写作任务交付附件引用无效")
        payload["schema_version"] = 2
        WritingTaskService._result_from_checkpoint(payload)
        return payload

    @staticmethod
    def _write_checkpoint(task_dir: Path, payload: dict[str, object]) -> None:
        WritingTaskService._write_json_atomic(task_dir / "execution.json", payload)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _remove_owned_workspace(self, task_dir: Path) -> None:
        resolved = task_dir.resolve(strict=False)
        if resolved != self._workspace_root and resolved.is_relative_to(self._workspace_root):
            shutil.rmtree(resolved, ignore_errors=True)

    @staticmethod
    def _merge_submission_meta(
        *,
        task_dir: Path,
        task: TaskRecord,
        message_id: str,
    ) -> None:
        meta_path = task_dir / "meta.json"
        meta = _read_json(meta_path, label="写作任务元数据")
        meta.update(
            {
                "task_id": task.task_id,
                "message_id_hash": build_idempotency_key(
                    task.channel,
                    task.user_id,
                    message_id,
                ),
                "queued_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "queue_mode": "persistent",
            }
        )
        WritingTaskService._write_json_atomic(meta_path, meta)

    async def _finalize_failure(self, workspace: WritingTaskWorkspace) -> None:
        if self._failure_finalizer is None:
            return
        try:
            await self._failure_finalizer(workspace)
        except Exception:
            return

    async def _notify_failure(self, recipient: str, error_code: str, task_id: str) -> None:
        try:
            await self._failure_notifier(recipient, error_code, task_id)
        except Exception:
            return


def _read_json(path: Path, *, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}损坏") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label}格式错误")
    return payload


__all__ = [
    "QUEUEABLE_WRITING_SKILLS",
    "WRITING_COST_CLASS",
    "WRITING_TASK_TYPES",
    "WritingTaskService",
    "WritingTaskSubmission",
    "WritingTaskWorkspace",
]
