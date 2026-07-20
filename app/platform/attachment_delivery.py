from __future__ import annotations

from dataclasses import asdict, dataclass
import asyncio
import errno
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any
import uuid

from app.platform.ops.events import OpsEventLogger
from app.platform.delivery_state import (
    CONFIRMED_DELIVERED,
    CONFIRMED_NOT_DELIVERED,
    DELIVERY_UNKNOWN,
    DeliveryOutcome,
    capture_wecom_delivery,
)
from app.platform.task_status import update_task_status


class _FileRejected(Exception):
    def __init__(self, *, error_code: str, user_message: str, size_bytes: int = 0):
        super().__init__(error_code)
        self.error_code = error_code
        self.user_message = user_message
        self.size_bytes = size_bytes


class _AttemptFailure(Exception):
    def __init__(self, *, error_code: str, cause: BaseException):
        super().__init__(error_code)
        self.error_code = error_code
        self.cause = cause


@dataclass(frozen=True)
class AttachmentDeliveryConfig:
    max_file_bytes: int = 100 * 512 * 1024
    chunk_bytes: int = 512 * 1024
    large_file_threshold_bytes: int = 5 * 1024 * 1024
    small_upload_timeout_seconds: float = 30.0
    large_upload_timeout_seconds: float = 120.0
    reply_timeout_seconds: float = 30.0
    max_attempts: int = 3
    retry_backoff_seconds: float = 2.0

    def __post_init__(self) -> None:
        positive_fields = (
            "max_file_bytes",
            "chunk_bytes",
            "large_file_threshold_bytes",
            "small_upload_timeout_seconds",
            "large_upload_timeout_seconds",
            "reply_timeout_seconds",
            "max_attempts",
        )
        for field_name in positive_fields:
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} 必须大于 0")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds 不能小于 0")


@dataclass(frozen=True)
class DeliveryRequest:
    file_path: Path
    allowed_root: Path
    frame: Any
    chat_id: str = ""
    task_dir: Path | None = None
    source: str = "attachment_delivery"
    sender_userid: str = ""
    sender_name: str = ""
    skill_id: str = ""
    job_id: str = ""
    manage_task_status: bool = True


@dataclass(frozen=True)
class DeliveryResult:
    """附件交付结果；metrics_path 是内部落盘位置，禁止直接外发给用户。"""

    delivered: bool
    status: str
    attempts: int
    size_bytes: int
    estimated_chunks: int
    upload_elapsed_seconds: float
    error_code: str
    user_message: str
    metrics_path: Path | None
    evidence: str = ""
    safe_error_code: str = ""
    occurred_at: str = ""
    attempt_id: str = ""
    correlation_id: str = ""
    compressed: bool = False
    images_compressed: bool = False

    def to_outcome(self) -> DeliveryOutcome:
        values: dict[str, str] = {
            "status": self.status,
            "evidence": self.evidence or "attachment_delivery_result",
            "safe_error_code": self.safe_error_code or self.error_code,
            "correlation_id": self.correlation_id,
        }
        if self.occurred_at:
            values["occurred_at"] = self.occurred_at
        if self.attempt_id:
            values["attempt_id"] = self.attempt_id
        return DeliveryOutcome(**values)


