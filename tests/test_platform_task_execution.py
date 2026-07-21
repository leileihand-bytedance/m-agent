from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import json
import multiprocessing
import sqlite3
import stat
import string
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.platform.task_execution as task_execution_module  # noqa: E402
from app.platform.task_execution import (  # noqa: E402
    ClaimLimits,
    IdempotencyConflictError,
    InvalidSafeErrorCodeError,
    InvalidTaskTransitionError,
    PersistentTaskExecutor,
    SafeTaskError,
    TaskHandlerResult,
    TaskLifecycleObserver,
    TaskOwnershipError,
    TaskRepository,
    build_idempotency_key,
)
from app.platform.ops.events import OpsEventLogger, read_ops_events  # noqa: E402


DEFAULT_LIMITS = ClaimLimits(global_limit=2, per_user_limit=1)
LEASE = timedelta(seconds=30)


@dataclass
class MutableClock:
    current: datetime

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


def submit_task(
    store: TaskRepository,
    *,
    key: str,
    user_id: str = "user-001",
    task_type: str = "review",
    cost_class: str = "low",
    payload: dict[str, object] | None = None,
    max_attempts: int = 3,
    resumable: bool = True,
):
    return store.submit(
        idempotency_key=key,
        channel="wecom",
        user_id=user_id,
        task_type=task_type,
        cost_class=cost_class,
        payload=payload or {"seq": key},
        max_attempts=max_attempts,
        resumable=resumable,
    )


def claim(
    store: TaskRepository,
    worker_id: str,
    limits: ClaimLimits = DEFAULT_LIMITS,
):
    return store.claim_next(
        limits,
        worker_id=worker_id,
        lease_duration=LEASE,
    )


def _process_submit_same_key(db_path: str, start_event, result_queue, index: int) -> None:
    try:
        store = TaskRepository(db_path)
        if not start_event.wait(timeout=5):
            raise TimeoutError("start timeout")
        record = store.submit(
            idempotency_key="multiprocess-idempotency",
            channel="wecom",
            user_id="user-001",
            task_type="review",
            cost_class="low",
            payload={"index": index},
            max_attempts=3,
            resumable=True,
        )
        result_queue.put(("ok", record.task_id))
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__))


def _process_claim_with_limits(db_path: str, start_event, result_queue, index: int) -> None:
    try:
        store = TaskRepository(db_path)
        if not start_event.wait(timeout=5):
            raise TimeoutError("start timeout")
        record = store.claim_next(
            ClaimLimits(global_limit=2, per_user_limit=1),
            worker_id=f"process-worker-{index}",
            lease_duration=timedelta(seconds=30),
        )
        result_queue.put(
            (
                "ok",
                record.task_id if record is not None else None,
                record.lease_token if record is not None else None,
            )
        )
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__, None))


def _run_processes(processes: list[multiprocessing.Process], start_event) -> None:
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0


def _create_legacy_task_db_without_heartbeat(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                channel TEXT NOT NULL,
                user_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                cost_class TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                resumable INTEGER NOT NULL,
                worker_id TEXT,
                lease_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                safe_error_code TEXT
            );

            INSERT INTO tasks VALUES (
                'legacy-running',
                'legacy-key',
                'wecom',
                'legacy-user',
                'review',
                'low',
                '{"body": "legacy"}',
                'running',
                1,
                3,
                1,
                'legacy-worker',
                '2099-01-01T00:00:00+00:00',
                '2026-07-15T09:00:00+00:00',
                '2026-07-15T09:00:00+00:00',
                NULL
            );

            PRAGMA user_version = 1;
            """
        )


def _create_legacy_task_db_without_lease_expiry(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                channel TEXT NOT NULL,
                user_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                cost_class TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                max_attempts INTEGER NOT NULL,
                resumable INTEGER NOT NULL,
                worker_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                safe_error_code TEXT
            );

            INSERT INTO tasks VALUES (
                'legacy-no-expiry', 'legacy-no-expiry-key', 'wecom',
                'legacy-user', 'review', 'low', '{"body": "legacy"}',
                'running', 1, 3, 1, 'legacy-worker',
                '2026-07-15T09:00:00+00:00', '2026-07-15T09:00:00+00:00', NULL
            );

            PRAGMA user_version = 1;
            """
        )


def test_build_idempotency_key_is_stable_sha256_without_plaintext():
    key = build_idempotency_key("wecom", "user-001", "message-敏感-001")

    assert key == build_idempotency_key("wecom", "user-001", "message-敏感-001")
    assert len(key) == 64
    assert set(key) <= set(string.hexdigits.lower())
    assert "wecom" not in key
    assert "user-001" not in key
    assert "message" not in key


def test_submit_is_idempotent_and_persists_required_fields(tmp_path: Path):
    store = TaskRepository(
        tmp_path / "tasks.sqlite3",
        now_factory=lambda: datetime(2026, 7, 15, 9, 0, tzinfo=UTC),
    )
    key = build_idempotency_key("wecom", "user-001", "msg-001")

    first = store.submit(
        idempotency_key=key,
        channel="wecom",
        user_id="user-001",
        task_type="review",
        cost_class="high",
        payload={"doc_ids": ["a.docx"], "note": "请审核"},
        max_attempts=3,
        resumable=True,
    )
    duplicate = store.submit(
        idempotency_key=key,
        channel="wecom",
        user_id="user-001",
        task_type="review",
        cost_class="high",
        payload={"doc_ids": ["b.docx"]},
        max_attempts=9,
        resumable=False,
    )

    assert duplicate.task_id == first.task_id
    assert duplicate.status == "queued"
    assert duplicate.payload == {"doc_ids": ["a.docx"], "note": "请审核"}
    assert duplicate.attempts == 0
    assert duplicate.max_attempts == 3
    assert duplicate.resumable is True
    assert duplicate.worker_id is None
    assert duplicate.lease_token is None
    assert duplicate.lease_expires_at is None
    assert duplicate.heartbeat_at is None
    assert duplicate.state_version == 1
    assert duplicate.safe_error_code is None
    assert duplicate.created_at == "2026-07-15T09:00:00+00:00"
    assert duplicate.updated_at == "2026-07-15T09:00:00+00:00"
    assert store.count_tasks() == 1


