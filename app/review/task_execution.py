from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
import json
from pathlib import Path
import re
import shutil
from typing import Awaitable, Callable, Literal, cast
from uuid import uuid4

from app.platform.models import UploadedFile
from app.platform.task_execution import (
    SafeTaskError,
    TaskHandlerResult,
    TaskRecord,
    TaskRepository,
    build_idempotency_key,
)
from app.platform.task_status import update_task_status, write_task_status
from app.review.capabilities import review_capability_for_task_type
from app.review.bot_logging import review_log_context


GENERAL_REVIEW_TASK_TYPE = "review_general_docx"
GENERAL_HTML_REVIEW_TASK_TYPE = "review_general_html"
NEICAN_REVIEW_TASK_TYPE = "review_neican_docx"
HALF_MONTHLY_REVIEW_TASK_TYPE = "review_halfmonthly_docx"
OFFICIAL_FORMAT_REVIEW_TASK_TYPE = "review_official_format_docx"
GENERAL_TEXT_REVIEW_TASK_TYPE = "review_general_text"
PPT_REVIEW_TASK_TYPE = "review_pptx"
MULTI_FILE_REVIEW_TASK_TYPE = "review_multi_file_docx"
GENERAL_REVIEW_COST_CLASS = "review_llm"
ReviewInputKind = Literal["docx", "text", "html", "pptx"]
_MIN_MULTI_FILES = 2
_MAX_MULTI_FILES = 5
_MAX_MULTI_TOTAL_BYTES = 50 * 1024 * 1024
REVIEW_FILE_TASK_TYPES = frozenset(
    {
        GENERAL_REVIEW_TASK_TYPE,
        GENERAL_HTML_REVIEW_TASK_TYPE,
        NEICAN_REVIEW_TASK_TYPE,
        HALF_MONTHLY_REVIEW_TASK_TYPE,
        OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
        PPT_REVIEW_TASK_TYPE,
    }
)
SINGLE_REVIEW_TASK_TYPES = (
    GENERAL_REVIEW_TASK_TYPE,
    GENERAL_HTML_REVIEW_TASK_TYPE,
    NEICAN_REVIEW_TASK_TYPE,
    HALF_MONTHLY_REVIEW_TASK_TYPE,
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
    GENERAL_TEXT_REVIEW_TASK_TYPE,
    PPT_REVIEW_TASK_TYPE,
)
REVIEW_TASK_TYPES = (*SINGLE_REVIEW_TASK_TYPES, MULTI_FILE_REVIEW_TASK_TYPE)
_DOCUMENT_TYPE_BY_TASK_TYPE = {
    GENERAL_REVIEW_TASK_TYPE: "general",
    GENERAL_HTML_REVIEW_TASK_TYPE: "general_html",
    NEICAN_REVIEW_TASK_TYPE: "neican",
    HALF_MONTHLY_REVIEW_TASK_TYPE: "half_monthly",
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE: "official_format",
    GENERAL_TEXT_REVIEW_TASK_TYPE: "general_text",
    PPT_REVIEW_TASK_TYPE: "ppt",
}
_FILE_INPUT_SPEC: dict[str, tuple[ReviewInputKind, frozenset[str]]] = {
    GENERAL_REVIEW_TASK_TYPE: ("docx", frozenset({".docx"})),
    NEICAN_REVIEW_TASK_TYPE: ("docx", frozenset({".docx"})),
    HALF_MONTHLY_REVIEW_TASK_TYPE: ("docx", frozenset({".docx"})),
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE: ("docx", frozenset({".docx"})),
    GENERAL_HTML_REVIEW_TASK_TYPE: ("html", frozenset({".html", ".htm"})),
    PPT_REVIEW_TASK_TYPE: ("pptx", frozenset({".pptx"})),
}


@dataclass(frozen=True)
class GeneralReviewWorkspace:
    task_id: str
    task_dir: Path
    input_file: Path
    filename: str
    sender_userid: str
    sender_name: str
    task_type: str = GENERAL_REVIEW_TASK_TYPE
    input_kind: ReviewInputKind = "docx"


@dataclass(frozen=True)
class MultiFileReviewWorkspace:
    task_id: str
    task_dir: Path
    input_files: tuple[Path, ...]
    filenames: tuple[str, ...]
    sender_userid: str
    sender_name: str
    message_id: str
    primary_file_index: int
    instructions: tuple[str, ...]


@dataclass(frozen=True)
class PreparedMultiFileReviewDelivery:
    summary_text: str
    attachment_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.summary_text.strip():
            raise ValueError("联合审核摘要不能为空")


