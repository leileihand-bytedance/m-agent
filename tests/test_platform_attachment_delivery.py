from __future__ import annotations

from datetime import date
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.platform.attachment_delivery import (
    AttachmentDelivery,
    AttachmentDeliveryConfig,
    DeliveryRequest,
)
from app.platform.ops.events import OpsEventLogger, read_ops_events
from app.platform.task_status import write_task_status


class FakeWsClient:
    def __init__(self):
        self.uploaded_media: list[tuple[bytes, str, str]] = []
        self.media_replies: list[tuple[object, str, str]] = []
        self.sent_media: list[tuple[str, str, str]] = []

    async def upload_media(self, content, *, type, filename):
        self.uploaded_media.append((content, type, filename))
        return {"media_id": f"media-{filename}"}

    async def reply_media(self, frame, media_type, media_id):
        self.media_replies.append((frame, media_type, media_id))

    async def send_media_message(self, chatid, media_type, media_id):
        self.sent_media.append((chatid, media_type, media_id))


class RetryReplyWsClient(FakeWsClient):
    def __init__(self):
        super().__init__()
        self.reply_attempts = 0

    async def reply_media(self, frame, media_type, media_id):
        self.reply_attempts += 1
        if self.reply_attempts == 1:
            raise RuntimeError("reply failed once")
        await super().reply_media(frame, media_type, media_id)


class FailingWsClient(FakeWsClient):
    error_message = "upstream exploded"

    async def upload_media(self, content, *, type, filename):
        self.uploaded_media.append((content, type, filename))
        raise RuntimeError(self.error_message)


class MissingMediaIdWsClient(FakeWsClient):
    async def upload_media(self, content, *, type, filename):
        self.uploaded_media.append((content, type, filename))
        return {}


class ReplyTimeoutWsClient(FakeWsClient):
    async def reply_media(self, frame, media_type, media_id):
        await asyncio.sleep(1)


class SlowUploadWsClient(FakeWsClient):
    async def upload_media(self, content, *, type, filename):
        await asyncio.sleep(0.05)
        return await super().upload_media(content, type=type, filename=filename)


class RaisingOpsLogger:
    def record(self, **kwargs: Any) -> None:
        raise RuntimeError("logger unavailable")


class SerialWsClient(FakeWsClient):
    def __init__(self):
        super().__init__()
        self.started_filenames: list[str] = []
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def upload_media(self, content, *, type, filename):
        self.started_filenames.append(filename)
        if len(self.started_filenames) == 1:
            self.first_started.set()
            await self.release_first.wait()
        return await super().upload_media(content, type=type, filename=filename)


class ValidationTrackingDelivery(AttachmentDelivery):
    def __init__(self, *, tracked_filename: str, **kwargs: Any):
        super().__init__(**kwargs)
        self._tracked_filename = tracked_filename
        self.tracked_file_validated = asyncio.Event()

    def _validate_request(self, *, file_path: Path, allowed_root: Path) -> dict[str, object]:
        result = super()._validate_request(file_path=file_path, allowed_root=allowed_root)
        if file_path.name == self._tracked_filename and result.get("ok") is True:
            self.tracked_file_validated.set()
        return result


def _write_file(path: Path, size: int, *, byte: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(byte * size)
    return path


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_file_bytes", 0),
        ("chunk_bytes", 0),
        ("large_file_threshold_bytes", 0),
        ("small_upload_timeout_seconds", 0),
        ("large_upload_timeout_seconds", 0),
        ("reply_timeout_seconds", 0),
        ("max_attempts", 0),
        ("retry_backoff_seconds", -1),
    ],
)
def test_attachment_delivery_config_rejects_invalid_limits(field, value):
    with pytest.raises(ValueError, match=field):
        AttachmentDeliveryConfig(**{field: value})


def _build_request(
    *,
    file_path: Path,
    allowed_root: Path,
    task_dir: Path | None = None,
    source: str = "writing_bot",
    chat_id: str = "",
) -> DeliveryRequest:
    return DeliveryRequest(
        file_path=file_path,
        allowed_root=allowed_root,
        frame={"msgid": "frame-001"},
        chat_id=chat_id,
        task_dir=task_dir,
        source=source,
        sender_userid="user-001",
        sender_name="test-user",
        skill_id="writer1",
        job_id="job-001",
    )


def test_attachment_delivery_default_matches_wecom_chunk_limit():
    config = AttachmentDeliveryConfig()

    assert config.max_file_bytes == 100 * 512 * 1024