def test_submit_duplicate_rejects_identity_conflict(tmp_path: Path):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    key = build_idempotency_key("wecom", "user-001", "msg-conflict")
    submit_task(store, key=key)

    with pytest.raises(IdempotencyConflictError, match="task_type"):
        submit_task(store, key=key, task_type="rewrite")

    assert store.count_tasks() == 1


def test_concurrent_submit_with_same_key_creates_one_task(tmp_path: Path):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    key = build_idempotency_key("wecom", "user-001", "msg-concurrent")
    workers = 8
    barrier = threading.Barrier(workers)

    def do_submit() -> str:
        barrier.wait()
        return submit_task(store, key=key).task_id

    with ThreadPoolExecutor(max_workers=workers) as pool:
        task_ids = list(pool.map(lambda _: do_submit(), range(workers)))

    assert len(set(task_ids)) == 1
    assert store.count_tasks() == 1


def test_submit_rejects_non_json_and_sensitive_payload_fields(tmp_path: Path):
    db_path = tmp_path / "tasks.sqlite3"
    store = TaskRepository(db_path)

    with pytest.raises(TypeError, match="JSON"):
        submit_task(store, key="bad-json", payload={"bad": {1, 2, 3}})

    with pytest.raises(ValueError, match="敏感字段"):
        submit_task(
            store,
            key="secret-payload",
            payload={"material": {"api_key": "sk-must-not-persist"}},
        )

    allowed = submit_task(
        store,
        key="allowed-payload",
        payload={
            "body": "用户提交的任务正文",
            "material_path": "/private/task-material/report.docx",
        },
    )

    assert allowed.payload["material_path"] == "/private/task-material/report.docx"
    with sqlite3.connect(db_path) as conn:
        dump = "\n".join(conn.iterdump())
    assert "sk-must-not-persist" not in dump


def test_claim_next_enforces_limits_and_atomically_sets_lease_owner(tmp_path: Path):
    store_a = TaskRepository(tmp_path / "tasks.sqlite3")
    store_b = TaskRepository(tmp_path / "tasks.sqlite3")
    first = submit_task(store_a, key="msg-101")
    blocked_same_user = submit_task(
        store_a,
        key="msg-102",
        cost_class="high",
    )
    other_user = submit_task(
        store_a,
        key="msg-103",
        user_id="user-002",
        cost_class="high",
    )
    limits = ClaimLimits(global_limit=2, per_user_limit=1, cost_class_limits={"high": 1})

    claim_one = claim(store_a, "worker-a", limits)
    claim_two = claim(store_b, "worker-b", limits)
    claim_three = claim(store_a, "worker-c", limits)

    assert claim_one is not None and claim_one.task_id == first.task_id
    assert claim_one.status == "running"
    assert claim_one.attempts == 1
    assert claim_one.worker_id == "worker-a"
    assert claim_one.lease_token is not None
    assert claim_one.lease_expires_at is not None
    assert claim_one.heartbeat_at is not None
    assert claim_one.state_version == 2
    assert claim_two is not None and claim_two.task_id == other_user.task_id
    assert claim_two.worker_id == "worker-b"
    assert claim_three is None
    assert store_a.get_task(blocked_same_user.task_id).status == "queued"


def test_claim_next_uses_bounded_sql_selection(tmp_path: Path, monkeypatch):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    submit_task(store, key="bounded-query")
    statements: list[str] = []
    original_connect = store._connect

    def traced_connect():
        conn = original_connect()
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(store, "_connect", traced_connect)
    assert claim(store, "worker-a") is not None

    queued_selects = [
        " ".join(statement.upper().split())
        for statement in statements
        if "STATUS = 'QUEUED'" in statement.upper()
        and statement.lstrip().upper().startswith("SELECT")
    ]
    assert queued_selects
    assert all("LIMIT 1" in statement for statement in queued_selects)


def test_worker_lease_renewal_owner_checks_and_expiry_recovery(tmp_path: Path):
    clock = MutableClock(datetime(2026, 7, 15, 9, 0, tzinfo=UTC))
    store = TaskRepository(tmp_path / "tasks.sqlite3", now_factory=clock)
    task = submit_task(store, key="lease-task", max_attempts=3)

    running = claim(store, "worker-a")
    assert running is not None
    assert running.lease_expires_at == "2026-07-15T09:00:30+00:00"
    assert running.lease_token is not None

    with pytest.raises(TaskOwnershipError, match="worker-b"):
        store.complete(
            task.task_id,
            worker_id="worker-b",
            lease_token=running.lease_token,
        )
    with pytest.raises(TaskOwnershipError, match="worker-b"):
        store.renew_lease(
            task.task_id,
            worker_id="worker-b",
            lease_token=running.lease_token,
            lease_duration=LEASE,
        )

    clock.advance(seconds=20)
    renewed = store.renew_lease(
        task.task_id,
        worker_id="worker-a",
        lease_token=running.lease_token,
        lease_duration=LEASE,
    )
    assert renewed.heartbeat_at == "2026-07-15T09:00:20+00:00"
    assert renewed.lease_expires_at == "2026-07-15T09:00:50+00:00"

    clock.advance(seconds=11)
    assert store.recover_interrupted().requeued == 0
    assert store.get_task(task.task_id).status == "running"

    clock.advance(seconds=20)
    assert store.recover_interrupted().requeued == 1
    assert store.get_task(task.task_id).status == "queued"

    reclaimed = claim(store, "worker-b")
    assert reclaimed is not None and reclaimed.attempts == 2
    with pytest.raises(TaskOwnershipError, match="worker-a"):
        store.complete(
            task.task_id,
            worker_id="worker-a",
            lease_token=running.lease_token,
        )
    completed = store.complete(
        task.task_id,
        worker_id="worker-b",
        lease_token=reclaimed.lease_token,
    )
    assert completed.status == "completed"
    assert completed.worker_id is None