@dataclass(frozen=True)
class PreparedReviewDelivery:
    kind: Literal["text", "text_parts", "attachment"]
    text: str = ""
    text_parts: tuple[str, ...] = ()
    file_path: Path | None = None

    @classmethod
    def text(cls, value: str) -> "PreparedReviewDelivery":
        if not value.strip():
            raise ValueError("文字交付内容不能为空")
        return cls(kind="text", text=value.strip())

    @classmethod
    def attachment(cls, path: str | Path) -> "PreparedReviewDelivery":
        return cls(kind="attachment", file_path=Path(path))

    @classmethod
    def multipart_text(cls, values: Iterable[str]) -> "PreparedReviewDelivery":
        parts = tuple(value.strip() for value in values if value.strip())
        if not parts:
            raise ValueError("多段文字交付内容不能为空")
        return cls(kind="text_parts", text_parts=parts)


@dataclass(frozen=True)
class ReviewTaskSubmission:
    task: TaskRecord
    created: bool


ReviewProcessor = Callable[[GeneralReviewWorkspace], Awaitable[PreparedReviewDelivery]]
MultiFileReviewProcessor = Callable[
    [MultiFileReviewWorkspace],
    Awaitable[PreparedMultiFileReviewDelivery],
]
TextSender = Callable[[str, str], Awaitable[bool]]
AttachmentSender = Callable[[str, Path, Path], Awaitable[bool]]
FailureNotifier = Callable[[str, str, str], Awaitable[None]]


def _with_review_log_context(handler):
    @wraps(handler)
    async def wrapped(self, task: TaskRecord):
        try:
            capability = review_capability_for_task_type(task.task_type)
        except ValueError:
            return await handler(self, task)
        with review_log_context(capability.id, task.task_id):
            return await handler(self, task)

    return wrapped


