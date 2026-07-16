from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
from typing import Awaitable, Callable, Literal
from uuid import uuid4

from app.platform.task_execution import (
    SafeTaskError,
    TaskHandlerResult,
    TaskRecord,
    TaskRepository,
    build_idempotency_key,
)
from app.platform.task_status import update_task_status, write_task_status


GENERAL_REVIEW_TASK_TYPE = "review_general_docx"
NEICAN_REVIEW_TASK_TYPE = "review_neican_docx"
HALF_MONTHLY_REVIEW_TASK_TYPE = "review_halfmonthly_docx"
OFFICIAL_FORMAT_REVIEW_TASK_TYPE = "review_official_format_docx"
GENERAL_TEXT_REVIEW_TASK_TYPE = "review_general_text"
GENERAL_REVIEW_COST_CLASS = "review_llm"
REVIEW_FILE_TASK_TYPES = frozenset(
    {
        GENERAL_REVIEW_TASK_TYPE,
        NEICAN_REVIEW_TASK_TYPE,
        HALF_MONTHLY_REVIEW_TASK_TYPE,
        OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
    }
)
REVIEW_TASK_TYPES = (
    GENERAL_REVIEW_TASK_TYPE,
    NEICAN_REVIEW_TASK_TYPE,
    HALF_MONTHLY_REVIEW_TASK_TYPE,
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE,
    GENERAL_TEXT_REVIEW_TASK_TYPE,
)
_DOCUMENT_TYPE_BY_TASK_TYPE = {
    GENERAL_REVIEW_TASK_TYPE: "general",
    NEICAN_REVIEW_TASK_TYPE: "neican",
    HALF_MONTHLY_REVIEW_TASK_TYPE: "half_monthly",
    OFFICIAL_FORMAT_REVIEW_TASK_TYPE: "official_format",
    GENERAL_TEXT_REVIEW_TASK_TYPE: "general_text",
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
    input_kind: Literal["docx", "text"] = "docx"


@dataclass(frozen=True)
class PreparedReviewDelivery:
    kind: Literal["text", "attachment"]
    text: str = ""
    file_path: Path | None = None

    @classmethod
    def text(cls, value: str) -> "PreparedReviewDelivery":
        if not value.strip():
            raise ValueError("文字交付内容不能为空")
        return cls(kind="text", text=value.strip())

    @classmethod
    def attachment(cls, path: str | Path) -> "PreparedReviewDelivery":
        return cls(kind="attachment", file_path=Path(path))


@dataclass(frozen=True)
class ReviewTaskSubmission:
    task: TaskRecord
    created: bool


ReviewProcessor = Callable[[GeneralReviewWorkspace], Awaitable[PreparedReviewDelivery]]
TextSender = Callable[[str, str], Awaitable[bool]]
AttachmentSender = Callable[[str, Path, Path], Awaitable[bool]]
FailureNotifier = Callable[[str, str, str], Awaitable[None]]


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
        if task_type not in REVIEW_FILE_TASK_TYPES:
            raise ValueError(f"不支持的单项审核任务类型：{task_type}")
        if Path(filename).suffix.lower() != ".docx":
            raise ValueError("单项文件审核只支持 .docx")
        return self._submit_input(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            message_id=message_id,
            task_type=task_type,
            filename=filename,
            input_kind="docx",
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
        input_kind: Literal["docx", "text"],
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
            try:
                prepared = await self._processor(workspace)
                checkpoint = self._processed_checkpoint(workspace, prepared)
                self._write_checkpoint(workspace.task_dir, checkpoint)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retryable = task.attempts < task.max_attempts
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
            "result_file": str(result_path.relative_to(workspace.task_dir)),
        }

    def _workspace_from_task(self, task: TaskRecord) -> GeneralReviewWorkspace:
        if task.task_type not in REVIEW_TASK_TYPES:
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
        expected_kind = "text" if task.task_type == GENERAL_TEXT_REVIEW_TASK_TYPE else "docx"
        if input_kind != expected_kind:
            raise ValueError("审核任务输入类型与任务类型不一致")
        if input_kind == "docx" and input_file.suffix.lower() != ".docx":
            raise ValueError("审核任务文件类型无效")
        if input_kind == "text" and input_file.suffix.lower() != ".txt":
            raise ValueError("审核文字快照类型无效")
        return GeneralReviewWorkspace(
            task_id=task.task_id,
            task_dir=task_dir,
            input_file=input_file,
            filename=filename,
            sender_userid=task.user_id,
            sender_name=str(task.payload.get("sender_name", "")).strip() or task.user_id,
            task_type=task.task_type,
            input_kind=input_kind,
        )

    def _create_workspace(
        self,
        *,
        filename: str,
        input_bytes: bytes,
        input_kind: Literal["docx", "text"],
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
        meta_path = task_dir / "meta.json"
        temporary = task_dir / f".meta.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(
                {
                    "task_id": task.task_id,
                    "task_type": task.task_type,
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


def _safe_input_name(filename: str, *, input_kind: Literal["docx", "text"]) -> str:
    fallback = "文字消息.txt" if input_kind == "text" else "uploaded.docx"
    path = Path(filename or fallback)
    stem = path.stem or "uploaded"
    safe_stem = re.sub(r"[^\w一-鿿\-_]", "_", stem)
    suffix = ".txt" if input_kind == "text" else ".docx"
    return f"{safe_stem}{suffix}"


__all__ = [
    "GENERAL_TEXT_REVIEW_TASK_TYPE",
    "GENERAL_REVIEW_COST_CLASS",
    "GENERAL_REVIEW_TASK_TYPE",
    "HALF_MONTHLY_REVIEW_TASK_TYPE",
    "NEICAN_REVIEW_TASK_TYPE",
    "OFFICIAL_FORMAT_REVIEW_TASK_TYPE",
    "REVIEW_FILE_TASK_TYPES",
    "REVIEW_TASK_TYPES",
    "GeneralReviewTaskService",
    "GeneralReviewWorkspace",
    "PreparedReviewDelivery",
    "ReviewTaskSubmission",
]