def test_retry_and_illegal_transitions_are_enforced(tmp_path: Path):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="msg-201", max_attempts=2)

    with pytest.raises(InvalidTaskTransitionError, match="queued"):
        store.complete(task.task_id, worker_id="worker-a", lease_token="not-running")

    running = claim(store, "worker-a", ClaimLimits(global_limit=1, per_user_limit=1))
    assert running is not None
    requeued = store.fail(
        task.task_id,
        worker_id="worker-a",
        lease_token=running.lease_token,
        safe_error_code="model_busy",
        retryable=True,
    )

    assert requeued.status == "queued"
    assert requeued.attempts == 1
    assert requeued.safe_error_code == "model_busy"
    assert requeued.worker_id is None

    second_running = claim(
        store,
        "worker-b",
        ClaimLimits(global_limit=1, per_user_limit=1),
    )
    assert second_running is not None
    exhausted = store.fail(
        task.task_id,
        worker_id="worker-b",
        lease_token=second_running.lease_token,
        safe_error_code="model_busy",
        retryable=True,
    )

    assert exhausted.status == "failed"
    assert exhausted.attempts == 2
    assert exhausted.safe_error_code == "model_busy"

    cancelled = submit_task(store, key="msg-202")
    assert store.cancel(cancelled.task_id).status == "cancelled"

    needs_input = submit_task(store, key="msg-203")
    needs_input_running = claim(
        store,
        "worker-c",
        ClaimLimits(global_limit=1, per_user_limit=1),
    )
    assert needs_input_running is not None
    waiting = store.mark_needs_input(
        needs_input.task_id,
        worker_id="worker-c",
        lease_token=needs_input_running.lease_token,
        safe_error_code="awaiting_user",
    )

    assert waiting.status == "needs_input"
    assert store.cancel(needs_input.task_id).status == "cancelled"

    running_again = submit_task(store, key="msg-204")
    claim(store, "worker-d", ClaimLimits(global_limit=1, per_user_limit=1))
    with pytest.raises(InvalidTaskTransitionError, match="running"):
        store.cancel(running_again.task_id)


def test_recover_interrupted_only_touches_expired_leases(tmp_path: Path):
    clock = MutableClock(datetime(2026, 7, 15, 9, 0, tzinfo=UTC))
    store = TaskRepository(tmp_path / "tasks.sqlite3", now_factory=clock)
    resumable = submit_task(store, key="msg-301", max_attempts=2)
    non_resumable = submit_task(
        store,
        key="msg-302",
        user_id="user-002",
        cost_class="high",
        max_attempts=2,
        resumable=False,
    )
    limits = ClaimLimits(global_limit=2, per_user_limit=1, cost_class_limits={"high": 1})
    claim(store, "worker-a", limits)
    claim(store, "worker-b", limits)

    active_summary = store.recover_interrupted()
    assert active_summary.requeued == 0
    assert active_summary.failed == 0

    clock.advance(seconds=31)
    expired_summary = store.recover_interrupted()

    assert expired_summary.requeued == 1
    assert expired_summary.failed == 1
    recovered = store.get_task(resumable.task_id)
    assert recovered.status == "queued"
    assert recovered.worker_id is None
    failed_task = store.get_task(non_resumable.task_id)
    assert failed_task.status == "failed"
    assert failed_task.safe_error_code == "interrupted_not_resumable"


def test_recovery_fails_expired_task_when_attempts_are_exhausted(tmp_path: Path):
    clock = MutableClock(datetime(2026, 7, 15, 9, 0, tzinfo=UTC))
    store = TaskRepository(tmp_path / "tasks.sqlite3", now_factory=clock)
    task = submit_task(store, key="msg-exhausted", max_attempts=1)
    assert claim(store, "worker-a") is not None

    clock.advance(seconds=31)
    summary = store.recover_interrupted()

    failed = store.get_task(task.task_id)
    assert summary.requeued == 0
    assert summary.failed == 1
    assert failed.status == "failed"
    assert failed.safe_error_code == "attempts_exhausted"
    assert claim(store, "worker-b") is None


def test_safe_error_codes_are_validated_and_sensitive_text_never_reaches_sqlite(
    tmp_path: Path,
):
    db_path = tmp_path / "tasks.sqlite3"
    store = TaskRepository(db_path)
    task = submit_task(store, key="safe-code")
    running = claim(store, "worker-a")
    assert running is not None

    with pytest.raises(InvalidSafeErrorCodeError):
        SafeTaskError("token_from_upstream")
    with pytest.raises(InvalidSafeErrorCodeError):
        store.mark_needs_input(
            task.task_id,
            worker_id="worker-a",
            lease_token=running.lease_token,
            safe_error_code="secret_path_req_id",
        )
    with pytest.raises(InvalidSafeErrorCodeError):
        store.fail(
            task.task_id,
            worker_id="worker-a",
            lease_token=running.lease_token,
            safe_error_code="UPPERCASE",
            retryable=False,
        )

    final = store.fail(
        task.task_id,
        worker_id="worker-a",
        lease_token=running.lease_token,
        safe_error_code="internal_error",
        retryable=False,
    )
    assert final.safe_error_code == "internal_error"

    with sqlite3.connect(db_path) as conn:
        dump = "\n".join(conn.iterdump())
    assert "token_from_upstream" not in dump
    assert "secret_path_req_id" not in dump
    assert "UPPERCASE" not in dump


