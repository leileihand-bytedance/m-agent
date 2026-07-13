from datetime import date, datetime
import json

from app.platform.ops.events import OpsEventLogger
from app.platform.ops.report import build_daily_report, previous_workday


def _append_chat_log(root, day: date, payload: dict):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day.strftime('%Y%m%d')}.jsonl"
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def test_previous_workday_skips_weekend():
    assert previous_workday(date(2026, 7, 6)) == date(2026, 7, 3)
    assert previous_workday(date(2026, 7, 7)) == date(2026, 7, 6)


def test_build_daily_report_summarizes_chat_logs_and_ops_events(tmp_path):
    chat_dir = tmp_path / "chat_logs"
    events_dir = tmp_path / "ops_events"
    target_day = date(2026, 7, 8)
    _append_chat_log(
        chat_dir,
        target_day,
        {
            "created_at": "2026-07-08 10:00:00",
            "sender_name": "test-user",
            "route_skill_id": "writer1",
            "result_skill_id": "writer1",
            "needs_clarification": False,
            "error": None,
        },
    )
    _append_chat_log(
        chat_dir,
        target_day,
        {
            "created_at": "2026-07-08 11:00:00",
            "sender_name": "user-002",
            "route_skill_id": "writer2",
            "result_skill_id": "writer2",
            "needs_clarification": True,
            "error": None,
        },
    )
    _append_chat_log(
        chat_dir,
        target_day,
        {
            "created_at": "2026-07-08 12:00:00",
            "sender_name": "user-003",
            "route_skill_id": "direct_report",
            "result_skill_id": "direct_report",
            "needs_clarification": False,
            "error": "RuntimeError: model timeout",
        },
    )
    OpsEventLogger(events_dir).record(
        source="writing_bot",
        severity="error",
        subject="写作处理失败",
        detail="RuntimeError: model timeout",
        sender_name="user-003",
        skill_id="direct_report",
        created_at=datetime(2026, 7, 8, 12, 1, 0),
    )

    report = build_daily_report(
        target_day=target_day,
        chat_log_dir=chat_dir,
        ops_events_dir=events_dir,
    )

    assert "M-Agent 工作日报" in report
    assert "2026-07-08" in report
    assert "总请求数：3" in report
    assert "成功完成：1" in report
    assert "需用户补充：1" in report
    assert "失败：1" in report
    assert "writer1：1" in report
    assert "writer2：1" in report
    assert "direct_report：1" in report
    assert "写作处理失败：1" in report