class GeneralReviewTaskService:
    """单项文件/文字审核的持久任务工作区和分阶段恢复。"""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        reviews_root: str | Path,
        processor: ReviewProcessor,
        text_sender: TextSender,
        attachment_sender: AttachmentSender,
        failure_notifier: FailureNotifier,
    ) -> None:
        self._repository = repository
        self._reviews_root = Path(reviews_root).resolve(strict=False)
        self._processor = processor
        self._text_sender = text_sender
        self._attachment_sender = attachment_sender
        self._failure_notifier = failure_notifier

    def submit(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        message_id: str,
        filename: str,
        file_bytes: bytes,
    ) -> ReviewTaskSubmission:
        """兼容原单个通用 Word 审核提交接口。"""
        return self.submit_file(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            message_id=message_id,
            task_type=GENERAL_REVIEW_TASK_TYPE,
            filename=filename,
            file_bytes=file_bytes,
        )

    def submit_file(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        message_id: str,
        task_type: str,
        filename: str,
        file_bytes: bytes,
    ) -> ReviewTaskSubmission:
        input_spec = _FILE_INPUT_SPEC.get(task_type)
        if input_spec is None:
            raise ValueError(f"不支持的单项审核任务类型：{task_type}")
        input_kind, allowed_suffixes = input_spec
        if Path(filename).suffix.lower() not in allowed_suffixes:
            allowed = "/".join(sorted(allowed_suffixes))
            raise ValueError(f"{task_type} 文件后缀必须是 {allowed}")
        return self._submit_input(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            message_id=message_id,
            task_type=task_type,
            filename=filename,
            input_kind=input_kind,
            input_bytes=file_bytes,
        )

    def submit_text(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        message_id: str,
        text: str,
    ) -> ReviewTaskSubmission:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("审核文字不能为空")
        return self._submit_input(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            message_id=message_id,
            task_type=GENERAL_TEXT_REVIEW_TASK_TYPE,
            filename="文字消息.txt",
            input_kind="text",
            input_bytes=clean_text.encode("utf-8"),
        )

    def _submit_input(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        message_id: str,
        task_type: str,
        filename: str,
        input_kind: ReviewInputKind,
        input_bytes: bytes,
    ) -> ReviewTaskSubmission:
        if not message_id.strip():
            raise ValueError("message_id 不能为空")
        if not input_bytes:
            raise ValueError("审核输入不能为空")
        idempotency_key = build_idempotency_key(channel, sender_userid, message_id)
        task_dir = self._create_workspace(
            filename=filename,
            input_bytes=input_bytes,
            input_kind=input_kind,
        )
        input_file = next((task_dir / "input").iterdir())
        payload = {
            "task_dir": str(task_dir),
            "input_file": str(input_file.relative_to(task_dir)),
            "input_kind": input_kind,
            "filename": filename,
            "sender_name": sender_name,
        }
        try:
            task = self._repository.submit(
                idempotency_key=idempotency_key,
                channel=channel,
                user_id=sender_userid,
                task_type=task_type,
                cost_class=GENERAL_REVIEW_COST_CLASS,
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
            return ReviewTaskSubmission(task=task, created=False)

        write_task_status(
            task_dir,
            processing_status="queued",
            delivery_status="unknown",
            source="review_task_submission",
            state_version=task.state_version,
        )
        self._write_submission_meta(
            task_dir=task_dir,
            task=task,
            filename=filename,
            document_type=_DOCUMENT_TYPE_BY_TASK_TYPE[task_type],
        )
        return ReviewTaskSubmission(task=task, created=True)

    @_with_review_log_context
    async def handle(self, task: TaskRecord) -> TaskHandlerResult:
        try:
            workspace = self._workspace_from_task(task)
        except (OSError, ValueError) as exc:
            await self._notify_failure(task.user_id, "invalid_task_payload", task.task_id)
            raise SafeTaskError("invalid_task_payload", retryable=False) from exc

        try:
            checkpoint = self._read_checkpoint(workspace.task_dir)
        except ValueError as exc:
            await self._notify_failure(
                task.user_id,
                "invalid_task_checkpoint",
                task.task_id,
            )
            raise SafeTaskError("invalid_task_checkpoint", retryable=False) from exc
        if checkpoint["processing_status"] != "completed":
            update_task_status(
                workspace.task_dir,
                processing_status="processing",
                source="review_task_processing",
            )
            try:
                prepared = await self._processor(workspace)
                checkpoint = self._processed_checkpoint(workspace, prepared)
                self._write_checkpoint(workspace.task_dir, checkpoint)
                update_task_status(
                    workspace.task_dir,
                    processing_status="completed",
                    source="review_task_processing",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retryable = task.attempts < task.max_attempts
                update_task_status(
                    workspace.task_dir,
                    processing_status="queued" if retryable else "failed",
                    source="review_task_processing",
                )
                if not retryable:
                    await self._notify_failure(
                        task.user_id,
                        "review_processing_failed",
                        task.task_id,
                    )
                raise SafeTaskError(
                    "review_processing_failed",
                    retryable=retryable,
                ) from exc

        delivery_status = str(checkpoint["delivery_status"])
        if delivery_status == "delivered":
            return TaskHandlerResult.completed()
        if delivery_status == "sending":
            await self._notify_failure(
                task.user_id,
                "delivery_status_uncertain",
                task.task_id,
            )
            raise SafeTaskError("delivery_status_uncertain", retryable=False)
        if delivery_status == "failed":
            await self._notify_failure(task.user_id, "delivery_failed", task.task_id)
            raise SafeTaskError("delivery_failed", retryable=False)

        checkpoint["delivery_status"] = "sending"
        self._write_checkpoint(workspace.task_dir, checkpoint)
        try:
            delivered = await self._deliver(workspace, checkpoint)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._notify_failure(
                task.user_id,
                "delivery_status_uncertain",
                task.task_id,
            )
            raise SafeTaskError("delivery_status_uncertain", retryable=False) from exc

        if not delivered:
            checkpoint["delivery_status"] = "failed"
            self._write_checkpoint(workspace.task_dir, checkpoint)
            update_task_status(
                workspace.task_dir,
                delivery_status="failed",
                source="review_task_delivery",
            )
            await self._notify_failure(task.user_id, "delivery_failed", task.task_id)
            raise SafeTaskError("delivery_failed", retryable=False)

        checkpoint["delivery_status"] = "delivered"
        self._write_checkpoint(workspace.task_dir, checkpoint)
        update_task_status(
            workspace.task_dir,
            delivery_status="delivered",
            source="review_task_delivery",
        )
        return TaskHandlerResult.completed()

    async def _deliver(
        self,
        workspace: GeneralReviewWorkspace,
        checkpoint: dict[str, object],
    ) -> bool:
        if checkpoint["result_kind"] == "text":
            return await self._text_sender(
                workspace.sender_userid,
                str(checkpoint["result_text"]),
            )
        if checkpoint["result_kind"] == "text_parts":
            parts = checkpoint["result_text_parts"]
            if not isinstance(parts, list):
                raise ValueError("审核任务多段文字结果无效")
            for part in parts:
                if not await self._text_sender(workspace.sender_userid, str(part)):
                    return False
            return True
        relative_path = Path(str(checkpoint["result_file"]))
        result_path = (workspace.task_dir / relative_path).resolve(strict=True)
        if not result_path.is_relative_to(workspace.task_dir):
            raise ValueError("审核结果文件超出任务目录")
        return await self._attachment_sender(
            workspace.sender_userid,
            result_path,
            workspace.task_dir,
        )

    def _processed_checkpoint(
        self,
        workspace: GeneralReviewWorkspace,
        prepared: PreparedReviewDelivery,
    ) -> dict[str, object]:
        if prepared.kind == "text":
            if not prepared.text.strip():
                raise ValueError("文字审核结果不能为空")
            return {
                "schema_version": 1,
                "processing_status": "completed",
                "delivery_status": "pending",
                "result_kind": "text",
                "result_text": prepared.text.strip(),
                "result_text_parts": [],
                "result_file": "",
            }
        if prepared.kind == "text_parts":
            parts = tuple(value.strip() for value in prepared.text_parts if value.strip())
            if not parts:
                raise ValueError("多段文字审核结果不能为空")
            return {
                "schema_version": 1,
                "processing_status": "completed",
                "delivery_status": "pending",
                "result_kind": "text_parts",
                "result_text": "",
                "result_text_parts": list(parts),
                "result_file": "",
            }
        if prepared.file_path is None:
            raise ValueError("附件审核结果缺少文件")
        result_path = prepared.file_path.resolve(strict=True)
        if not result_path.is_relative_to(workspace.task_dir):
            raise ValueError("审核结果文件超出任务目录")
        return {
            "schema_version": 1,
            "processing_status": "completed",
            "delivery_status": "pending",
            "result_kind": "attachment",
            "result_text": "",
            "result_text_parts": [],
            "result_file": str(result_path.relative_to(workspace.task_dir)),
        }

    def _workspace_from_task(self, task: TaskRecord) -> GeneralReviewWorkspace:
        if task.task_type not in SINGLE_REVIEW_TASK_TYPES:
            raise ValueError("任务类型不是受支持的单项审核")
        allowed_payload_fields = {
            "task_dir",
            "input_file",
            "input_kind",
            "filename",
            "sender_name",
        }
        unexpected_fields = set(task.payload) - allowed_payload_fields
        if unexpected_fields:
            raise ValueError("审核任务包含未授权载荷字段")
        task_dir = Path(str(task.payload.get("task_dir", ""))).resolve(strict=True)
        if task_dir == self._reviews_root or not task_dir.is_relative_to(self._reviews_root):
            raise ValueError("任务目录超出审核根目录")
        relative_input = Path(str(task.payload.get("input_file", "")))
        if relative_input.is_absolute() or ".." in relative_input.parts:
            raise ValueError("审核输入文件引用不安全")
        input_file = (task_dir / relative_input).resolve(strict=True)
        input_root = (task_dir / "input").resolve(strict=True)
        if not input_file.is_file() or not input_file.is_relative_to(input_root):
            raise ValueError("审核输入文件不存在或超出任务目录")
        filename = str(task.payload.get("filename", "")).strip()
        if not filename:
            raise ValueError("审核文件名不能为空")
        default_kind = "docx" if task.task_type == GENERAL_REVIEW_TASK_TYPE else ""
        input_kind = str(task.payload.get("input_kind", "") or default_kind)
        if task.task_type == GENERAL_TEXT_REVIEW_TASK_TYPE:
            expected_kind = "text"
        else:
            input_spec = _FILE_INPUT_SPEC.get(task.task_type)
            if input_spec is None:
                raise ValueError("审核任务输入类型未登记")
            expected_kind = input_spec[0]
        if input_kind != expected_kind:
            raise ValueError("审核任务输入类型与任务类型不一致")
        if input_kind == "text" and input_file.suffix.lower() != ".txt":
            raise ValueError("审核文字快照类型无效")
        if input_kind != "text" and input_file.suffix.lower() not in _FILE_INPUT_SPEC[
            task.task_type
        ][1]:
            raise ValueError("审核任务文件类型无效")
        return GeneralReviewWorkspace(
            task_id=task.task_id,
            task_dir=task_dir,
            input_file=input_file,
            filename=filename,
            sender_userid=task.user_id,
            sender_name=str(task.payload.get("sender_name", "")).strip() or task.user_id,
            task_type=task.task_type,
            input_kind=cast(ReviewInputKind, input_kind),
        )

    def _create_workspace(
        self,
        *,
        filename: str,
        input_bytes: bytes,
        input_kind: ReviewInputKind,
    ) -> Path:
        now = datetime.now().astimezone()
        month_dir = self._reviews_root / f"{now:%Y}" / f"{now:%m}"
        month_dir.mkdir(parents=True, exist_ok=True)
        task_dir = month_dir / f"queued-{uuid4().hex}"
        input_dir = task_dir / "input"
        output_dir = task_dir / "output"
        input_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir(parents=True, exist_ok=False)
        safe_name = _safe_input_name(filename, input_kind=input_kind)
        (input_dir / safe_name).write_bytes(input_bytes)
        return task_dir

    def _remove_owned_workspace(self, task_dir: Path) -> None:
        resolved = task_dir.resolve(strict=False)
        if resolved != self._reviews_root and resolved.is_relative_to(self._reviews_root):
            shutil.rmtree(resolved, ignore_errors=True)

    @staticmethod
    def _read_checkpoint(task_dir: Path) -> dict[str, object]:
        path = task_dir / "execution.json"
        if not path.is_file():
            return {
                "schema_version": 1,
                "processing_status": "pending",
                "delivery_status": "pending",
                "result_kind": "",
                "result_text": "",
                "result_text_parts": [],
                "result_file": "",
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("审核任务执行状态损坏") from exc
        if not isinstance(payload, dict):
            raise ValueError("审核任务执行状态格式错误")
        if payload.get("schema_version") != 1:
            raise ValueError("审核任务执行状态版本不受支持")
        if payload.get("processing_status") != "completed":
            raise ValueError("审核任务处理状态无效")
        if payload.get("delivery_status") not in {
            "pending",
            "sending",
            "delivered",
            "failed",
        }:
            raise ValueError("审核任务交付状态无效")
        result_kind = payload.get("result_kind")
        if result_kind == "text":
            if not str(payload.get("result_text", "")).strip():
                raise ValueError("审核任务文字结果为空")
        elif result_kind == "text_parts":
            parts = payload.get("result_text_parts")
            if (
                not isinstance(parts, list)
                or not parts
                or any(not isinstance(part, str) or not part.strip() for part in parts)
            ):
                raise ValueError("审核任务多段文字结果为空")
        elif result_kind == "attachment":
            relative_path = Path(str(payload.get("result_file", "")))
            if (
                not str(relative_path)
                or relative_path.is_absolute()
                or ".." in relative_path.parts
            ):
                raise ValueError("审核任务附件引用无效")
        else:
            raise ValueError("审核任务结果类型无效")
        return payload

    @staticmethod
    def _write_checkpoint(task_dir: Path, payload: dict[str, object]) -> None:
        path = task_dir / "execution.json"
        temporary = task_dir / f".execution.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _write_submission_meta(
        *,
        task_dir: Path,
        task: TaskRecord,
        filename: str,
        document_type: str,
    ) -> None:
        capability = review_capability_for_task_type(task.task_type)
        meta_path = task_dir / "meta.json"
        temporary = task_dir / f".meta.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "capability_id": capability.id,
                    "capability_name": capability.name,
                    "original_filename": filename,
                    "sender_userid": task.user_id,
                    "message_id": "",
                    "queued_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "document_type": document_type,
                    "queue_mode": "persistent",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(meta_path)

    async def _notify_failure(self, recipient: str, error_code: str, task_id: str) -> None:
        try:
            await self._failure_notifier(recipient, error_code, task_id)
        except Exception:
            return


class MultiFileReviewTaskService:
    """多文件联合审核的输入快照、处理检查点和逐项交付恢复。"""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        reviews_root: str | Path,
        processor: MultiFileReviewProcessor,
        text_sender: TextSender,
        attachment_sender: AttachmentSender,
        failure_notifier: FailureNotifier,
    ) -> None:
        self._repository = repository
        self._reviews_root = Path(reviews_root).resolve(strict=False)
        self._processor = processor
        self._text_sender = text_sender
        self._attachment_sender = attachment_sender
        self._failure_notifier = failure_notifier

    def submit(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        message_id: str,
        files: tuple[UploadedFile, ...],
        primary_file_index: int,
        instructions: tuple[str, ...] = (),
    ) -> ReviewTaskSubmission:
        self._validate_submission(
            message_id=message_id,
            files=files,
            primary_file_index=primary_file_index,
        )
        task_dir, input_files = self._create_workspace(files)
        request_path = task_dir / "request.json"
        clean_instructions = tuple(item.strip() for item in instructions if item.strip())
        self._write_request(
            request_path,
            task_dir=task_dir,
            input_files=input_files,
            filenames=tuple(file.filename for file in files),
            message_id=message_id,
            primary_file_index=primary_file_index,
            instructions=clean_instructions,
        )
        payload = {
            "task_dir": str(task_dir),
            "request_file": request_path.name,
            "sender_name": sender_name,
        }
        try:
            task = self._repository.submit(
                idempotency_key=build_idempotency_key(channel, sender_userid, message_id),
                channel=channel,
                user_id=sender_userid,
                task_type=MULTI_FILE_REVIEW_TASK_TYPE,
                cost_class=GENERAL_REVIEW_COST_CLASS,
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
            return ReviewTaskSubmission(task=task, created=False)

        write_task_status(
            task_dir,
            processing_status="queued",
            delivery_status="unknown",
            source="review_multi_file_submission",
            state_version=task.state_version,
        )
        self._write_submission_meta(
            task_dir=task_dir,
            task=task,
            file_count=len(files),
        )
        return ReviewTaskSubmission(task=task, created=True)

    @_with_review_log_context
    async def handle(self, task: TaskRecord) -> TaskHandlerResult:
        try:
            workspace = self._workspace_from_task(task)
            checkpoint = self._read_checkpoint(workspace.task_dir)
        except (OSError, ValueError) as exc:
            await self._notify_failure(task.user_id, "invalid_task_payload", task.task_id)
            raise SafeTaskError("invalid_task_payload", retryable=False) from exc

        if checkpoint["processing_status"] != "completed":
            update_task_status(
                workspace.task_dir,
                processing_status="processing",
                source="review_multi_file_processing",
            )
            try:
                prepared = await self._processor(workspace)
                checkpoint = self._processed_checkpoint(workspace, prepared)
                self._write_checkpoint(workspace.task_dir, checkpoint)
                update_task_status(
                    workspace.task_dir,
                    processing_status="completed",
                    source="review_multi_file_processing",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retryable = task.attempts < task.max_attempts
                update_task_status(
                    workspace.task_dir,
                    processing_status="queued" if retryable else "failed",
                    source="review_multi_file_processing",
                )
                if not retryable:
                    await self._notify_failure(
                        task.user_id,
                        "review_processing_failed",
                        task.task_id,
                    )
                raise SafeTaskError(
                    "review_processing_failed",
                    retryable=retryable,
                ) from exc

        items = checkpoint.get("delivery_items")
        if not isinstance(items, list) or not items:
            await self._notify_failure(task.user_id, "invalid_task_payload", task.task_id)
            raise SafeTaskError("invalid_task_payload", retryable=False)

        for raw_item in items:
            if not isinstance(raw_item, dict):
                raise SafeTaskError("invalid_task_payload", retryable=False)
            status = str(raw_item.get("status", ""))
            if status == "delivered":
                continue
            if status == "sending":
                await self._notify_failure(
                    task.user_id,
                    "delivery_status_uncertain",
                    task.task_id,
                )
                raise SafeTaskError("delivery_status_uncertain", retryable=False)
            if status == "failed":
                await self._notify_failure(task.user_id, "delivery_failed", task.task_id)
                raise SafeTaskError("delivery_failed", retryable=False)

            raw_item["status"] = "sending"
            self._write_checkpoint(workspace.task_dir, checkpoint)
            try:
                delivered = await self._deliver_item(workspace, raw_item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._notify_failure(
                    task.user_id,
                    "delivery_status_uncertain",
                    task.task_id,
                )
                raise SafeTaskError(
                    "delivery_status_uncertain",
                    retryable=False,
                ) from exc

            if not delivered:
                raw_item["status"] = "failed"
                checkpoint["delivery_status"] = "failed"
                self._write_checkpoint(workspace.task_dir, checkpoint)
                update_task_status(
                    workspace.task_dir,
                    delivery_status="failed",
                    source="review_multi_file_delivery",
                )
                await self._notify_failure(task.user_id, "delivery_failed", task.task_id)
                raise SafeTaskError("delivery_failed", retryable=False)

            raw_item["status"] = "delivered"
            self._write_checkpoint(workspace.task_dir, checkpoint)

        checkpoint["delivery_status"] = "delivered"
        self._write_checkpoint(workspace.task_dir, checkpoint)
        update_task_status(
            workspace.task_dir,
            delivery_status="delivered",
            source="review_multi_file_delivery",
        )
        return TaskHandlerResult.completed()

    async def _deliver_item(
        self,
        workspace: MultiFileReviewWorkspace,
        item: dict[str, object],
    ) -> bool:
        kind = str(item.get("kind", ""))
        if kind == "text":
            return await self._text_sender(
                workspace.sender_userid,
                str(item.get("text", "")),
            )
        if kind != "attachment":
            raise ValueError("联合审核交付项类型无效")
        relative_path = Path(str(item.get("file", "")))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("联合审核附件引用不安全")
        result_path = (workspace.task_dir / relative_path).resolve(strict=True)
        output_root = (workspace.task_dir / "output").resolve(strict=True)
        if not result_path.is_file() or not result_path.is_relative_to(output_root):
            raise ValueError("联合审核附件超出任务输出目录")
        return await self._attachment_sender(
            workspace.sender_userid,
            result_path,
            workspace.task_dir,
        )

    def _workspace_from_task(self, task: TaskRecord) -> MultiFileReviewWorkspace:
        if task.task_type != MULTI_FILE_REVIEW_TASK_TYPE:
            raise ValueError("任务类型不是多文件联合审核")
        allowed_payload_fields = {"task_dir", "request_file", "sender_name"}
        if set(task.payload) - allowed_payload_fields:
            raise ValueError("联合审核任务包含未授权载荷字段")
        task_dir = Path(str(task.payload.get("task_dir", ""))).resolve(strict=True)
        if task_dir == self._reviews_root or not task_dir.is_relative_to(self._reviews_root):
            raise ValueError("联合审核任务目录超出审核根目录")
        request_name = Path(str(task.payload.get("request_file", "")))
        if request_name.is_absolute() or ".." in request_name.parts:
            raise ValueError("联合审核请求引用不安全")
        request_path = (task_dir / request_name).resolve(strict=True)
        if not request_path.is_file() or not request_path.is_relative_to(task_dir):
            raise ValueError("联合审核请求不存在")
        request = self._read_request(task_dir=task_dir, path=request_path)
        return MultiFileReviewWorkspace(
            task_id=task.task_id,
            task_dir=task_dir,
            input_files=request["input_files"],
            filenames=request["filenames"],
            sender_userid=task.user_id,
            sender_name=str(task.payload.get("sender_name", "")).strip() or task.user_id,
            message_id=request["message_id"],
            primary_file_index=request["primary_file_index"],
            instructions=request["instructions"],
        )

    @staticmethod
    def _validate_submission(
        *,
        message_id: str,
        files: tuple[UploadedFile, ...],
        primary_file_index: int,
    ) -> None:
        if not message_id.strip():
            raise ValueError("message_id 不能为空")
        if not _MIN_MULTI_FILES <= len(files) <= _MAX_MULTI_FILES:
            raise ValueError("多文件联合审核只支持 2 至 5 份文件")
        if not 0 <= primary_file_index < len(files):
            raise ValueError("联合审核主文件序号无效")
        total_bytes = 0
        for file in files:
            if Path(file.filename).suffix.lower() != ".docx":
                raise ValueError("多文件联合审核只支持 DOCX")
            size = file.size_bytes
            if size <= 0:
                raise ValueError("联合审核输入文件不能为空")
            total_bytes += size
        if total_bytes > _MAX_MULTI_TOTAL_BYTES:
            raise ValueError("联合审核文件总大小超过 50MB")

    def _create_workspace(
        self,
        files: tuple[UploadedFile, ...],
    ) -> tuple[Path, tuple[Path, ...]]:
        now = datetime.now().astimezone()
        month_dir = self._reviews_root / f"{now:%Y}" / f"{now:%m}"
        month_dir.mkdir(parents=True, exist_ok=True)
        task_dir = month_dir / f"multi-queued-{uuid4().hex}"
        input_dir = task_dir / "input"
        output_dir = task_dir / "output"
        input_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir(parents=True, exist_ok=False)
        input_paths: list[Path] = []
        try:
            for index, file in enumerate(files, start=1):
                path = input_dir / f"{index:02d}_{_safe_input_name(file.filename, input_kind='docx')}"
                path.write_bytes(file.read_bytes())
                input_paths.append(path)
        except Exception:
            self._remove_owned_workspace(task_dir)
            raise
        return task_dir, tuple(input_paths)

    @staticmethod
    def _write_request(
        path: Path,
        *,
        task_dir: Path,
        input_files: tuple[Path, ...],
        filenames: tuple[str, ...],
        message_id: str,
        primary_file_index: int,
        instructions: tuple[str, ...],
    ) -> None:
        payload = {
            "schema_version": 1,
            "input_files": [str(item.relative_to(task_dir)) for item in input_files],
            "filenames": list(filenames),
            "message_id": message_id,
            "primary_file_index": primary_file_index,
            "instructions": list(instructions),
        }
        MultiFileReviewTaskService._write_json_atomic(path, payload)

    @staticmethod
    def _read_request(*, task_dir: Path, path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("联合审核请求损坏") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("联合审核请求版本无效")
        raw_inputs = payload.get("input_files")
        raw_filenames = payload.get("filenames")
        if not isinstance(raw_inputs, list) or not isinstance(raw_filenames, list):
            raise ValueError("联合审核请求文件清单无效")
        if len(raw_inputs) != len(raw_filenames) or not _MIN_MULTI_FILES <= len(raw_inputs) <= _MAX_MULTI_FILES:
            raise ValueError("联合审核请求文件数量无效")
        input_root = (task_dir / "input").resolve(strict=True)
        input_files: list[Path] = []
        filenames: list[str] = []
        for raw_input, raw_filename in zip(raw_inputs, raw_filenames):
            relative = Path(str(raw_input))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("联合审核输入引用不安全")
            input_path = (task_dir / relative).resolve(strict=True)
            if (
                not input_path.is_file()
                or not input_path.is_relative_to(input_root)
                or input_path.suffix.lower() != ".docx"
            ):
                raise ValueError("联合审核输入文件无效")
            filename = str(raw_filename).strip()
            if not filename or Path(filename).suffix.lower() != ".docx":
                raise ValueError("联合审核原始文件名无效")
            input_files.append(input_path)
            filenames.append(filename)
        primary_file_index = payload.get("primary_file_index")
        if not isinstance(primary_file_index, int) or not 0 <= primary_file_index < len(input_files):
            raise ValueError("联合审核主文件序号无效")
        raw_instructions = payload.get("instructions", [])
        if not isinstance(raw_instructions, list) or any(not isinstance(item, str) for item in raw_instructions):
            raise ValueError("联合审核补充要求无效")
        message_id = str(payload.get("message_id", "")).strip()
        if not message_id:
            raise ValueError("联合审核消息标识无效")
        return {
            "input_files": tuple(input_files),
            "filenames": tuple(filenames),
            "message_id": message_id,
            "primary_file_index": primary_file_index,
            "instructions": tuple(item.strip() for item in raw_instructions if item.strip()),
        }

    @staticmethod
    def _processed_checkpoint(
        workspace: MultiFileReviewWorkspace,
        prepared: PreparedMultiFileReviewDelivery,
    ) -> dict[str, object]:
        items: list[dict[str, object]] = [
            {
                "kind": "text",
                "text": prepared.summary_text.strip(),
                "file": "",
                "status": "pending",
            }
        ]
        output_root = (workspace.task_dir / "output").resolve(strict=True)
        for path in prepared.attachment_paths:
            resolved = path.resolve(strict=True)
            if not resolved.is_file() or not resolved.is_relative_to(output_root):
                raise ValueError("联合审核结果附件超出任务输出目录")
            items.append(
                {
                    "kind": "attachment",
                    "text": "",
                    "file": str(resolved.relative_to(workspace.task_dir)),
                    "status": "pending",
                }
            )
        return {
            "schema_version": 1,
            "processing_status": "completed",
            "delivery_status": "pending",
            "delivery_items": items,
        }

    @staticmethod
    def _read_checkpoint(task_dir: Path) -> dict[str, object]:
        path = task_dir / "execution.json"
        if not path.is_file():
            return {
                "schema_version": 1,
                "processing_status": "pending",
                "delivery_status": "pending",
                "delivery_items": [],
            }
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("联合审核执行状态损坏") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("联合审核执行状态版本无效")
        if payload.get("processing_status") != "completed":
            raise ValueError("联合审核处理状态无效")
        if payload.get("delivery_status") not in {"pending", "delivered", "failed"}:
            raise ValueError("联合审核交付状态无效")
        items = payload.get("delivery_items")
        if not isinstance(items, list) or not items:
            raise ValueError("联合审核交付清单为空")
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("联合审核交付项无效")
            if item.get("kind") not in {"text", "attachment"}:
                raise ValueError("联合审核交付项类型无效")
            if item.get("status") not in {"pending", "sending", "delivered", "failed"}:
                raise ValueError("联合审核交付项状态无效")
            if item.get("kind") == "text" and not str(item.get("text", "")).strip():
                raise ValueError("联合审核交付摘要为空")
            if item.get("kind") == "attachment":
                relative = Path(str(item.get("file", "")))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("联合审核交付附件引用无效")
        return payload

    @staticmethod
    def _write_checkpoint(task_dir: Path, payload: dict[str, object]) -> None:
        MultiFileReviewTaskService._write_json_atomic(task_dir / "execution.json", payload)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
        temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)

    @staticmethod
    def _write_submission_meta(
        *,
        task_dir: Path,
        task: TaskRecord,
        file_count: int,
    ) -> None:
        capability = review_capability_for_task_type(task.task_type)
        MultiFileReviewTaskService._write_json_atomic(
            task_dir / "meta.json",
            {
                "task_id": task.task_id,
                "task_type": task.task_type,
                "capability_id": capability.id,
                "capability_name": capability.name,
                "sender_userid": task.user_id,
                "queued_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "document_type": capability.document_type,
                "file_count": file_count,
                "queue_mode": "persistent",
            },
        )

    def _remove_owned_workspace(self, task_dir: Path) -> None:
        resolved = task_dir.resolve(strict=False)
        if resolved != self._reviews_root and resolved.is_relative_to(self._reviews_root):
            shutil.rmtree(resolved, ignore_errors=True)

    async def _notify_failure(self, recipient: str, error_code: str, task_id: str) -> None:
        try:
            await self._failure_notifier(recipient, error_code, task_id)
        except Exception:
            return


def _safe_input_name(filename: str, *, input_kind: ReviewInputKind) -> str:
    fallback_by_kind = {
        "text": "文字消息.txt",
        "docx": "uploaded.docx",
        "html": "uploaded.html",
        "pptx": "uploaded.pptx",
    }
    fallback = fallback_by_kind[input_kind]
    path = Path(filename or fallback)
    stem = path.stem or "uploaded"
    safe_stem = re.sub(r"[^\w一-鿿\-_]", "_", stem)
    if input_kind == "text":
        suffix = ".txt"
    elif input_kind == "docx":
        suffix = ".docx"
    elif input_kind == "html":
        suffix = path.suffix.lower()
        if suffix not in {".html", ".htm"}:
            suffix = ".html"
    else:
        suffix = ".pptx"
    return f"{safe_stem}{suffix}"


__all__ = [
    "GENERAL_HTML_REVIEW_TASK_TYPE",
    "GENERAL_TEXT_REVIEW_TASK_TYPE",
    "GENERAL_REVIEW_COST_CLASS",
    "GENERAL_REVIEW_TASK_TYPE",
    "HALF_MONTHLY_REVIEW_TASK_TYPE",
    "NEICAN_REVIEW_TASK_TYPE",
    "OFFICIAL_FORMAT_REVIEW_TASK_TYPE",
    "PPT_REVIEW_TASK_TYPE",
    "MULTI_FILE_REVIEW_TASK_TYPE",
    "REVIEW_FILE_TASK_TYPES",
    "REVIEW_TASK_TYPES",
    "SINGLE_REVIEW_TASK_TYPES",
    "GeneralReviewTaskService",
    "GeneralReviewWorkspace",
    "MultiFileReviewTaskService",
    "MultiFileReviewWorkspace",
    "PreparedMultiFileReviewDelivery",
    "PreparedReviewDelivery",
    "ReviewTaskSubmission",
]