def test_executor_returns_final_records_and_persists_only_safe_classifications(
    tmp_path: Path,
):
    db_path = tmp_path / "tasks.sqlite3"
    store = TaskRepository(db_path)
    completed = submit_task(store, key="msg-401", payload={"step": "complete"})
    waiting = submit_task(
        store,
        key="msg-402",
        user_id="user-002",
        payload={"step": "wait"},
    )
    unexpected = submit_task(
        store,
        key="msg-403",
        user_id="user-003",
        payload={"step": "boom"},
        max_attempts=1,
    )
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="executor-a",
        lease_duration=LEASE,
    )

    async def handler(task):
        if task.payload["step"] == "wait":
            return TaskHandlerResult.needs_input("awaiting_user")
        if task.payload["step"] == "boom":
            raise RuntimeError("secret-token traceback must not be persisted")
        return None

    executor.register_handler("review", handler)

    first = asyncio.run(executor.run_once())
    second = asyncio.run(executor.run_once())
    third = asyncio.run(executor.run_once())

    assert first is not None and first.task_id == completed.task_id
    assert first.status == "completed"
    assert second is not None and second.task_id == waiting.task_id
    assert second.status == "needs_input"
    assert second.safe_error_code == "awaiting_user"
    assert third is not None and third.task_id == unexpected.task_id
    assert third.status == "failed"
    assert third.safe_error_code == "internal_error"

    with sqlite3.connect(db_path) as conn:
        dump = "\n".join(conn.iterdump())
    assert "secret-token" not in dump
    assert "traceback" not in dump


def test_handler_error_awaits_heartbeat_cleanup_before_returning(
    tmp_path: Path,
    monkeypatch,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="heartbeat-cleanup", max_attempts=1)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="executor-a",
        lease_duration=LEASE,
    )

    async def scenario():
        cleanup_finished = asyncio.Event()

        async def heartbeat(_task_id, _worker_id, _lease_token):
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.05)
                cleanup_finished.set()

        async def handler(_task):
            raise SafeTaskError("upstream_busy", retryable=False)

        monkeypatch.setattr(executor, "_heartbeat_loop", heartbeat)
        executor.register_handler("review", handler)
        result = await executor.run_once()
        return result, cleanup_finished.is_set()

    result, cleanup_finished = asyncio.run(scenario())

    assert result is not None and result.task_id == task.task_id
    assert result.status == "failed"
    assert result.safe_error_code == "upstream_busy"
    assert cleanup_finished is True


def test_sync_handler_heartbeat_failure_does_not_finalize_while_thread_is_running(
    tmp_path: Path,
    monkeypatch,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="heartbeat-sync-thread", max_attempts=1)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="executor-a",
        lease_duration=timedelta(seconds=0.1),
        heartbeat_interval=0.04,
    )
    started = threading.Event()
    allow_finish = threading.Event()

    def handler(_task):
        started.set()
        assert allow_finish.wait(timeout=2)
        return TaskHandlerResult.completed()

    async def broken_heartbeat(*_args):
        await asyncio.sleep(0)
        raise RuntimeError("heartbeat unavailable")

    monkeypatch.setattr(executor, "_heartbeat_loop", broken_heartbeat)
    executor.register_handler("review", handler)

    async def scenario():
        execution = asyncio.create_task(executor.run_once())
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(0.15)
        while_running = await asyncio.to_thread(store.get_task, task.task_id)
        recovery = await asyncio.to_thread(store.recover_interrupted)
        competing_claim = await asyncio.to_thread(
            store.claim_next,
            ClaimLimits(global_limit=1, per_user_limit=1),
            worker_id="competing-worker",
            lease_duration=timedelta(seconds=0.1),
        )
        allow_finish.set()
        result = await asyncio.wait_for(execution, timeout=2)
        return while_running, recovery, competing_claim, result

    try:
        while_running, recovery, competing_claim, result = asyncio.run(scenario())
    finally:
        allow_finish.set()

    assert while_running.status == "running"
    assert recovery.requeued == 0
    assert recovery.failed == 0
    assert competing_claim is None
    assert result is not None and result.status == "failed"
    assert result.safe_error_code == "attempts_exhausted"


def test_run_once_offloads_sqlite_and_sync_handler_without_blocking_event_loop(
    tmp_path: Path,
    monkeypatch,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    submit_task(store, key="sync-handler")
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="executor-a",
        lease_duration=LEASE,
    )
    calls: list[str] = []
    original_to_thread = asyncio.to_thread

    async def recording_to_thread(func, /, *args, **kwargs):
        calls.append(getattr(func, "__name__", type(func).__name__))
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(task_execution_module.asyncio, "to_thread", recording_to_thread)

    def handler(_task):
        time.sleep(0.12)
        return TaskHandlerResult.completed()

    executor.register_handler("review", handler)

    async def scenario():
        running = asyncio.create_task(executor.run_once())
        await asyncio.sleep(0.02)
        loop_was_responsive = not running.done()
        result = await running
        return loop_was_responsive, result

    loop_was_responsive, result = asyncio.run(scenario())

    assert loop_was_responsive is True
    assert result is not None and result.status == "completed"
    assert "claim_next" in calls
    assert "handler" in calls
    assert "complete" in calls


def test_run_once_requeues_owned_task_when_cancelled_then_reraises(tmp_path: Path):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="cancelled-run", max_attempts=2)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="executor-a",
        lease_duration=LEASE,
    )

    async def scenario():
        started = asyncio.Event()

        async def handler(_task):
            started.set()
            await asyncio.Event().wait()

        executor.register_handler("review", handler)
        execution = asyncio.create_task(executor.run_once())
        await asyncio.wait_for(started.wait(), timeout=1)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(scenario())

    released = store.get_task(task.task_id)
    assert released.status == "queued"
    assert released.safe_error_code == "execution_cancelled"
    assert released.worker_id is None