@pytest.mark.anyio
async def test_attachment_delivery_succeeds_and_writes_metrics_and_status(tmp_path):
    allowed_root = tmp_path / "allowed"
    task_dir = tmp_path / "task"
    file_path = _write_file(allowed_root / "output" / "result.docx", 9, byte=b"a")
    write_task_status(task_dir, processing_status="completed", delivery_status="unknown")
    ws_client = FakeWsClient()
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=1,
            chunk_bytes=4,
            max_file_bytes=1024,
        )
    )

    result = await delivery.deliver(
        ws_client=ws_client,
        request=_build_request(file_path=file_path, allowed_root=allowed_root, task_dir=task_dir),
    )

    assert result.delivered is True
    assert result.status == "delivered"
    assert result.attempts == 1
    assert result.size_bytes == 9
    assert result.estimated_chunks == 3
    assert result.error_code == ""
    assert result.user_message == ""
    assert result.compressed is False
    assert result.images_compressed is False
    assert result.metrics_path == task_dir / "delivery.json"
    assert ws_client.uploaded_media == [(b"aaaaaaaaa", "file", "result.docx")]
    assert ws_client.media_replies == [({"msgid": "frame-001"}, "file", "media-result.docx")]

    metrics_path = Path(str(result.metrics_path))
    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["delivered"] is True
    assert payload["filename"] == "result.docx"
    assert payload["status"] == "delivered"
    assert payload["compressed"] is False
    assert payload["images_compressed"] is False
    assert "file_path" not in payload

    status_payload = json.loads((task_dir / "status.json").read_text(encoding="utf-8"))
    assert status_payload["processing_status"] == "completed"
    assert status_payload["delivery_status"] == "delivered"


@pytest.mark.anyio
async def test_attachment_delivery_can_actively_send_to_chat_without_callback_frame(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "result.docx", 5)
    ws_client = FakeWsClient()
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(max_attempts=1, max_file_bytes=1024)
    )

    result = await delivery.deliver(
        ws_client=ws_client,
        request=_build_request(
            file_path=file_path,
            allowed_root=allowed_root,
            chat_id="user-001",
        ),
    )

    assert result.delivered is True
    assert ws_client.media_replies == []
    assert ws_client.sent_media == [("user-001", "file", "media-result.docx")]


@pytest.mark.anyio
async def test_attachment_delivery_retries_full_upload_when_reply_fails(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "retry.docx", 5)
    ws_client = RetryReplyWsClient()
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=2,
            retry_backoff_seconds=0,
            max_file_bytes=1024,
        )
    )

    result = await delivery.deliver(
        ws_client=ws_client,
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is True
    assert result.attempts == 2
    assert len(ws_client.uploaded_media) == 2
    assert ws_client.reply_attempts == 2


@pytest.mark.anyio
async def test_attachment_delivery_serializes_uploads_per_instance(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_a = _write_file(allowed_root / "output" / "a.docx", 5, byte=b"a")
    file_b = _write_file(allowed_root / "output" / "b.docx", 5, byte=b"b")
    ws_client = SerialWsClient()
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=1,
            max_file_bytes=1024,
        )
    )

    task_a = asyncio.create_task(
        delivery.deliver(
            ws_client=ws_client,
            request=_build_request(file_path=file_a, allowed_root=allowed_root),
        )
    )
    task_b = asyncio.create_task(
        delivery.deliver(
            ws_client=ws_client,
            request=_build_request(file_path=file_b, allowed_root=allowed_root),
        )
    )

    await ws_client.first_started.wait()
    await asyncio.sleep(0.05)
    assert ws_client.started_filenames == ["a.docx"]

    ws_client.release_first.set()
    result_a, result_b = await asyncio.gather(task_a, task_b)

    assert result_a.delivered is True
    assert result_b.delivered is True
    assert ws_client.started_filenames == ["a.docx", "b.docx"]


@pytest.mark.anyio
async def test_attachment_delivery_rejects_path_outside_allowed_root(tmp_path):
    allowed_root = tmp_path / "allowed"
    outside_file = _write_file(tmp_path / "outside" / "result.docx", 5)
    ws_client = FakeWsClient()
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=1,
            max_file_bytes=1024,
        )
    )

    result = await delivery.deliver(
        ws_client=ws_client,
        request=_build_request(file_path=outside_file, allowed_root=allowed_root),
    )

    assert result.delivered is False
    assert result.status == "rejected"
    assert result.error_code == "path_outside_allowed_root"
    assert ws_client.uploaded_media == []
    assert "目录" in result.user_message