class AttachmentDelivery:
    def __init__(
        self,
        *,
        config: AttachmentDeliveryConfig | None = None,
        ops_event_logger: OpsEventLogger | None = None,
    ):
        self._config = config or AttachmentDeliveryConfig()
        # 企业微信 Bot 入口必须注入 logger；None 只用于无运维通道的内部调用或测试。
        self._ops_event_logger = ops_event_logger
        self._upload_lock = asyncio.Lock()

    async def deliver(self, *, ws_client: Any, request: DeliveryRequest) -> DeliveryResult:
        file_path = Path(request.file_path)
        task_dir = Path(request.task_dir) if request.task_dir is not None else None
        allowed_root = Path(request.allowed_root)
        start_time = time.monotonic()

        validation = self._validate_request(file_path=file_path, allowed_root=allowed_root)
        if validation["ok"] is not True:
            error_code = str(validation["error_code"])
            user_message = str(validation["user_message"])
            if error_code == "file_too_large":
                alert_recorded = self._record_ops_failure(
                    request=request,
                    filename=file_path.name,
                    size_bytes=int(validation["size_bytes"]),
                    estimated_chunks=self._estimate_chunks(int(validation["size_bytes"])),
                    attempts=0,
                    error_code=error_code,
                    error=None,
                )
                user_message = self._manual_retrieval_message(
                    prefix="文件超过企业微信可发送上限，结果已保留。",
                    alert_recorded=alert_recorded,
                    job_id=request.job_id,
                )
            return self._finalize_result(
                result=self._rejected_result(
                    error_code=error_code,
                    user_message=user_message,
                    size_bytes=int(validation["size_bytes"]),
                    start_time=start_time,
                ),
                request=request,
                task_dir=task_dir,
                filename=file_path.name,
            )

        attempts = 0
        last_exc: BaseException | None = None
        last_error_code = ""
        last_outcome: DeliveryOutcome | None = None
        size_bytes = int(validation["size_bytes"])
        estimated_chunks = self._estimate_chunks(size_bytes)
        resolved_file = Path(str(validation["resolved_file"]))

        async with self._upload_lock:
            try:
                resolved_file, file_bytes, size_bytes = self._read_file_safely(
                    file_path=file_path,
                    allowed_root=allowed_root,
                )
            except _FileRejected as exc:
                return self._finalize_result(
                    result=self._rejected_result(
                        error_code=exc.error_code,
                        user_message=exc.user_message,
                        size_bytes=exc.size_bytes,
                        start_time=start_time,
                    ),
                    request=request,
                    task_dir=task_dir,
                    filename=file_path.name,
                )

            estimated_chunks = self._estimate_chunks(size_bytes)
            for attempt in range(1, self._config.max_attempts + 1):
                attempts = attempt
                try:
                    outcome = await self._upload_and_reply(
                        ws_client=ws_client,
                        frame=request.frame,
                        chat_id=request.chat_id,
                        file_bytes=file_bytes,
                        filename=resolved_file.name,
                        upload_timeout=self._upload_timeout_for(size_bytes),
                    )
                    if outcome.status != CONFIRMED_DELIVERED:
                        last_outcome = outcome
                        last_error_code = (
                            "reply_timeout"
                            if outcome.safe_error_code == "delivery_ack_timeout"
                            else outcome.safe_error_code or "reply_failed"
                        )
                        break
                    result = DeliveryResult(
                        delivered=True,
                        status=outcome.status,
                        attempts=attempts,
                        size_bytes=size_bytes,
                        estimated_chunks=estimated_chunks,
                        upload_elapsed_seconds=round(time.monotonic() - start_time, 3),
                        error_code="",
                        user_message="",
                        metrics_path=None,
                        evidence=outcome.evidence,
                        safe_error_code=outcome.safe_error_code,
                        occurred_at=outcome.occurred_at,
                        attempt_id=outcome.attempt_id,
                        correlation_id=outcome.correlation_id,
                    )
                    return self._finalize_result(
                        result=result,
                        request=request,
                        task_dir=task_dir,
                        filename=resolved_file.name,
                    )
                except _AttemptFailure as exc:
                    last_exc = exc.cause
                    last_error_code = exc.error_code
                    if attempt < self._config.max_attempts:
                        await asyncio.sleep(self._config.retry_backoff_seconds * attempt)

        alert_recorded = self._record_ops_failure(
            request=request,
            filename=resolved_file.name,
            size_bytes=size_bytes,
            estimated_chunks=estimated_chunks,
            attempts=attempts,
            error_code=last_error_code or "upload_failed",
            error=last_exc,
            delivery_status=(
                last_outcome.status
                if last_outcome is not None
                else CONFIRMED_NOT_DELIVERED
            ),
        )
        is_unknown = last_outcome is not None and last_outcome.status == DELIVERY_UNKNOWN
        user_message = self._manual_retrieval_message(
            prefix=(
                "文件发送状态暂时无法确认，"
                if is_unknown
                else "文件上传失败，"
            ),
            alert_recorded=alert_recorded,
            job_id=request.job_id,
        )
        result = DeliveryResult(
            delivered=False,
            status=(
                last_outcome.status if last_outcome is not None else CONFIRMED_NOT_DELIVERED
            ),
            attempts=attempts,
            size_bytes=size_bytes,
            estimated_chunks=estimated_chunks,
            upload_elapsed_seconds=round(time.monotonic() - start_time, 3),
            error_code=last_error_code or "upload_failed",
            user_message=user_message,
            metrics_path=None,
            evidence=(
                last_outcome.evidence if last_outcome is not None else "upload_failed"
            ),
            safe_error_code=(
                last_outcome.safe_error_code
                if last_outcome is not None
                else last_error_code or "upload_failed"
            ),
            occurred_at=(last_outcome.occurred_at if last_outcome is not None else ""),
            attempt_id=(last_outcome.attempt_id if last_outcome is not None else ""),
            correlation_id=(
                last_outcome.correlation_id if last_outcome is not None else ""
            ),
        )
        finalized = self._finalize_result(
            result=result,
            request=request,
            task_dir=task_dir,
            filename=resolved_file.name,
        )
        return finalized

    async def _upload_and_reply(
        self,
        *,
        ws_client: Any,
        frame: Any,
        chat_id: str,
        file_bytes: bytes,
        filename: str,
        upload_timeout: float,
    ) -> DeliveryOutcome:
        try:
            upload_result = await asyncio.wait_for(
                ws_client.upload_media(file_bytes, type="file", filename=filename),
                timeout=upload_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise _AttemptFailure(error_code="upload_timeout", cause=exc) from exc
        except Exception as exc:
            raise _AttemptFailure(error_code="upload_failed", cause=exc) from exc

        media_id = str(getattr(upload_result, "get", lambda *_: "")("media_id", "") or "")
        if not media_id:
            exc = ValueError("upload response missing media_id")
            raise _AttemptFailure(error_code="missing_media_id", cause=exc) from exc

        async def send_message() -> object:
            if chat_id:
                sender = getattr(ws_client, "send_media_message", None)
                if not callable(sender):
                    raise RuntimeError("WebSocket not connected, unable to send data")
                return await sender(chat_id, "file", media_id)
            return await ws_client.reply_media(frame, "file", media_id)

        return await capture_wecom_delivery(
            send_message,
            timeout_seconds=self._config.reply_timeout_seconds,
        )

    def _rejected_result(
        self,
        *,
        error_code: str,
        user_message: str,
        size_bytes: int,
        start_time: float,
    ) -> DeliveryResult:
        return DeliveryResult(
            delivered=False,
            status=CONFIRMED_NOT_DELIVERED,
            attempts=0,
            size_bytes=size_bytes,
            estimated_chunks=self._estimate_chunks(size_bytes),
            upload_elapsed_seconds=round(time.monotonic() - start_time, 3),
            error_code=error_code,
            user_message=user_message,
            metrics_path=None,
            evidence="local_validation_rejected",
            safe_error_code=error_code,
        )

    def _validate_request(self, *, file_path: Path, allowed_root: Path) -> dict[str, object]:
        resolved_root = allowed_root.resolve(strict=False)
        size_bytes = 0

        if file_path.is_symlink():
            return {
                "ok": False,
                "error_code": "file_symlink",
                "user_message": "不允许上传符号链接文件。",
                "size_bytes": 0,
            }
        if not file_path.exists():
            return {
                "ok": False,
                "error_code": "file_missing",
                "user_message": "文件不存在，无法上传。",
                "size_bytes": 0,
            }
        try:
            resolved_file = file_path.resolve(strict=True)
        except OSError:
            return {
                "ok": False,
                "error_code": "file_unreadable",
                "user_message": "文件暂时不可读取，无法上传。",
                "size_bytes": 0,
            }
        if not resolved_file.is_relative_to(resolved_root):
            return {
                "ok": False,
                "error_code": "path_outside_allowed_root",
                "user_message": "文件不在允许的任务目录内，已拒绝上传。",
                "size_bytes": 0,
            }
        if not file_path.is_file():
            return {
                "ok": False,
                "error_code": "not_a_file",
                "user_message": "目标不是可上传文件。",
                "size_bytes": 0,
            }
        try:
            size_bytes = file_path.stat().st_size
        except OSError:
            return {
                "ok": False,
                "error_code": "file_unreadable",
                "user_message": "文件暂时不可读取，无法上传。",
                "size_bytes": 0,
            }
        if size_bytes <= 0:
            return {
                "ok": False,
                "error_code": "file_empty",
                "user_message": "文件为空，无法上传。",
                "size_bytes": size_bytes,
            }
        if size_bytes > self._config.max_file_bytes:
            return {
                "ok": False,
                "error_code": "file_too_large",
                "user_message": "文件过大，暂不支持上传。",
                "size_bytes": size_bytes,
            }
        return {
            "ok": True,
            "resolved_file": resolved_file,
            "size_bytes": size_bytes,
        }

    def _read_file_safely(
        self,
        *,
        file_path: Path,
        allowed_root: Path,
    ) -> tuple[Path, bytes, int]:
        resolved_root = allowed_root.resolve(strict=False)
        if file_path.is_symlink():
            raise _FileRejected(
                error_code="file_symlink",
                user_message="不允许上传符号链接文件。",
            )

        try:
            path_stat_before = file_path.lstat()
            resolved_before = file_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise _FileRejected(
                error_code="file_missing",
                user_message="文件不存在，无法上传。",
            ) from exc
        except OSError as exc:
            raise _FileRejected(
                error_code="file_unreadable",
                user_message="文件暂时不可读取，无法上传。",
            ) from exc

        if stat.S_ISLNK(path_stat_before.st_mode):
            raise _FileRejected(
                error_code="file_symlink",
                user_message="不允许上传符号链接文件。",
            )
        if not resolved_before.is_relative_to(resolved_root):
            raise _FileRejected(
                error_code="path_outside_allowed_root",
                user_message="文件不在允许的任务目录内，已拒绝上传。",
                size_bytes=path_stat_before.st_size,
            )
        if not stat.S_ISREG(path_stat_before.st_mode):
            raise _FileRejected(
                error_code="not_a_file",
                user_message="目标不是可上传文件。",
            )
        self._check_opened_size(path_stat_before.st_size)

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_descriptor = os.open(file_path, flags)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _FileRejected(
                    error_code="file_symlink",
                    user_message="不允许上传符号链接文件。",
                ) from exc
            raise _FileRejected(
                error_code="file_unreadable",
                user_message="文件暂时不可读取，无法上传。",
            ) from exc

        try:
            opened_stat = os.fstat(file_descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                raise _FileRejected(
                    error_code="not_a_file",
                    user_message="目标不是可上传文件。",
                )
            if not os.path.samestat(path_stat_before, opened_stat):
                raise _FileRejected(
                    error_code="file_changed",
                    user_message="文件在上传前发生变化，已拒绝上传。",
                    size_bytes=opened_stat.st_size,
                )
            self._check_opened_size(opened_stat.st_size)

            with os.fdopen(file_descriptor, "rb", closefd=False) as file_handle:
                file_bytes = file_handle.read(self._config.max_file_bytes + 1)
            opened_stat_after = os.fstat(file_descriptor)

            if len(file_bytes) > self._config.max_file_bytes:
                raise _FileRejected(
                    error_code="file_too_large",
                    user_message="文件过大，暂不支持上传。",
                    size_bytes=len(file_bytes),
                )
            if not file_bytes:
                raise _FileRejected(
                    error_code="file_empty",
                    user_message="文件为空，无法上传。",
                )
            if self._stat_changed(opened_stat, opened_stat_after) or opened_stat_after.st_size != len(file_bytes):
                raise _FileRejected(
                    error_code="file_changed",
                    user_message="文件读取期间发生变化，已拒绝上传。",
                    size_bytes=opened_stat_after.st_size,
                )

            try:
                path_stat_after = file_path.lstat()
                resolved_after = file_path.resolve(strict=True)
            except OSError as exc:
                raise _FileRejected(
                    error_code="file_changed",
                    user_message="文件读取期间发生变化，已拒绝上传。",
                    size_bytes=opened_stat_after.st_size,
                ) from exc

            if stat.S_ISLNK(path_stat_after.st_mode):
                raise _FileRejected(
                    error_code="file_symlink",
                    user_message="不允许上传符号链接文件。",
                    size_bytes=opened_stat_after.st_size,
                )
            if not resolved_after.is_relative_to(resolved_root):
                raise _FileRejected(
                    error_code="path_outside_allowed_root",
                    user_message="文件不在允许的任务目录内，已拒绝上传。",
                    size_bytes=opened_stat_after.st_size,
                )
            if not os.path.samestat(opened_stat_after, path_stat_after) or self._stat_changed(
                opened_stat_after,
                path_stat_after,
            ):
                raise _FileRejected(
                    error_code="file_changed",
                    user_message="文件读取期间发生变化，已拒绝上传。",
                    size_bytes=opened_stat_after.st_size,
                )
            self._check_opened_size(path_stat_after.st_size)
            return resolved_after, file_bytes, len(file_bytes)
        finally:
            os.close(file_descriptor)

    def _check_opened_size(self, size_bytes: int) -> None:
        if size_bytes <= 0:
            raise _FileRejected(
                error_code="file_empty",
                user_message="文件为空，无法上传。",
                size_bytes=size_bytes,
            )
        if size_bytes > self._config.max_file_bytes:
            raise _FileRejected(
                error_code="file_too_large",
                user_message="文件过大，暂不支持上传。",
                size_bytes=size_bytes,
            )

    @staticmethod
    def _stat_changed(before: os.stat_result, after: os.stat_result) -> bool:
        return (
            not os.path.samestat(before, after)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        )

    def _estimate_chunks(self, size_bytes: int) -> int:
        if size_bytes <= 0:
            return 0
        return max(1, math.ceil(size_bytes / self._config.chunk_bytes))

    def _upload_timeout_for(self, size_bytes: int) -> float:
        if size_bytes >= self._config.large_file_threshold_bytes:
            return self._config.large_upload_timeout_seconds
        return self._config.small_upload_timeout_seconds

    def _finalize_result(
        self,
        *,
        result: DeliveryResult,
        request: DeliveryRequest,
        task_dir: Path | None,
        filename: str,
    ) -> DeliveryResult:
        metrics_path: Path | None = None
        if task_dir is not None:
            task_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = self._write_metrics(
                task_dir=task_dir,
                request=request,
                result=result,
                filename=filename,
            )
            if request.manage_task_status:
                self._update_task_status(
                    task_dir=task_dir,
                    delivery_status=(
                        "delivered"
                        if result.delivered
                        else "unknown"
                        if result.status == DELIVERY_UNKNOWN
                        else "failed"
                    ),
                    source=request.source,
                )
        return DeliveryResult(
            delivered=result.delivered,
            status=result.status,
            attempts=result.attempts,
            size_bytes=result.size_bytes,
            estimated_chunks=result.estimated_chunks,
            upload_elapsed_seconds=result.upload_elapsed_seconds,
            error_code=result.error_code,
            user_message=result.user_message,
            metrics_path=metrics_path,
            evidence=result.evidence,
            safe_error_code=result.safe_error_code,
            occurred_at=result.occurred_at,
            attempt_id=result.attempt_id,
            correlation_id=result.correlation_id,
            compressed=result.compressed,
            images_compressed=result.images_compressed,
        )

    def _write_metrics(
        self,
        *,
        task_dir: Path,
        request: DeliveryRequest,
        result: DeliveryResult,
        filename: str,
    ) -> Path:
        result_payload = asdict(result)
        result_payload.pop("metrics_path", None)
        payload = {
            "schema_version": 1,
            "source": request.source,
            "sender_userid": request.sender_userid,
            "sender_name": request.sender_name,
            "skill_id": request.skill_id,
            "job_id": request.job_id,
            "filename": filename,
            **result_payload,
        }
        path = task_dir / "delivery.json"
        temporary = task_dir / f".delivery.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def _update_task_status(self, *, task_dir: Path, delivery_status: str, source: str) -> None:
        try:
            update_task_status(
                task_dir,
                delivery_status=delivery_status,
                source=source,
            )
        except (OSError, ValueError):
            return

    def _record_ops_failure(
        self,
        *,
        request: DeliveryRequest,
        filename: str,
        size_bytes: int,
        estimated_chunks: int,
        attempts: int,
        error_code: str,
        error: BaseException | None,
        delivery_status: str = CONFIRMED_NOT_DELIVERED,
    ) -> bool:
        if self._ops_event_logger is None:
            return False
        safe_error_code, safe_summary = self._safe_error_summary(error_code)
        error_type = self._safe_error_type(error)
        safe_filename = self._safe_filename(filename)
        detail = (
            f"文件名: {safe_filename}\n"
            f"大小: {size_bytes} 字节\n"
            f"估算分片: {estimated_chunks}\n"
            f"尝试次数: {attempts}\n"
            f"交付状态: {delivery_status}\n"
            f"异常类型: {error_type}\n"
            f"安全错误码: {safe_error_code}\n"
            f"异常摘要: {safe_summary}"
        )
        try:
            is_unknown = delivery_status == DELIVERY_UNKNOWN
            self._ops_event_logger.record(
                source=request.source,
                severity="warning" if is_unknown else "error",
                subject="附件交付状态未知" if is_unknown else "附件交付失败",
                detail=detail,
                sender_userid=request.sender_userid,
                sender_name=request.sender_name,
                skill_id=request.skill_id,
                job_id=request.job_id,
            )
        except Exception:
            return False
        return True

    @staticmethod
    def _safe_error_summary(error_code: str) -> tuple[str, str]:
        summaries = {
            "upload_timeout": "上传请求超时",
            "upload_failed": "上传请求失败",
            "missing_media_id": "上传响应缺少媒体标识",
            "reply_timeout": "媒体回复请求超时",
            "reply_failed": "媒体回复请求失败",
            "file_too_large": "文件超过企业微信附件发送上限",
        }
        if error_code not in summaries:
            return "delivery_failed", "附件交付失败"
        return error_code, summaries[error_code]

    @staticmethod
    def _manual_retrieval_message(
        *,
        prefix: str,
        alert_recorded: bool,
        job_id: str,
    ) -> str:
        if prefix.endswith("，"):
            message = prefix + ("已提醒管理员处理。" if alert_recorded else "请联系管理员处理。")
        else:
            message = prefix + ("已提醒管理员处理。" if alert_recorded else "请联系管理员处理。")
        safe_job_id = job_id.strip()
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", safe_job_id):
            message += f"处理编号：{safe_job_id}。"
        return message

    @staticmethod
    def _safe_error_type(error: BaseException | None) -> str:
        if error is None:
            return "UnknownError"
        name = type(error).__name__
        if len(name) > 64 or not name.isascii() or not name.isidentifier():
            return "UnknownError"
        return name

    @staticmethod
    def _safe_filename(filename: str) -> str:
        basename = Path(filename).name
        cleaned = "".join(character for character in basename if character.isprintable())
        return cleaned[:128] or "unknown-file"