def test_run_forever_recovers_expired_tasks_and_caps_worker_concurrency(tmp_path: Path):
    clock = MutableClock(datetime(2026, 7, 15, 9, 0, tzinfo=UTC))
    store = TaskRepository(tmp_path / "tasks.sqlite3", now_factory=clock)
    tasks = [
        submit_task(
            store,
            key=f"loop-{index}",
            user_id=f"user-{index}",
            max_attempts=3,
        )
        for index in range(5)
    ]
    interrupted = claim(store, "dead-worker")
    assert interrupted is not None
    clock.advance(seconds=31)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=2, per_user_limit=1),
        worker_id="service",
        lease_duration=timedelta(minutes=5),
    )

    async def scenario():
        stop_event = asyncio.Event()
        active = 0
        max_active = 0
        handled: list[str] = []

        async def handler(task):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            handled.append(task.task_id)
            active -= 1
            if len(handled) == len(tasks):
                stop_event.set()

        executor.register_handler("review", handler)
        await asyncio.wait_for(
            executor.run_forever(
                stop_event=stop_event,
                poll_interval=0.001,
                worker_count=7,
            ),
            timeout=2,
        )
        return max_active, handled

    max_active, handled = asyncio.run(scenario())

    assert set(handled) == {task.task_id for task in tasks}
    assert max_active <= 2
    assert all(store.get_task(task.task_id).status == "completed" for task in tasks)
    assert store.get_task(interrupted.task_id).attempts == 2


def test_transition_observer_receives_final_records_and_cannot_break_state(tmp_path: Path):
    observed = []

    def observer(record):
        observed.append(record)
        raise RuntimeError("observer unavailable")

    store = TaskRepository(tmp_path / "tasks.sqlite3", on_transition=observer)
    task = submit_task(store, key="observer-task")
    observed.clear()

    running = claim(store, "worker-a")
    assert running is not None
    completed = store.complete(
        task.task_id,
        worker_id="worker-a",
        lease_token=running.lease_token,
    )

    assert running is not None and running.status == "running"
    assert completed.status == "completed"
    assert [record.status for record in observed] == ["running", "completed"]
    assert store.get_task(task.task_id).status == "completed"


def test_task_lifecycle_observer_updates_status_and_emits_safe_failure(tmp_path: Path):
    task_root = tmp_path / "tasks"
    task_dir = task_root / "writing" / "job-001"
    events_dir = tmp_path / "events"
    observer = TaskLifecycleObserver(
        task_root=task_root,
        ops_event_logger=OpsEventLogger(events_dir),
    )
    store = TaskRepository(tmp_path / "tasks.sqlite3", on_transition=observer)
    task = store.submit(
        idempotency_key="observer-safe-failure",
        channel="wecom",
        user_id="user-001",
        task_type="writer1",
        cost_class="model",
        payload={
            "task_dir": str(task_dir),
            "body": "这是不能进入运维告警的用户正文",
        },
        max_attempts=1,
        resumable=False,
    )

    running = claim(store, "worker-a")
    assert running is not None
    failed = store.fail(
        task.task_id,
        worker_id="worker-a",
        lease_token=running.lease_token,
        safe_error_code="internal_error",
        retryable=False,
    )

    status = json.loads((task_dir / "status.json").read_text(encoding="utf-8"))
    assert status["processing_status"] == "failed"
    assert status["delivery_status"] == "unknown"
    assert status["state_version"] == failed.state_version
    events = read_ops_events(events_dir, date.today())
    assert len(events) == 1
    assert events[0].subject == "后台任务执行失败"
    assert "internal_error" in events[0].detail
    assert "用户正文" not in events[0].detail
    assert str(task_dir) not in events[0].detail


def test_task_lifecycle_observer_rejects_task_dir_outside_managed_root(tmp_path: Path):
    task_root = tmp_path / "tasks"
    outside = tmp_path / "outside" / "job-001"
    observer = TaskLifecycleObserver(task_root=task_root)
    store = TaskRepository(tmp_path / "tasks.sqlite3", on_transition=observer)

    store.submit(
        idempotency_key="observer-outside-root",
        channel="wecom",
        user_id="user-001",
        task_type="writer1",
        cost_class="model",
        payload={"task_dir": str(outside)},
        max_attempts=1,
        resumable=False,
    )

    assert not outside.exists()


def test_fencing_token_blocks_old_attempt_from_same_worker(tmp_path: Path):
    clock = MutableClock(datetime(2026, 7, 15, 9, 0, tzinfo=UTC))
    store = TaskRepository(tmp_path / "tasks.sqlite3", now_factory=clock)
    task = submit_task(store, key="same-worker-fencing", max_attempts=3)
    first = claim(store, "stable-worker")
    assert first is not None and first.lease_token is not None

    clock.advance(seconds=31)
    assert store.recover_interrupted().requeued == 1
    second = claim(store, "stable-worker")
    assert second is not None and second.lease_token is not None

    assert second.lease_token != first.lease_token
    with pytest.raises(TaskOwnershipError, match="token"):
        store.complete(
            task.task_id,
            worker_id="stable-worker",
            lease_token=first.lease_token,
        )

    completed = store.complete(
        task.task_id,
        worker_id="stable-worker",
        lease_token=second.lease_token,
    )
    assert completed.status == "completed"


def test_owned_transition_samples_lease_time_after_acquiring_write_lock(
    tmp_path: Path,
    monkeypatch,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="lock-then-clock")
    running = claim(store, "worker-a")
    assert running is not None
    lock_state = {"acquired": False}
    original_connect = store._connect

    class ConnectionProxy:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, *args, **kwargs):
            result = self._connection.execute(sql, *args, **kwargs)
            if str(sql).strip().upper().startswith("BEGIN IMMEDIATE"):
                lock_state["acquired"] = True
            return result

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def close(self):
            return self._connection.close()

    monkeypatch.setattr(store, "_connect", lambda: ConnectionProxy(original_connect()))

    def locked_clock():
        assert lock_state["acquired"] is True
        return datetime.now(UTC)

    store._now_factory = locked_clock
    completed = store.complete(
        task.task_id,
        worker_id="worker-a",
        lease_token=running.lease_token,
    )

    assert completed.status == "completed"


