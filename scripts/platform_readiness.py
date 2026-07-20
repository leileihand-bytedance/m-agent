from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.platform.config import DEFAULT_ENV_PATH, parse_env_file
from app.platform.data_paths import DataPaths
from app.platform.ops.heartbeat import find_stale_heartbeats
from app.platform.task_execution import (
    ClaimLimits,
    TaskRepository,
    build_idempotency_key,
)
from scripts.bot_services import default_manager, selected_services


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str


def run_offline_acceptance(*, root: Path | None = None) -> tuple[ReadinessCheck, ...]:
    """Exercise idempotency, multi-user scheduling and restart recovery together."""

    if root is None:
        with tempfile.TemporaryDirectory(prefix="m-agent-readiness-") as temporary:
            return run_offline_acceptance(root=Path(temporary))

    current_time = [datetime(2026, 7, 20, 9, 0, tzinfo=UTC)]
    db_path = root / "task-execution.sqlite3"
    repository = TaskRepository(db_path, now_factory=lambda: current_time[0])
    duplicate_key = build_idempotency_key("wecom", "acceptance-user-a", "same-message")

    def submit_duplicate(_index: int) -> str:
        return repository.submit(
            idempotency_key=duplicate_key,
            channel="wecom",
            user_id="acceptance-user-a",
            task_type="acceptance",
            cost_class="model",
            payload={"case": "duplicate"},
            max_attempts=3,
            resumable=True,
        ).task_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        duplicate_ids = tuple(pool.map(submit_duplicate, range(8)))

    checks = [
        ReadinessCheck(
            "重复消息幂等",
            len(set(duplicate_ids)) == 1 and repository.count_tasks() == 1,
            "8 次并发提交应只创建 1 项任务",
        )
    ]

    for user_index in range(3):
        for task_index in range(2):
            repository.submit(
                idempotency_key=f"multi-{user_index}-{task_index}",
                channel="wecom",
                user_id=f"acceptance-user-{user_index}",
                task_type="acceptance",
                cost_class="model",
                payload={"case": "multi-user"},
                max_attempts=3,
                resumable=True,
            )

    limits = ClaimLimits(global_limit=2, per_user_limit=1)
    first = repository.claim_next(
        limits,
        worker_id="acceptance-worker-1",
        lease_duration=timedelta(seconds=30),
    )
    second = repository.claim_next(
        limits,
        worker_id="acceptance-worker-2",
        lease_duration=timedelta(seconds=30),
    )
    third = repository.claim_next(
        limits,
        worker_id="acceptance-worker-3",
        lease_duration=timedelta(seconds=30),
    )
    distinct_users = first is not None and second is not None and first.user_id != second.user_id
    checks.append(
        ReadinessCheck(
            "多用户并发隔离",
            distinct_users and third is None,
            "全局并发 2、单用户并发 1 时，应同时调度两个不同用户",
        )
    )

    if first is not None and first.lease_token:
        repository.complete(
            first.task_id,
            worker_id="acceptance-worker-1",
            lease_token=first.lease_token,
        )
    current_time[0] += timedelta(seconds=31)
    restarted_repository = TaskRepository(db_path, now_factory=lambda: current_time[0])
    summary = restarted_repository.recover_interrupted()
    recovered_status = (
        restarted_repository.get_task(second.task_id).status
        if second is not None
        else "missing"
    )
    checks.append(
        ReadinessCheck(
            "进程重启恢复",
            summary.requeued == 1 and recovered_status == "queued",
            "租约过期的处理中任务应由新进程重新排队，已完成任务不回退",
        )
    )
    return tuple(checks)


def run_production_readiness(*, project_root: Path = ROOT) -> tuple[ReadinessCheck, ...]:
    """Read production health without reading user content or mutating queue data."""

    checks: list[ReadinessCheck] = list(run_offline_acceptance())
    manager = default_manager()
    statuses = manager.status(selected_services("all"))
    unhealthy = [item.key for item in statuses if not item.loaded or item.state != "running"]
    checks.append(
        ReadinessCheck(
            "后台服务",
            not unhealthy,
            "全部运行" if not unhealthy else "未正常运行：" + "、".join(unhealthy),
        )
    )

    values = parse_env_file(DEFAULT_ENV_PATH)
    values.update(os.environ)
    paths = DataPaths.from_values(values, project_root=project_root)
    stale = find_stale_heartbeats(
        paths.heartbeats,
        monitored_services=("writing_bot", "review_bot", "rewrite_bot", "ops_bot"),
        max_age_seconds=int(values.get("M_AGENT_OPS_HEARTBEAT_MAX_AGE_SECONDS", "180") or "180"),
    )
    checks.append(
        ReadinessCheck(
            "服务心跳",
            not stale,
            "全部新鲜" if not stale else "异常：" + "、".join(item.service for item in stale),
        )
    )

    queue_dir = paths.task_queue_db.parent
    for name in ("writing", "review"):
        db_path = queue_dir / f"{name}.sqlite3"
        checks.append(_check_sqlite(name, db_path))

    checks.append(_check_pending_deliveries(queue_dir))
    return tuple(checks)


def _check_sqlite(name: str, path: Path) -> ReadinessCheck:
    if not path.is_file():
        return ReadinessCheck(f"{name} 队列", False, "数据库不存在")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            result = str(connection.execute("PRAGMA quick_check").fetchone()[0])
    except sqlite3.Error:
        return ReadinessCheck(f"{name} 队列", False, "只读完整性检查失败")
    return ReadinessCheck(f"{name} 队列", result == "ok", result)


def _check_pending_deliveries(queue_dir: Path) -> ReadinessCheck:
    total = 0
    for name in ("writing", "review"):
        path = queue_dir / f"{name}.sqlite3"
        if not path.is_file():
            return ReadinessCheck("待人工交付", False, f"{name} 队列数据库不存在")
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                row = connection.execute(
                    """
                    SELECT COUNT(*) FROM tasks
                    WHERE status = 'failed'
                      AND safe_error_code IN (
                        'delivery_failed',
                        'delivery_not_delivered',
                        'delivery_status_uncertain'
                      )
                    """
                ).fetchone()
        except sqlite3.Error:
            return ReadinessCheck("待人工交付", False, f"{name} 队列只读查询失败")
        total += int(row[0]) if row else 0
    return ReadinessCheck(
        "待人工交付",
        total == 0,
        "无" if total == 0 else f"{total} 项，请在管理台处理",
    )


def _print_checks(checks: tuple[ReadinessCheck, ...]) -> int:
    for check in checks:
        marker = "通过" if check.passed else "未通过"
        print(f"[{marker}] {check.name}：{check.detail}")
    passed = sum(1 for check in checks if check.passed)
    print(f"结果：{passed}/{len(checks)} 项通过")
    return 0 if passed == len(checks) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M-Agent 开放前生产体检")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("offline", "production"),
        default="offline",
        help="offline 只做隔离演练；production 额外只读检查真实服务和队列",
    )
    args = parser.parse_args(argv)
    checks = (
        run_production_readiness()
        if args.mode == "production"
        else run_offline_acceptance()
    )
    return _print_checks(checks)


if __name__ == "__main__":
    raise SystemExit(main())
