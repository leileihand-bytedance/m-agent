from datetime import datetime, timedelta

from app.platform.ops.heartbeat import find_stale_heartbeats, write_heartbeat


def test_find_stale_heartbeats_reports_missing_and_expired_services(tmp_path):
    now = datetime(2026, 7, 9, 10, 0, 0)
    write_heartbeat(tmp_path, "writing_bot", now=now - timedelta(seconds=20))
    write_heartbeat(tmp_path, "review_bot", now=now - timedelta(seconds=600))

    stale = find_stale_heartbeats(
        tmp_path,
        monitored_services=["writing_bot", "review_bot", "ops_bot"],
        now=now,
        max_age_seconds=120,
    )

    assert [item.service for item in stale] == ["review_bot", "ops_bot"]
    assert stale[0].reason == "stale"
    assert stale[1].reason == "missing"