def test_sync_handler_cancellation_waits_for_thread_and_keeps_lease(tmp_path: Path):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="sync-cancel-fenced", max_attempts=3)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="sync-worker",
        lease_duration=timedelta(seconds=0.4),
        heartbeat_interval=0.1,
    )
    started = threading.Event()
    allow_finish = threading.Event()
    executions = 0

    def handler(_task):
        nonlocal executions
        executions += 1
        started.set()
        assert allow_finish.wait(timeout=2)
        return TaskHandlerResult.completed()

    executor.register_handler("review", handler)

    async def scenario():
        execution = asyncio.create_task(executor.run_once())
        assert await asyncio.to_thread(started.wait, 1)
        execution.cancel()
        await asyncio.sleep(0.15)
        during_cancel = await asyncio.to_thread(store.get_task, task.task_id)
        competing_claim = await asyncio.to_thread(
            store.claim_next,
            ClaimLimits(global_limit=1, per_user_limit=1),
            worker_id="competing-worker",
            lease_duration=timedelta(seconds=0.4),
        )
        allow_finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=2)
        return during_cancel, competing_claim

    try:
        during_cancel, competing_claim = asyncio.run(scenario())
    finally:
        allow_finish.set()

    assert during_cancel.status == "running"
    assert competing_claim is None
    assert executions == 1
    assert store.get_task(task.task_id).status == "completed"


def test_cancelled_claim_waits_for_result_and_releases_claimed_task(
    tmp_path: Path,
    monkeypatch,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="cancel-during-claim", max_attempts=3)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="claim-worker",
        lease_duration=LEASE,
    )
    original_claim = store.claim_next
    claimed = threading.Event()
    allow_return = threading.Event()
    handler_calls = 0

    def slow_claim(*args, **kwargs):
        record = original_claim(*args, **kwargs)
        claimed.set()
        assert allow_return.wait(timeout=2)
        return record

    async def handler(_task):
        nonlocal handler_calls
        handler_calls += 1

    monkeypatch.setattr(store, "claim_next", slow_claim)
    executor.register_handler("review", handler)

    async def scenario():
        execution = asyncio.create_task(executor.run_once())
        assert await asyncio.to_thread(claimed.wait, 1)
        execution.cancel()
        allow_return.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=2)
        await asyncio.sleep(0.02)

    try:
        asyncio.run(scenario())
    finally:
        allow_return.set()

    released = store.get_task(task.task_id)
    assert released.status == "queued"
    assert released.worker_id is None
    assert released.lease_token is None
    assert handler_calls == 0


def test_repeated_cancellation_during_claim_still_releases_claimed_task(
    tmp_path: Path,
    monkeypatch,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="cancel-during-claim-twice", max_attempts=3)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="claim-worker",
        lease_duration=LEASE,
    )
    original_claim = store.claim_next
    claimed = threading.Event()
    allow_return = threading.Event()

    def slow_claim(*args, **kwargs):
        record = original_claim(*args, **kwargs)
        claimed.set()
        assert allow_return.wait(timeout=2)
        return record

    monkeypatch.setattr(store, "claim_next", slow_claim)
    executor.register_handler("review", lambda _task: None)

    async def scenario():
        execution = asyncio.create_task(executor.run_once())
        assert await asyncio.to_thread(claimed.wait, 1)
        execution.cancel()
        await asyncio.sleep(0)
        execution.cancel()
        allow_return.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=2)

    try:
        asyncio.run(scenario())
    finally:
        allow_return.set()

    released = store.get_task(task.task_id)
    assert released.status == "queued"
    assert released.worker_id is None


def test_cancellation_during_execution_guard_acquisition_releases_guard_and_claim(
    tmp_path: Path,
    monkeypatch,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="cancel-during-execution-guard", max_attempts=3)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="guard-worker",
        lease_duration=LEASE,
    )
    original_acquire = store.acquire_execution_guard
    guard_acquired = threading.Event()
    allow_return = threading.Event()

    def slow_acquire(*args, **kwargs):
        guard = original_acquire(*args, **kwargs)
        guard_acquired.set()
        assert allow_return.wait(timeout=2)
        return guard

    monkeypatch.setattr(store, "acquire_execution_guard", slow_acquire)
    executor.register_handler("review", lambda _task: None)

    async def scenario():
        execution = asyncio.create_task(executor.run_once())
        assert await asyncio.to_thread(guard_acquired.wait, 1)
        execution.cancel()
        allow_return.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=2)

    try:
        asyncio.run(scenario())
    finally:
        allow_return.set()

    released = store.get_task(task.task_id)
    assert released.status == "queued"
    monkeypatch.setattr(store, "acquire_execution_guard", original_acquire)
    probe = store.acquire_execution_guard(task.task_id, blocking=False)
    assert probe is not None
    probe.release()


def test_expired_claim_recovered_before_guard_acquisition_never_starts_old_handler(
    tmp_path: Path,
    monkeypatch,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="recover-before-guard", max_attempts=3)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="old-worker",
        lease_duration=timedelta(seconds=0.1),
        heartbeat_interval=0.04,
    )
    original_acquire = store.acquire_execution_guard
    before_flock = threading.Event()
    allow_acquire = threading.Event()
    handler_calls = 0
    delay_lock = threading.Lock()
    delayed_once = False

    def delayed_acquire(*args, **kwargs):
        nonlocal delayed_once
        with delay_lock:
            should_delay = not delayed_once
            if should_delay:
                delayed_once = True
        if should_delay:
            before_flock.set()
            assert allow_acquire.wait(timeout=2)
        return original_acquire(*args, **kwargs)

    def handler(_task):
        nonlocal handler_calls
        handler_calls += 1

    monkeypatch.setattr(store, "acquire_execution_guard", delayed_acquire)
    executor.register_handler("review", handler)

    async def scenario():
        execution = asyncio.create_task(executor.run_once())
        assert await asyncio.to_thread(before_flock.wait, 1)
        await asyncio.sleep(0.15)
        recovery = await asyncio.to_thread(store.recover_interrupted)
        allow_acquire.set()
        result = await asyncio.wait_for(execution, timeout=2)
        return recovery, result

    try:
        recovery, result = asyncio.run(scenario())
    finally:
        allow_acquire.set()

    assert recovery.requeued == 1
    assert handler_calls == 0
    assert result is not None and result.status == "queued"
    assert store.get_task(task.task_id).status == "queued"


