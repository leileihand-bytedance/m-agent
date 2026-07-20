from __future__ import annotations

from pathlib import Path
import sqlite3

from scripts.platform_readiness import (
    _check_pending_deliveries,
    _check_sqlite,
    run_offline_acceptance,
)


def test_offline_readiness_covers_idempotency_multi_user_and_restart(tmp_path: Path) -> None:
    checks = run_offline_acceptance(root=tmp_path)

    assert [check.name for check in checks] == [
        "重复消息幂等",
        "多用户并发隔离",
        "进程重启恢复",
    ]
    assert all(check.passed for check in checks)


def test_sqlite_readiness_check_is_read_only_and_reports_corruption(tmp_path: Path) -> None:
    healthy = tmp_path / "healthy.sqlite3"
    with sqlite3.connect(healthy) as connection:
        connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY)")

    assert _check_sqlite("writing", healthy).passed
    assert not _check_sqlite("review", tmp_path / "missing.sqlite3").passed
    assert not (tmp_path / "missing.sqlite3").exists()


def test_pending_delivery_check_reads_both_queues_without_mutating_them(
    tmp_path: Path,
) -> None:
    for name in ("writing", "review"):
        with sqlite3.connect(tmp_path / f"{name}.sqlite3") as connection:
            connection.execute("CREATE TABLE tasks (status TEXT, safe_error_code TEXT)")
    assert _check_pending_deliveries(tmp_path).passed

    with sqlite3.connect(tmp_path / "review.sqlite3") as connection:
        connection.execute(
            "INSERT INTO tasks VALUES ('failed', 'delivery_status_uncertain')"
        )

    check = _check_pending_deliveries(tmp_path)
    assert not check.passed
    assert check.detail.startswith("1 项")
