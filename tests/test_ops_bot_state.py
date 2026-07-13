from datetime import date, datetime

from app.platform.ops.bot import OpsBotState, collect_pending_events, should_send_daily_report
from app.platform.ops.events import OpsEventLogger


def test_collect_pending_events_skips_already_notified_events(tmp_path):
    logger = OpsEventLogger(tmp_path / "events")
    first = logger.record(
        source="writing_bot",
        severity="error",
        subject="写作处理失败",
        detail="error 1",
        created_at=datetime(2026, 7, 9, 9, 0, 0),
    )
    second = logger.record(
        source="writing_bot",
        severity="warning",
        subject="链接读取失败",
        detail="warning 1",
        created_at=datetime(2026, 7, 9, 9, 1, 0),
    )
    state = OpsBotState(notified_event_ids={first.event_id}, last_daily_report_for="")

    pending = collect_pending_events(
        events_dir=tmp_path / "events",
        today=date(2026, 7, 9),
        state=state,
    )

    assert [event.event_id for event in pending] == [second.event_id]


def test_collect_pending_events_does_not_replay_previous_workday_events(tmp_path):
    logger = OpsEventLogger(tmp_path / "events")
    friday = logger.record(
        source="writing_bot",
        severity="error",
        subject="写作 Bot 连接断开",
        detail="old disconnect",
        created_at=datetime(2026, 7, 10, 15, 0, 0),
    )
    monday = logger.record(
        source="writing_bot",
        severity="error",
        subject="写作处理失败",
        detail="current failure",
        created_at=datetime(2026, 7, 13, 10, 0, 0),
    )

    pending = collect_pending_events(
        events_dir=tmp_path / "events",
        today=date(2026, 7, 13),
        state=OpsBotState(notified_event_ids=set(), last_daily_report_for=""),
    )

    assert friday.event_id not in [event.event_id for event in pending]
    assert [event.event_id for event in pending] == [monday.event_id]


def test_collect_pending_events_deduplicates_same_day_equivalent_events(tmp_path):
    logger = OpsEventLogger(tmp_path / "events")
    first = logger.record(
        source="writing_bot",
        severity="error",
        subject="写作 Bot 连接错误",
        detail="network unavailable",
        created_at=datetime(2026, 7, 13, 13, 20, 0),
    )
    duplicate = logger.record(
        source="writing_bot",
        severity="error",
        subject="写作 Bot 连接错误",
        detail="network unavailable",
        created_at=datetime(2026, 7, 13, 13, 20, 30),
    )
    state = OpsBotState(notified_event_ids=set(), last_daily_report_for="")

    pending = collect_pending_events(
        events_dir=tmp_path / "events",
        today=date(2026, 7, 13),
        state=state,
    )

    assert [event.event_id for event in pending] == [first.event_id]
    assert duplicate.event_id in state.notified_event_ids


def test_collect_pending_events_suppresses_new_duplicate_of_already_notified_event(tmp_path):
    logger = OpsEventLogger(tmp_path / "events")
    first = logger.record(
        source="review_bot",
        severity="error",
        subject="审核 Bot 连接断开",
        detail="no close frame received or sent",
        created_at=datetime(2026, 7, 13, 13, 16, 0),
    )
    duplicate = logger.record(
        source="review_bot",
        severity="error",
        subject="审核 Bot 连接断开",
        detail="no close frame received or sent",
        created_at=datetime(2026, 7, 13, 14, 16, 0),
    )
    state = OpsBotState(notified_event_ids={first.event_id}, last_daily_report_for="")

    pending = collect_pending_events(
        events_dir=tmp_path / "events",
        today=date(2026, 7, 13),
        state=state,
    )

    assert pending == []
    assert duplicate.event_id in state.notified_event_ids


def test_should_send_daily_report_only_after_configured_time():
    state = OpsBotState(notified_event_ids=set(), last_daily_report_for="")

    assert should_send_daily_report(
        now=datetime(2026, 7, 9, 8, 59, 0),
        hour=9,
        minute=0,
        state=state,
    ) is False
    assert should_send_daily_report(
        now=datetime(2026, 7, 9, 9, 0, 0),
        hour=9,
        minute=0,
        state=state,
    ) is True

    state.last_daily_report_for = "2026-07-09"
    assert should_send_daily_report(
        now=datetime(2026, 7, 9, 10, 0, 0),
        hour=9,
        minute=0,
        state=state,
    ) is False