def test_cancelled_sync_handler_exception_preserves_cancellation_signal(tmp_path: Path):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="cancel-sync-error", max_attempts=3)
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="sync-worker",
        lease_duration=LEASE,
    )
    started = threading.Event()
    allow_raise = threading.Event()

    def handler(_task):
        started.set()
        assert allow_raise.wait(timeout=2)
        raise RuntimeError("sync failure after cancellation")

    executor.register_handler("review", handler)

    async def scenario():
        execution = asyncio.create_task(executor.run_once())
        assert await asyncio.to_thread(started.wait, 1)
        execution.cancel()
        allow_raise.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(execution, timeout=2)

    try:
        asyncio.run(scenario())
    finally:
        allow_raise.set()

    released = store.get_task(task.task_id)
    assert released.status == "queued"
    assert released.safe_error_code == "execution_cancelled"


def test_run_forever_periodically_recovers_task_that_expires_after_startup(
    tmp_path: Path,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(store, key="periodic-recovery", max_attempts=3)
    dead_claim = store.claim_next(
        ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="dead-worker",
        lease_duration=timedelta(seconds=0.12),
    )
    assert dead_claim is not None
    executor = PersistentTaskExecutor(
        repository=store,
        limits=ClaimLimits(global_limit=1, per_user_limit=1),
        worker_id="service",
        lease_duration=timedelta(seconds=0.4),
        heartbeat_interval=0.1,
    )

    async def scenario():
        stop_event = asyncio.Event()

        async def handler(_task):
            stop_event.set()

        executor.register_handler("review", handler)
        await asyncio.wait_for(
            executor.run_forever(
                stop_event=stop_event,
                poll_interval=0.005,
                recovery_interval=0.02,
            ),
            timeout=2,
        )

    asyncio.run(scenario())

    completed = store.get_task(task.task_id)
    assert completed.status == "completed"
    assert completed.attempts == 2


def test_heartbeat_interval_must_not_exceed_half_the_lease(tmp_path: Path):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    limits = ClaimLimits(global_limit=1, per_user_limit=1)

    with pytest.raises(ValueError, match="heartbeat_interval"):
        PersistentTaskExecutor(
            repository=store,
            limits=limits,
            lease_duration=timedelta(seconds=10),
            heartbeat_interval=6,
        )

    PersistentTaskExecutor(
        repository=store,
        limits=limits,
        lease_duration=timedelta(seconds=10),
        heartbeat_interval=5,
    )


def test_resume_needs_input_merges_or_replaces_payload_and_increments_version(
    tmp_path: Path,
):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    task = submit_task(
        store,
        key="resume-input",
        payload={"body": "原始正文", "material_path": "/tasks/input.docx"},
        max_attempts=3,
    )
    first = claim(store, "worker-a")
    assert first is not None
    waiting = store.mark_needs_input(
        task.task_id,
        worker_id="worker-a",
        lease_token=first.lease_token,
        safe_error_code="awaiting_user",
    )

    merged = store.resume_needs_input(
        task.task_id,
        payload={"clarification": "补充说明"},
        merge=True,
    )
    duplicate = submit_task(
        store,
        key="resume-input",
        payload={"body": "重复 submit 不得覆盖"},
        max_attempts=9,
    )

    assert merged.status == "queued"
    assert merged.payload == {
        "body": "原始正文",
        "material_path": "/tasks/input.docx",
        "clarification": "补充说明",
    }
    assert merged.state_version == waiting.state_version + 1
    assert duplicate.payload == merged.payload

    second = claim(store, "worker-b")
    assert second is not None
    store.mark_needs_input(
        task.task_id,
        worker_id="worker-b",
        lease_token=second.lease_token,
    )
    replaced = store.resume_needs_input(
        task.task_id,
        payload={"body": "替换后的安全正文"},
        merge=False,
    )
    assert replaced.payload == {"body": "替换后的安全正文"}

    with pytest.raises(InvalidTaskTransitionError, match="queued"):
        store.resume_needs_input(task.task_id, payload={"extra": "invalid"})


def test_schema_migration_adds_lease_and_state_columns_transactionally(tmp_path: Path):
    db_path = tmp_path / "legacy" / "tasks.sqlite3"
    _create_legacy_task_db_without_heartbeat(db_path)

    store = TaskRepository(db_path)
    migrated = store.get_task("legacy-running")
    second_open = TaskRepository(db_path)

    assert store.schema_version >= 2
    assert second_open.schema_version == store.schema_version
    assert second_open.count_tasks() == 1
    assert migrated.status == "running"
    assert migrated.heartbeat_at == "2026-07-15T09:00:00+00:00"
    assert migrated.lease_token is not None
    assert migrated.state_version == 1
    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        legacy_tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'tasks_legacy%'"
        ).fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert {"heartbeat_at", "lease_token", "state_version"} <= columns
    assert legacy_tables == []
    assert integrity == "ok"


def test_schema_migration_adds_missing_lease_expiry_and_makes_running_task_recoverable(tmp_path: Path):
    db_path = tmp_path / "legacy-no-expiry" / "tasks.sqlite3"
    _create_legacy_task_db_without_lease_expiry(db_path)

    store = TaskRepository(
        db_path,
        now_factory=lambda: datetime(2026, 7, 15, 9, 1, tzinfo=UTC),
    )
    migrated = store.get_task("legacy-no-expiry")

    assert migrated.lease_expires_at == "2026-07-15T09:00:00+00:00"
    assert store.recover_interrupted().requeued == 1