@pytest.mark.anyio
async def test_attachment_delivery_rejects_oversized_file(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "large.docx", 12)
    ws_client = FakeWsClient()
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=1,
            max_file_bytes=10,
        )
    )

    result = await delivery.deliver(
        ws_client=ws_client,
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is False
    assert result.status == "rejected"
    assert result.error_code == "file_too_large"
    assert ws_client.uploaded_media == []


@pytest.mark.anyio
async def test_oversized_delivery_records_ops_and_returns_manual_retrieval_id(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "large.docx", 12)
    ops_dir = tmp_path / "events"
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(max_attempts=1, max_file_bytes=10),
        ops_event_logger=OpsEventLogger(ops_dir),
    )

    result = await delivery.deliver(
        ws_client=FakeWsClient(),
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.error_code == "file_too_large"
    assert "处理编号：job-001" in result.user_message
    assert "任务编号" not in result.user_message
    assert "已提醒管理员" in result.user_message
    events = read_ops_events(ops_dir, date.today())
    assert len(events) == 1
    assert events[0].job_id == "job-001"


@pytest.mark.anyio
async def test_attachment_delivery_rejects_empty_file(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "empty.docx", 0)
    ws_client = FakeWsClient()
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(max_attempts=1, max_file_bytes=1024)
    )

    result = await delivery.deliver(
        ws_client=ws_client,
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is False
    assert result.error_code == "file_empty"
    assert ws_client.uploaded_media == []


@pytest.mark.anyio
async def test_attachment_delivery_treats_missing_media_id_as_failure(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "missing-id.docx", 5)
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(max_attempts=1, max_file_bytes=1024)
    )

    result = await delivery.deliver(
        ws_client=MissingMediaIdWsClient(),
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is False
    assert result.attempts == 1
    assert result.error_code == "missing_media_id"


@pytest.mark.anyio
async def test_attachment_delivery_classifies_reply_timeout(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "reply-timeout.docx", 5)
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=1,
            max_file_bytes=1024,
            small_upload_timeout_seconds=0.5,
            reply_timeout_seconds=0.01,
        )
    )

    result = await delivery.deliver(
        ws_client=ReplyTimeoutWsClient(),
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is False
    assert result.error_code == "reply_timeout"


@pytest.mark.anyio
async def test_attachment_delivery_uses_large_upload_timeout_for_large_file(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "large-timeout.docx", 12)
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=1,
            max_file_bytes=1024,
            large_file_threshold_bytes=10,
            small_upload_timeout_seconds=0.01,
            large_upload_timeout_seconds=0.2,
        )
    )

    result = await delivery.deliver(
        ws_client=SlowUploadWsClient(),
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is True


@pytest.mark.anyio
async def test_attachment_delivery_classifies_small_upload_timeout(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "small-timeout.docx", 5)
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=1,
            max_file_bytes=1024,
            large_file_threshold_bytes=10,
            small_upload_timeout_seconds=0.01,
            large_upload_timeout_seconds=0.2,
        )
    )

    result = await delivery.deliver(
        ws_client=SlowUploadWsClient(),
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is False
    assert result.error_code == "upload_timeout"


async def _replace_while_waiting_for_lock(
    *,
    tmp_path: Path,
    replacement: str,
) -> tuple[object, SerialWsClient]:
    allowed_root = tmp_path / "allowed"
    hold_file = _write_file(allowed_root / "output" / "hold.docx", 4)
    tracked_file = _write_file(allowed_root / "output" / "replace.docx", 4)
    ws_client = SerialWsClient()
    delivery = ValidationTrackingDelivery(
        tracked_filename=tracked_file.name,
        config=AttachmentDeliveryConfig(
            max_attempts=1,
            max_file_bytes=10,
            small_upload_timeout_seconds=1,
        ),
    )

    hold_task = asyncio.create_task(
        delivery.deliver(
            ws_client=ws_client,
            request=_build_request(file_path=hold_file, allowed_root=allowed_root),
        )
    )
    await ws_client.first_started.wait()
    tracked_task = asyncio.create_task(
        delivery.deliver(
            ws_client=ws_client,
            request=_build_request(file_path=tracked_file, allowed_root=allowed_root),
        )
    )
    await delivery.tracked_file_validated.wait()

    if replacement == "symlink":
        outside_file = _write_file(tmp_path / "outside" / "secret.docx", 6, byte=b"s")
        tracked_file.unlink()
        tracked_file.symlink_to(outside_file)
    else:
        tracked_file.write_bytes(b"z" * 100)

    ws_client.release_first.set()
    hold_result, tracked_result = await asyncio.gather(hold_task, tracked_task)
    assert hold_result.delivered is True
    return tracked_result, ws_client


@pytest.mark.anyio
async def test_attachment_delivery_rejects_file_replaced_by_symlink_inside_lock(tmp_path):
    result, ws_client = await _replace_while_waiting_for_lock(
        tmp_path=tmp_path,
        replacement="symlink",
    )

    assert result.delivered is False
    assert result.status == "rejected"
    assert result.error_code in {"file_symlink", "path_outside_allowed_root", "file_changed"}
    assert ws_client.started_filenames == ["hold.docx"]


@pytest.mark.anyio
async def test_attachment_delivery_rejects_file_replaced_by_oversized_file_inside_lock(tmp_path):
    result, ws_client = await _replace_while_waiting_for_lock(
        tmp_path=tmp_path,
        replacement="oversized",
    )

    assert result.delivered is False
    assert result.status == "rejected"
    assert result.error_code == "file_too_large"
    assert ws_client.started_filenames == ["hold.docx"]


@pytest.mark.anyio
async def test_attachment_delivery_uses_unique_metrics_temp_names(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    task_dir = tmp_path / "task"
    file_path = _write_file(allowed_root / "output" / "result.docx", 5)
    temporary_names: list[str] = []
    original_write_text = Path.write_text

    def tracking_write_text(path: Path, *args: Any, **kwargs: Any) -> int:
        if path.name.startswith(".delivery") and path.name.endswith(".tmp"):
            temporary_names.append(path.name)
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", tracking_write_text)
    request = _build_request(file_path=file_path, allowed_root=allowed_root, task_dir=task_dir)

    for _ in range(2):
        result = await AttachmentDelivery(
            config=AttachmentDeliveryConfig(max_attempts=1, max_file_bytes=1024)
        ).deliver(ws_client=FakeWsClient(), request=request)
        assert result.delivered is True

    assert len(temporary_names) == 2
    assert len(set(temporary_names)) == 2


@pytest.mark.anyio
async def test_attachment_delivery_records_safe_ops_event_and_failed_status(tmp_path):
    allowed_root = tmp_path / "allowed"
    task_dir = tmp_path / "task"
    file_path = _write_file(allowed_root / "output" / "failure.docx", 7)
    write_task_status(task_dir, processing_status="needs_input", delivery_status="unknown")
    ws_client = FailingWsClient()
    sensitive_local_path = "/" + "Users/op04/private/input.docx"
    ws_client.error_message = (
        f"upload failed {sensitive_local_path} "
        "req_id=req-123 token=top-secret secret=hidden api_key=private-key "
        "body=这是疑似用户正文"
    )
    ops_logger = OpsEventLogger(tmp_path / "ops_events")
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(
            max_attempts=2,
            retry_backoff_seconds=0,
            max_file_bytes=1024,
        ),
        ops_event_logger=ops_logger,
    )

    result = await delivery.deliver(
        ws_client=ws_client,
        request=_build_request(
            file_path=file_path,
            allowed_root=allowed_root,
            task_dir=task_dir,
            source="writing_bot",
        ),
    )

    assert result.delivered is False
    assert result.status == "failed"
    assert result.attempts == 2
    assert result.error_code == "upload_failed"
    assert "文件上传失败，已提醒管理员处理" in result.user_message
    assert "处理编号：job-001" in result.user_message
    assert "任务编号" not in result.user_message
    assert "req-123" not in result.user_message
    assert str(file_path) not in result.user_message

    events = read_ops_events(tmp_path / "ops_events", date.today())
    assert len(events) == 1
    assert events[0].source == "writing_bot"
    assert events[0].subject == "附件交付失败"
    assert events[0].sender_name == "test-user"
    assert events[0].skill_id == "writer1"
    assert events[0].job_id == "job-001"
    assert "failure.docx" in events[0].detail
    assert "RuntimeError" in events[0].detail
    assert "upload_failed" in events[0].detail
    assert "上传请求失败" in events[0].detail
    assert str(file_path) not in events[0].detail
    assert sensitive_local_path not in events[0].detail
    assert "req_id" not in events[0].detail
    assert "req-123" not in events[0].detail
    assert "token" not in events[0].detail.lower()
    assert "top-secret" not in events[0].detail
    assert "private-key" not in events[0].detail
    assert "用户正文" not in events[0].detail
    assert "delivery.json" not in events[0].detail

    status_payload = json.loads((task_dir / "status.json").read_text(encoding="utf-8"))
    assert status_payload["processing_status"] == "needs_input"
    assert status_payload["delivery_status"] == "failed"


@pytest.mark.anyio
async def test_attachment_delivery_does_not_claim_alert_without_logger(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "failure.docx", 5)
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(max_attempts=1, max_file_bytes=1024)
    )

    result = await delivery.deliver(
        ws_client=FailingWsClient(),
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is False
    assert "已提醒管理员" not in result.user_message


@pytest.mark.anyio
async def test_attachment_delivery_ignores_ops_logger_failure(tmp_path):
    allowed_root = tmp_path / "allowed"
    file_path = _write_file(allowed_root / "output" / "failure.docx", 5)
    delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(max_attempts=1, max_file_bytes=1024),
        ops_event_logger=RaisingOpsLogger(),
    )

    result = await delivery.deliver(
        ws_client=FailingWsClient(),
        request=_build_request(file_path=file_path, allowed_root=allowed_root),
    )

    assert result.delivered is False
    assert "已提醒管理员" not in result.user_message
