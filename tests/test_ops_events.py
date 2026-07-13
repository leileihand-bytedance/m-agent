from datetime import datetime
import json

from app.platform.ops.events import OpsEventLogger, read_ops_events


def test_ops_event_logger_records_jsonl_event(tmp_path):
    logger = OpsEventLogger(tmp_path)
    created_at = datetime(2026, 7, 9, 10, 30, 0)

    event = logger.record(
        source="writing_bot",
        severity="error",
        subject="写作处理失败",
        detail="RuntimeError: model timeout",
        sender_userid="user-001",
        sender_name="test-user",
        skill_id="writer1",
        job_id="job-001",
        created_at=created_at,
    )

    path = tmp_path / "20260709.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["event_id"] == event.event_id
    assert payload["source"] == "writing_bot"
    assert payload["severity"] == "error"
    assert payload["subject"] == "写作处理失败"
    assert payload["sender_name"] == "test-user"
    assert payload["skill_id"] == "writer1"
    assert payload["job_id"] == "job-001"


def test_read_ops_events_skips_broken_lines(tmp_path):
    logger = OpsEventLogger(tmp_path)
    logger.record(
        source="writing_bot",
        severity="warning",
        subject="链接读取失败",
        detail="https://example.com 读取失败",
        created_at=datetime(2026, 7, 9, 9, 0, 0),
    )
    with (tmp_path / "20260709.jsonl").open("a", encoding="utf-8") as file:
        file.write("{bad json}\n")

    events = read_ops_events(tmp_path, datetime(2026, 7, 9).date())

    assert len(events) == 1
    assert events[0].subject == "链接读取失败"