def test_repository_rejects_future_schema_version_without_downgrading(tmp_path: Path):
    db_path = tmp_path / "future" / "tasks.sqlite3"
    _create_legacy_task_db_without_heartbeat(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 999")
        before_indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            )
        }

    with pytest.raises(RuntimeError, match="高于当前程序"):
        TaskRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 999
        after_indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert after_indexes == before_indexes


def test_initialize_checks_schema_version_while_holding_write_transaction(
    tmp_path: Path,
    monkeypatch,
):
    observed_transaction_states: list[bool] = []
    original_connect = TaskRepository._connect

    class ConnectionProxy:
        def __init__(self, connection):
            self._connection = connection

        def execute(self, sql, *args, **kwargs):
            if str(sql).strip().upper() == "PRAGMA USER_VERSION":
                observed_transaction_states.append(self._connection.in_transaction)
            return self._connection.execute(sql, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._connection, name)

        def close(self):
            return self._connection.close()

    def wrapped_connect(repository):
        return ConnectionProxy(original_connect(repository))

    monkeypatch.setattr(TaskRepository, "_connect", wrapped_connect)
    TaskRepository(tmp_path / "tasks.sqlite3")

    assert observed_transaction_states
    assert all(observed_transaction_states)


def test_database_directory_and_file_permissions_are_private(tmp_path: Path):
    db_dir = tmp_path / "queue-data"
    db_dir.mkdir(mode=0o755)
    db_path = db_dir / "tasks.sqlite3"

    TaskRepository(db_path)

    assert stat.S_IMODE(db_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(db_path.stat().st_mode) == 0o600


def test_payload_values_reject_obvious_credentials_but_allow_normal_text(
    tmp_path: Path,
):
    db_path = tmp_path / "tasks.sqlite3"
    store = TaskRepository(db_path)
    credential_values = (
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "Bearer short123",
        "api_key=sk-live-abcdefghijklmnop",
        "x-api-key: abc",
        "password: verysecretvalue",
        "password=x",
    )
    credential_payloads = (
        {"passphrase": "actual-secret"},
        {"access_key": "AKIAEXAMPLE"},
        {"private_key": "raw-private-key-material"},
    )

    for index, value in enumerate(credential_values):
        with pytest.raises(ValueError, match="敏感"):
            submit_task(
                store,
                key=f"credential-{index}",
                payload={"body": value},
            )

    for index, payload in enumerate(credential_payloads):
        with pytest.raises(ValueError, match="敏感"):
            submit_task(
                store,
                key=f"credential-field-{index}",
                payload=payload,
            )

    allowed = submit_task(
        store,
        key="normal-security-text",
        payload={
            "body": "正文讨论 token 预算和 Bearer 认证概念，不包含凭据。",
            "material_path": "/tasks/material/report.docx",
        },
    )
    assert allowed.status == "queued"
    with sqlite3.connect(db_path) as conn:
        dump = "\n".join(conn.iterdump())
    assert all(value not in dump for value in credential_values)


def test_multiprocess_submit_same_key_creates_exactly_one_task(tmp_path: Path):
    db_path = tmp_path / "tasks.sqlite3"
    TaskRepository(db_path)
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_process_submit_same_key,
            args=(str(db_path), start_event, result_queue, index),
        )
        for index in range(6)
    ]

    _run_processes(processes, start_event)
    results = [result_queue.get(timeout=2) for _ in processes]

    assert {result[0] for result in results} == {"ok"}
    assert len({result[1] for result in results}) == 1
    assert TaskRepository(db_path).count_tasks() == 1


def test_multiprocess_claim_respects_global_limit(tmp_path: Path):
    db_path = tmp_path / "tasks.sqlite3"
    store = TaskRepository(db_path)
    for index in range(4):
        submit_task(store, key=f"mp-claim-{index}", user_id=f"user-{index}")
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_process_claim_with_limits,
            args=(str(db_path), start_event, result_queue, index),
        )
        for index in range(6)
    ]

    _run_processes(processes, start_event)
    results = [result_queue.get(timeout=2) for _ in processes]
    claimed = [result for result in results if result[0] == "ok" and result[1]]

    assert {result[0] for result in results} == {"ok"}
    assert len(claimed) == 2
    assert len({result[1] for result in claimed}) == 2
    assert all(result[2] for result in claimed)
    with sqlite3.connect(db_path) as conn:
        running_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = 'running'"
        ).fetchone()[0]
    assert running_count == 2


def test_repository_schema_checks_and_sqlite_pragmas(tmp_path: Path):
    db_path = tmp_path / "tasks.sqlite3"
    store = TaskRepository(db_path, busy_timeout_ms=2345)
    task = submit_task(store, key="schema-task")

    with store._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET status = 'unknown' WHERE task_id = ?", (task.task_id,))
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET attempts = -1 WHERE task_id = ?", (task.task_id,))
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE tasks SET worker_id = 'orphan' WHERE task_id = ?", (task.task_id,))

    assert mode.lower() == "wal"
    assert timeout == 2345


def test_repository_reports_active_tasks_for_selected_user_and_types(tmp_path: Path):
    store = TaskRepository(tmp_path / "tasks.sqlite3")
    writing = submit_task(
        store,
        key="writing-1",
        user_id="user-001",
        task_type="writing_writer1",
    )
    submit_task(
        store,
        key="review-1",
        user_id="user-001",
        task_type="review_general_docx",
    )

    assert store.has_active_task(
        user_id="user-001",
        task_types={"writing_writer1"},
    ) is True
    assert store.has_active_task(
        user_id="other-user",
        task_types={"writing_writer1"},
    ) is False

    claimed = claim(store, "worker-1")
    assert claimed is not None and claimed.task_id == writing.task_id
    store.complete(
        writing.task_id,
        worker_id="worker-1",
        lease_token=claimed.lease_token or "",
    )

    assert store.has_active_task(
        user_id="user-001",
        task_types={"writing_writer1"},
    ) is False
