from __future__ import annotations

import asyncio
import fcntl
import hashlib
import inspect
import json
import os
import re
import sqlite3
from contextlib import closing, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Literal, Mapping, cast
from uuid import uuid4

from app.platform.ops.events import OpsEventLogger
from app.platform.task_status import read_task_status, update_task_status, write_task_status


TaskStatus = Literal[
    "queued",
    "running",
    "needs_input",
    "completed",
    "failed",
    "cancelled",
]

MAX_SAFE_ERROR_CODE_LENGTH = 64
_SAFE_ERROR_CODE_PATTERN = re.compile(
    rf"^[a-z0-9_]{{1,{MAX_SAFE_ERROR_CODE_LENGTH}}}$"
)
_SENSITIVE_ERROR_FRAGMENTS = (
    "path",
    "token",
    "secret",
    "req_id",
    "request_id",
    "password",
    "api_key",
    "authorization",
    "cookie",
    "bearer",
)
_SENSITIVE_PAYLOAD_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "apikey",
    "authorization",
    "cookie",
    "passphrase",
    "accesskey",
    "privatekey",
    "credential",
)
_SENSITIVE_PAYLOAD_VALUE_PATTERN = re.compile(
    r"(?:authorization\s*:\s*bearer\s+\S+|"
    r"(?<![a-z0-9_-])bearer\s+[a-z0-9._~-]{8,}|"
    r"(?:x[-_])?api[-_]?key\s*[:=]\s*\S+|"
    r"password\s*[:=]\s*\S+|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.IGNORECASE,
)
SCHEMA_VERSION = 3


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    idempotency_key: str
    channel: str
    user_id: str
    task_type: str
    cost_class: str
    payload: dict[str, object]
    status: TaskStatus
    attempts: int
    max_attempts: int
    resumable: bool
    worker_id: str | None
    lease_token: str | None
    lease_expires_at: str | None
    heartbeat_at: str | None
    created_at: str
    updated_at: str
    safe_error_code: str | None
    state_version: int


@dataclass(frozen=True)
class ClaimLimits:
    global_limit: int
    per_user_limit: int
    cost_class_limits: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RecoverySummary:
    requeued: int
    failed: int


@dataclass(frozen=True)
class TaskHandlerResult:
    status: Literal["completed", "needs_input"]
    safe_error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"completed", "needs_input"}:
            raise ValueError(f"不支持的 handler 结果状态：{self.status}")
        _validate_safe_error_code(self.safe_error_code, optional=True)
        if self.status == "completed" and self.safe_error_code is not None:
            raise ValueError("completed 结果不能包含 safe_error_code")

    @classmethod
    def completed(cls) -> TaskHandlerResult:
        return cls(status="completed")

    @classmethod
    def needs_input(cls, safe_error_code: str | None = None) -> TaskHandlerResult:
        return cls(status="needs_input", safe_error_code=safe_error_code)


class InvalidTaskTransitionError(RuntimeError):
    pass


class TaskOwnershipError(RuntimeError):
    pass


class TaskLeaseExpiredError(TaskOwnershipError):
    pass


class IdempotencyConflictError(RuntimeError):
    pass


class InvalidSafeErrorCodeError(ValueError):
    pass


class SafeTaskError(RuntimeError):
    def __init__(self, safe_error_code: str, *, retryable: bool = False) -> None:
        _validate_safe_error_code(safe_error_code)
        super().__init__(safe_error_code)
        self.safe_error_code = safe_error_code
        self.retryable = retryable


class TaskExecutionGuard:
    """跨进程持有某次任务的实际执行权，进程退出时由操作系统自动释放。"""

    def __init__(self, file_descriptor: int) -> None:
        self._file_descriptor = file_descriptor

    def release(self) -> None:
        if self._file_descriptor < 0:
            return
        try:
            fcntl.flock(self._file_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._file_descriptor)
            self._file_descriptor = -1


TaskHandler = Callable[
    [TaskRecord],
    TaskHandlerResult | None | Awaitable[TaskHandlerResult | None],
]
TaskTransitionObserver = Callable[[TaskRecord], object]


def build_idempotency_key(channel: str, user_id: str, message_id: str) -> str:
    """用稳定 SHA-256 摘要隔离消息身份，不在 key 中保留明文。"""

    values = (channel, user_id, message_id)
    if any(not value.strip() for value in values):
        raise ValueError("channel、user_id、message_id 均不能为空")
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TaskLifecycleObserver:
    """把队列状态安全同步到任务目录，并在最终失败时写入运维事件。"""

    def __init__(
        self,
        *,
        task_root: str | Path,
        ops_event_logger: OpsEventLogger | None = None,
    ) -> None:
        self._task_root = Path(task_root).resolve(strict=False)
        self._ops_event_logger = ops_event_logger

    def __call__(self, record: TaskRecord) -> None:
        task_dir = self._resolve_task_dir(record)
        if task_dir is not None:
            self._write_status(task_dir, record.status, record.state_version)
        if record.status == "failed":
            self._record_failure(record)

    def _resolve_task_dir(self, record: TaskRecord) -> Path | None:
        raw = record.payload.get("task_dir")
        if not isinstance(raw, str) or not raw.strip():
            return None
        candidate = Path(raw).expanduser().resolve(strict=False)
        if candidate == self._task_root or not candidate.is_relative_to(self._task_root):
            return None
        return candidate

    @staticmethod
    def _write_status(task_dir: Path, status: TaskStatus, state_version: int) -> None:
        current = read_task_status(task_dir)
        if current:
            update_task_status(
                task_dir,
                processing_status=status,
                source="task_execution",
                state_version=state_version,
            )
            return
        write_task_status(
            task_dir,
            processing_status=status,
            delivery_status="unknown",
            source="task_execution",
            state_version=state_version,
        )

    def _record_failure(self, record: TaskRecord) -> None:
        if self._ops_event_logger is None:
            return
        safe_code = record.safe_error_code or "internal_error"
        detail = (
            f"任务编号: {record.task_id}\n"
            f"安全错误码: {safe_code}\n"
            f"执行次数: {record.attempts}/{record.max_attempts}"
        )
        safe_skill_id = (
            record.task_type
            if re.fullmatch(r"[a-z0-9_-]{1,64}", record.task_type)
            else ""
        )
        try:
            self._ops_event_logger.record(
                source="task_execution",
                severity="error",
                subject="后台任务执行失败",
                detail=detail,
                sender_userid=record.user_id,
                skill_id=safe_skill_id,
                job_id=record.task_id,
            )
        except Exception:
            return


class TaskRepository:
    """基于 SQLite 的持久化任务仓库。"""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        now_factory: Callable[[], datetime] | None = None,
        on_transition: TaskTransitionObserver | None = None,
    ) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms 不能小于 0")
        self._db_path = Path(db_path)
        self._busy_timeout_ms = busy_timeout_ms
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._on_transition = on_transition
        self._db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._db_path.parent, 0o700)
        self._execution_locks_dir = self._db_path.parent / "execution-locks"
        self._execution_locks_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._execution_locks_dir, 0o700)
        self._initialize()
        os.chmod(self._db_path, 0o600)

    @property
    def schema_version(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def submit(
        self,
        *,
        idempotency_key: str,
        channel: str,
        user_id: str,
        task_type: str,
        cost_class: str,
        payload: Mapping[str, object],
        max_attempts: int,
        resumable: bool,
    ) -> TaskRecord:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key 不能为空")
        if max_attempts < 1:
            raise ValueError("max_attempts 必须大于等于 1")

        payload_json = self._serialize_payload(payload)
        timestamp = self._timestamp()
        task_id = f"task-{uuid4().hex}"
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM tasks WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                self._ensure_idempotency_identity(
                    existing,
                    channel=channel,
                    user_id=user_id,
                    task_type=task_type,
                    cost_class=cost_class,
                )
                conn.commit()
                return self._row_to_record(existing)
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id,
                    idempotency_key,
                    channel,
                    user_id,
                    task_type,
                    cost_class,
                    payload_json,
                    status,
                    attempts,
                    max_attempts,
                    resumable,
                    worker_id,
                    lease_token,
                    lease_expires_at,
                    heartbeat_at,
                    created_at,
                    updated_at,
                    safe_error_code,
                    state_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, NULL, NULL, NULL, NULL, ?, ?, NULL, 1)
                """,
                (
                    task_id,
                    idempotency_key,
                    channel,
                    user_id,
                    task_type,
                    cost_class,
                    payload_json,
                    max_attempts,
                    1 if resumable else 0,
                    timestamp,
                    timestamp,
                ),
            )
            record = self._row_to_record(self._fetch_row(conn, task_id))
            conn.commit()
        self._notify_transition(record)
        return record

    def get_task(self, task_id: str) -> TaskRecord:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"未知任务：{task_id}")
        return self._row_to_record(row)

    def count_tasks(self) -> int:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()
        return int(row[0])

    def has_active_task(self, *, user_id: str, task_types: set[str]) -> bool:
        if not user_id.strip() or not task_types or any(not item.strip() for item in task_types):
            return False
        ordered_types = sorted(task_types)
        placeholders = ", ".join("?" for _ in ordered_types)
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"""
                SELECT 1
                FROM tasks
                WHERE user_id = ?
                  AND task_type IN ({placeholders})
                  AND status IN ('queued', 'running', 'needs_input')
                LIMIT 1
                """,
                (user_id, *ordered_types),
            ).fetchone()
        return row is not None

    def acquire_execution_guard(
        self,
        task_id: str,
        *,
        blocking: bool = False,
    ) -> TaskExecutionGuard | None:
        if not task_id.strip() or len(task_id) > 256:
            raise ValueError("task_id 格式无效")
        lock_name = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        lock_path = self._execution_locks_dir / f"{lock_name}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(lock_path, flags, 0o600)
        os.fchmod(file_descriptor, 0o600)
        operation = fcntl.LOCK_EX
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(file_descriptor, operation)
        except BlockingIOError:
            os.close(file_descriptor)
            return None
        return TaskExecutionGuard(file_descriptor)

    def validate_claim(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
    ) -> TaskRecord:
        """取得执行锁后再次确认 claim 仍有效，阻断锁前恢复竞态。"""

        self._validate_worker_id(worker_id)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            timestamp = self._timestamp()
            row = self._fetch_row(conn, task_id)
            if row["status"] != "running":
                raise TaskOwnershipError(
                    f"worker {worker_id} 的任务 {task_id} 已不再处于 running 状态"
                )
            self._ensure_owned_running(
                task_id,
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                now=timestamp,
                to_status="running",
            )
            record = self._row_to_record(row)
            conn.commit()
        return record

    def claim_next(
        self,
        limits: ClaimLimits,
        *,
        worker_id: str,
        lease_duration: timedelta,
    ) -> TaskRecord | None:
        self._validate_limits(limits)
        self._validate_worker_id(worker_id)
        self._validate_lease_duration(lease_duration)
        lease_token = uuid4().hex

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = self._now_utc()
            timestamp = self._format_timestamp(now)
            lease_expires_at = self._format_timestamp(now + lease_duration)
            if self._count_running(conn) >= limits.global_limit:
                conn.commit()
                return None

            cost_clause, cost_params = self._cost_limit_sql(limits.cost_class_limits)
            candidate = conn.execute(
                f"""
                SELECT q.task_id
                FROM tasks AS q
                WHERE q.status = 'queued'
                  AND q.attempts < q.max_attempts
                  AND (
                      SELECT COUNT(*)
                      FROM tasks AS running_user
                      WHERE running_user.status = 'running'
                        AND running_user.user_id = q.user_id
                  ) < ?
                  {cost_clause}
                ORDER BY q.created_at ASC, q.task_id ASC
                LIMIT 1
                """,
                (limits.per_user_limit, *cost_params),
            ).fetchone()
            if candidate is None:
                conn.commit()
                return None

            claimed = conn.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    attempts = attempts + 1,
                    worker_id = ?,
                    lease_token = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    updated_at = ?,
                    safe_error_code = NULL,
                    state_version = state_version + 1
                WHERE task_id = ?
                  AND status = 'queued'
                  AND attempts < max_attempts
                """,
                (
                    worker_id,
                    lease_token,
                    lease_expires_at,
                    timestamp,
                    timestamp,
                    candidate["task_id"],
                ),
            )
            if claimed.rowcount != 1:
                conn.commit()
                return None
            record = self._row_to_record(
                self._fetch_row(conn, str(candidate["task_id"]))
            )
            conn.commit()
        self._notify_transition(record)
        return record

    def renew_lease(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
        lease_duration: timedelta,
    ) -> TaskRecord:
        self._validate_worker_id(worker_id)
        self._validate_lease_duration(lease_duration)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = self._now_utc()
            timestamp = self._format_timestamp(now)
            lease_expires_at = self._format_timestamp(now + lease_duration)
            row = self._fetch_row(conn, task_id)
            self._ensure_owned_running(
                task_id,
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                now=timestamp,
                to_status="running",
            )
            conn.execute(
                """
                UPDATE tasks
                SET lease_expires_at = ?,
                    heartbeat_at = ?,
                    updated_at = ?,
                    state_version = state_version + 1
                WHERE task_id = ?
                """,
                (lease_expires_at, timestamp, timestamp, task_id),
            )
            record = self._row_to_record(self._fetch_row(conn, task_id))
            conn.commit()
        return record

    def complete(
        self, task_id: str, *, worker_id: str, lease_token: str
    ) -> TaskRecord:
        return self._transition_owned(
            task_id,
            worker_id=worker_id,
            lease_token=lease_token,
            to_status="completed",
            safe_error_code=None,
        )

    def mark_needs_input(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
        safe_error_code: str | None = None,
    ) -> TaskRecord:
        _validate_safe_error_code(safe_error_code, optional=True)
        return self._transition_owned(
            task_id,
            worker_id=worker_id,
            lease_token=lease_token,
            to_status="needs_input",
            safe_error_code=safe_error_code,
        )

    def fail(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
        safe_error_code: str,
        retryable: bool,
    ) -> TaskRecord:
        _validate_safe_error_code(safe_error_code)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            timestamp = self._timestamp()
            row = self._fetch_row(conn, task_id)
            self._ensure_owned_running(
                task_id,
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                now=timestamp,
                to_status="queued" if retryable else "failed",
            )
            if retryable and int(row["attempts"]) < int(row["max_attempts"]):
                target_status: TaskStatus = "queued"
            else:
                target_status = "failed"
            conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    worker_id = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    updated_at = ?,
                    safe_error_code = ?,
                    state_version = state_version + 1
                WHERE task_id = ?
                """,
                (target_status, timestamp, safe_error_code, task_id),
            )
            record = self._row_to_record(self._fetch_row(conn, task_id))
            conn.commit()
        self._notify_transition(record)
        return record

    def cancel(self, task_id: str) -> TaskRecord:
        return self._transition_unowned(
            task_id,
            from_statuses={"queued", "needs_input"},
            to_status="cancelled",
            safe_error_code=None,
        )

    def resume_needs_input(
        self,
        task_id: str,
        *,
        payload: Mapping[str, object],
        merge: bool = True,
    ) -> TaskRecord:
        """把用户补充材料原子地写回等待任务，并重新排队。"""

        payload_json = self._serialize_payload(payload)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetch_row(conn, task_id)
            self._ensure_transition_allowed(
                task_id,
                current_status=str(row["status"]),
                from_statuses={"needs_input"},
                to_status="queued",
            )
            if merge:
                current_payload = json.loads(row["payload_json"])
                if not isinstance(current_payload, dict):
                    raise TypeError("任务 payload 必须是 JSON 对象")
                current_payload.update(json.loads(payload_json))
                payload_json = self._serialize_payload(current_payload)
            conn.execute(
                """
                UPDATE tasks
                SET status = 'queued',
                    payload_json = ?,
                    updated_at = ?,
                    safe_error_code = NULL,
                    state_version = state_version + 1
                WHERE task_id = ?
                """,
                (payload_json, self._timestamp(), task_id),
            )
            record = self._row_to_record(self._fetch_row(conn, task_id))
            conn.commit()
        self._notify_transition(record)
        return record

    def release_cancelled(
        self, task_id: str, *, worker_id: str, lease_token: str
    ) -> TaskRecord:
        """执行协程被取消时释放当前 owner，并按可恢复性安全收敛状态。"""

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            timestamp = self._timestamp()
            row = self._fetch_row(conn, task_id)
            self._ensure_owned_running(
                task_id,
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                now=timestamp,
                to_status="queued",
            )
            if int(row["attempts"]) >= int(row["max_attempts"]):
                target_status: TaskStatus = "failed"
                safe_error_code = "attempts_exhausted"
            elif bool(row["resumable"]):
                target_status = "queued"
                safe_error_code = "execution_cancelled"
            else:
                target_status = "failed"
                safe_error_code = "interrupted_not_resumable"
            conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    worker_id = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    updated_at = ?,
                    safe_error_code = ?,
                    state_version = state_version + 1
                WHERE task_id = ?
                """,
                (target_status, timestamp, safe_error_code, task_id),
            )
            record = self._row_to_record(self._fetch_row(conn, task_id))
            conn.commit()
        self._notify_transition(record)
        return record

    def recover_interrupted(self) -> RecoverySummary:
        requeued = 0
        failed = 0
        recovered_records: list[TaskRecord] = []
        held_guards: list[TaskExecutionGuard] = []
        try:
            with closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                timestamp = self._timestamp()
                rows = conn.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE status = 'running'
                      AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                    ORDER BY lease_expires_at ASC, task_id ASC
                    """,
                    (timestamp,),
                ).fetchall()
                for row in rows:
                    guard = self.acquire_execution_guard(str(row["task_id"]), blocking=False)
                    if guard is None:
                        continue
                    held_guards.append(guard)
                    if int(row["attempts"]) >= int(row["max_attempts"]):
                        target_status: TaskStatus = "failed"
                        safe_error_code: str | None = "attempts_exhausted"
                        failed += 1
                    elif bool(row["resumable"]):
                        target_status = "queued"
                        safe_error_code = None
                        requeued += 1
                    else:
                        target_status = "failed"
                        safe_error_code = "interrupted_not_resumable"
                        failed += 1
                    conn.execute(
                        """
                        UPDATE tasks
                        SET status = ?,
                            worker_id = NULL,
                            lease_token = NULL,
                            lease_expires_at = NULL,
                            heartbeat_at = NULL,
                            updated_at = ?,
                            safe_error_code = ?,
                            state_version = state_version + 1
                        WHERE task_id = ?
                          AND status = 'running'
                          AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                        """,
                        (
                            target_status,
                            timestamp,
                            safe_error_code,
                            row["task_id"],
                            timestamp,
                        ),
                    )
                    recovered_records.append(
                        self._row_to_record(self._fetch_row(conn, str(row["task_id"])))
                    )
                conn.commit()
        finally:
            for guard in held_guards:
                guard.release()
        for record in recovered_records:
            self._notify_transition(record)
        return RecoverySummary(requeued=requeued, failed=failed)

    def _transition_owned(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_token: str,
        to_status: TaskStatus,
        safe_error_code: str | None,
    ) -> TaskRecord:
        self._validate_worker_id(worker_id)
        _validate_safe_error_code(safe_error_code, optional=True)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            timestamp = self._timestamp()
            row = self._fetch_row(conn, task_id)
            self._ensure_owned_running(
                task_id,
                row,
                worker_id=worker_id,
                lease_token=lease_token,
                now=timestamp,
                to_status=to_status,
            )
            conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    worker_id = NULL,
                    lease_token = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    updated_at = ?,
                    safe_error_code = ?,
                    state_version = state_version + 1
                WHERE task_id = ?
                """,
                (to_status, timestamp, safe_error_code, task_id),
            )
            record = self._row_to_record(self._fetch_row(conn, task_id))
            conn.commit()
        self._notify_transition(record)
        return record

    def _transition_unowned(
        self,
        task_id: str,
        *,
        from_statuses: set[TaskStatus],
        to_status: TaskStatus,
        safe_error_code: str | None,
    ) -> TaskRecord:
        _validate_safe_error_code(safe_error_code, optional=True)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetch_row(conn, task_id)
            self._ensure_transition_allowed(
                task_id,
                current_status=row["status"],
                from_statuses=from_statuses,
                to_status=to_status,
            )
            conn.execute(
                """
                UPDATE tasks
                SET status = ?,
                    updated_at = ?,
                    safe_error_code = ?,
                    state_version = state_version + 1
                WHERE task_id = ?
                """,
                (to_status, self._timestamp(), safe_error_code, task_id),
            )
            record = self._row_to_record(self._fetch_row(conn, task_id))
            conn.commit()
        self._notify_transition(record)
        return record

    def _initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current_version > SCHEMA_VERSION:
                conn.rollback()
                raise RuntimeError(
                    f"任务队列 schema 版本 {current_version} 高于当前程序支持的 {SCHEMA_VERSION}"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    channel TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    cost_class TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'queued',
                            'running',
                            'needs_input',
                            'completed',
                            'failed',
                            'cancelled'
                        )
                    ),
                    attempts INTEGER NOT NULL CHECK (attempts >= 0),
                    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
                    resumable INTEGER NOT NULL CHECK (resumable IN (0, 1)),
                    worker_id TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_version INTEGER NOT NULL DEFAULT 1 CHECK (state_version >= 1),
                    safe_error_code TEXT CHECK (
                        safe_error_code IS NULL OR (
                            length(safe_error_code) BETWEEN 1 AND 64
                            AND safe_error_code NOT GLOB '*[^a-z0-9_]*'
                            AND instr(safe_error_code, 'path') = 0
                            AND instr(safe_error_code, 'token') = 0
                            AND instr(safe_error_code, 'secret') = 0
                            AND instr(safe_error_code, 'req_id') = 0
                            AND instr(safe_error_code, 'request_id') = 0
                            AND instr(safe_error_code, 'password') = 0
                            AND instr(safe_error_code, 'api_key') = 0
                            AND instr(safe_error_code, 'authorization') = 0
                            AND instr(safe_error_code, 'cookie') = 0
                            AND instr(safe_error_code, 'bearer') = 0
                        )
                    ),
                    CHECK (attempts <= max_attempts),
                    CHECK (
                        (
                            status = 'running'
                            AND worker_id IS NOT NULL
                            AND length(worker_id) > 0
                            AND lease_expires_at IS NOT NULL
                            AND heartbeat_at IS NOT NULL
                            AND lease_token IS NOT NULL
                        ) OR (
                            status <> 'running'
                            AND worker_id IS NULL
                            AND lease_token IS NULL
                            AND lease_expires_at IS NULL
                            AND heartbeat_at IS NULL
                        )
                    )
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_status_created
                ON tasks(status, created_at, task_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_running_user
                ON tasks(status, user_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_running_cost
                ON tasks(status, cost_class)
                """
            )
            conn.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """在单个写事务中补齐历史 SQLite 队列表缺失的版本和 lease 字段。"""

        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current_version > SCHEMA_VERSION:
                conn.rollback()
                raise RuntimeError(
                    f"任务队列 schema 版本 {current_version} 高于当前程序支持的 {SCHEMA_VERSION}"
                )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "lease_expires_at" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN lease_expires_at TEXT")
            if "heartbeat_at" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN heartbeat_at TEXT")
            if "lease_token" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN lease_token TEXT")
            if "state_version" not in columns:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN state_version INTEGER NOT NULL DEFAULT 1"
                )
            conn.execute(
                """
                UPDATE tasks
                SET heartbeat_at = COALESCE(heartbeat_at, updated_at),
                    lease_expires_at = COALESCE(lease_expires_at, updated_at),
                    lease_token = COALESCE(lease_token, lower(hex(randomblob(16))))
                WHERE status = 'running'
                """
            )
            conn.execute(
                """
                UPDATE tasks
                SET lease_token = NULL,
                    heartbeat_at = NULL,
                    lease_expires_at = NULL
                WHERE status <> 'running'
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_running_lease
                ON tasks(status, lease_expires_at)
                """
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path,
            timeout=max(self._busy_timeout_ms, 1) / 1000,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _fetch_row(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"未知任务：{task_id}")
        return row

    def _row_to_record(self, row: sqlite3.Row) -> TaskRecord:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise TypeError("任务 payload 必须是 JSON 对象")
        return TaskRecord(
            task_id=str(row["task_id"]),
            idempotency_key=str(row["idempotency_key"]),
            channel=str(row["channel"]),
            user_id=str(row["user_id"]),
            task_type=str(row["task_type"]),
            cost_class=str(row["cost_class"]),
            payload=cast(dict[str, object], payload),
            status=cast(TaskStatus, row["status"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            resumable=bool(row["resumable"]),
            worker_id=str(row["worker_id"]) if row["worker_id"] is not None else None,
            lease_token=(
                str(row["lease_token"]) if row["lease_token"] is not None else None
            ),
            lease_expires_at=(
                str(row["lease_expires_at"])
                if row["lease_expires_at"] is not None
                else None
            ),
            heartbeat_at=(
                str(row["heartbeat_at"])
                if row["heartbeat_at"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            safe_error_code=(
                str(row["safe_error_code"])
                if row["safe_error_code"] is not None
                else None
            ),
            state_version=int(row["state_version"]),
        )

    def _serialize_payload(self, payload: Mapping[str, object]) -> str:
        _validate_payload_value(payload)
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise TypeError("payload 必须是 JSON 可序列化对象") from exc

    def _now_utc(self) -> datetime:
        value = self._now_factory()
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _timestamp(self) -> str:
        return self._format_timestamp(self._now_utc())

    def _format_timestamp(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    def _count_running(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS count FROM tasks WHERE status = 'running'"
        ).fetchone()
        return int(row["count"])

    def _cost_limit_sql(
        self,
        cost_limits: Mapping[str, int],
    ) -> tuple[str, tuple[object, ...]]:
        if not cost_limits:
            return "", ()
        cost_classes = tuple(cost_limits)
        placeholders = ", ".join("?" for _ in cost_classes)
        clauses: list[str] = []
        clause_params: list[object] = []
        for cost_class, limit in cost_limits.items():
            clauses.append(
                """
                (
                    q.cost_class = ?
                    AND (
                        SELECT COUNT(*)
                        FROM tasks AS running_cost
                        WHERE running_cost.status = 'running'
                          AND running_cost.cost_class = q.cost_class
                    ) < ?
                )
                """
            )
            clause_params.extend((cost_class, limit))
        sql = f"""
            AND (
                q.cost_class NOT IN ({placeholders})
                OR {' OR '.join(clauses)}
            )
        """
        return sql, (*cost_classes, *clause_params)

    def _ensure_idempotency_identity(
        self,
        row: sqlite3.Row,
        *,
        channel: str,
        user_id: str,
        task_type: str,
        cost_class: str,
    ) -> None:
        expected = {
            "channel": channel,
            "user_id": user_id,
            "task_type": task_type,
            "cost_class": cost_class,
        }
        conflicts = [name for name, value in expected.items() if row[name] != value]
        if conflicts:
            fields = ", ".join(conflicts)
            raise IdempotencyConflictError(
                f"idempotency_key 已绑定不同任务身份；冲突字段：{fields}"
            )

    def _ensure_owned_running(
        self,
        task_id: str,
        row: sqlite3.Row,
        *,
        worker_id: str,
        lease_token: str,
        now: str,
        to_status: TaskStatus,
    ) -> None:
        self._ensure_transition_allowed(
            task_id,
            current_status=row["status"],
            from_statuses={"running"},
            to_status=to_status,
        )
        if row["worker_id"] != worker_id:
            raise TaskOwnershipError(
                f"worker {worker_id} 不是任务 {task_id} 的当前 owner"
            )
        if not lease_token or row["lease_token"] != lease_token:
            raise TaskOwnershipError(
                f"worker {worker_id} 不是任务 {task_id} 的当前 lease token owner"
            )
        lease_expires_at = row["lease_expires_at"]
        if lease_expires_at is None or str(lease_expires_at) <= now:
            raise TaskLeaseExpiredError(f"worker {worker_id} 的任务 lease 已过期")

    def _validate_limits(self, limits: ClaimLimits) -> None:
        if limits.global_limit < 0:
            raise ValueError("global_limit 不能小于 0")
        if limits.per_user_limit < 0:
            raise ValueError("per_user_limit 不能小于 0")
        for cost_class, limit in limits.cost_class_limits.items():
            if limit < 0:
                raise ValueError(f"cost_class_limit 不能小于 0: {cost_class}")

    def _validate_worker_id(self, worker_id: str) -> None:
        if not worker_id.strip() or len(worker_id) > 128:
            raise ValueError("worker_id 必须为 1 到 128 个字符")

    def _validate_lease_duration(self, lease_duration: timedelta) -> None:
        if lease_duration.total_seconds() <= 0:
            raise ValueError("lease_duration 必须大于 0")

    def _ensure_transition_allowed(
        self,
        task_id: str,
        *,
        current_status: str,
        from_statuses: set[TaskStatus],
        to_status: TaskStatus,
    ) -> None:
        if current_status not in from_statuses:
            allowed = ", ".join(sorted(from_statuses))
            raise InvalidTaskTransitionError(
                f"任务 {task_id} 当前状态为 {current_status}，不能迁移到 {to_status}；"
                f"允许来源状态：{allowed}"
            )

    def _notify_transition(self, record: TaskRecord) -> None:
        if self._on_transition is None:
            return
        try:
            self._on_transition(record)
        except Exception:
            return


class PersistentTaskExecutor:
    """异步执行持久任务，并通过 worker lease 防止重复执行。"""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        limits: ClaimLimits,
        worker_id: str | None = None,
        lease_duration: timedelta = timedelta(minutes=1),
        heartbeat_interval: float | None = None,
    ) -> None:
        resolved_worker_id = worker_id or f"executor-{uuid4().hex}"
        repository._validate_limits(limits)
        repository._validate_worker_id(resolved_worker_id)
        repository._validate_lease_duration(lease_duration)
        lease_seconds = lease_duration.total_seconds()
        resolved_heartbeat = (
            max(lease_seconds / 3, 0.001)
            if heartbeat_interval is None
            else heartbeat_interval
        )
        if resolved_heartbeat <= 0:
            raise ValueError("heartbeat_interval 必须大于 0")
        if resolved_heartbeat > lease_seconds / 2:
            raise ValueError("heartbeat_interval 不能超过 lease_duration 的一半")
        self._repository = repository
        self._limits = limits
        self._worker_id = resolved_worker_id
        self._lease_duration = lease_duration
        self._heartbeat_interval = resolved_heartbeat
        self._handlers: dict[str, TaskHandler] = {}

    def register_handler(self, task_type: str, handler: TaskHandler) -> None:
        if not task_type.strip():
            raise ValueError("task_type 不能为空")
        self._handlers[task_type] = handler

    async def run_once(self) -> TaskRecord | None:
        return await self._run_once_for_worker(self._worker_id)

    async def run_forever(
        self,
        *,
        stop_event: asyncio.Event,
        poll_interval: float = 0.25,
        worker_count: int | None = None,
        recovery_interval: float = 5.0,
    ) -> None:
        if poll_interval < 0:
            raise ValueError("poll_interval 不能小于 0")
        if worker_count is not None and worker_count < 1:
            raise ValueError("worker_count 必须大于等于 1")
        if recovery_interval <= 0:
            raise ValueError("recovery_interval 必须大于 0")

        await asyncio.to_thread(self._repository.recover_interrupted)
        requested_workers = (
            self._limits.global_limit if worker_count is None else worker_count
        )
        actual_workers = min(requested_workers, self._limits.global_limit)
        if actual_workers <= 0:
            await stop_event.wait()
            return

        async with asyncio.TaskGroup() as group:
            group.create_task(
                self._recovery_loop(
                    stop_event=stop_event,
                    recovery_interval=recovery_interval,
                )
            )
            for index in range(actual_workers):
                group.create_task(
                    self._worker_loop(
                        worker_id=f"{self._worker_id}:{index}",
                        stop_event=stop_event,
                        poll_interval=poll_interval,
                    )
                )

    async def _recovery_loop(
        self,
        *,
        stop_event: asyncio.Event,
        recovery_interval: float,
    ) -> None:
        while not stop_event.is_set():
            await asyncio.to_thread(self._repository.recover_interrupted)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=recovery_interval)
            except TimeoutError:
                pass

    async def _worker_loop(
        self,
        *,
        worker_id: str,
        stop_event: asyncio.Event,
        poll_interval: float,
    ) -> None:
        while not stop_event.is_set():
            result = await self._run_once_for_worker(worker_id)
            if result is not None:
                continue
            if poll_interval == 0:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            except TimeoutError:
                pass

    async def _run_once_for_worker(self, worker_id: str) -> TaskRecord | None:
        claim_task = asyncio.create_task(
            asyncio.to_thread(
                self._repository.claim_next,
                self._limits,
                worker_id=worker_id,
                lease_duration=self._lease_duration,
            )
        )
        try:
            task = await asyncio.shield(claim_task)
        except asyncio.CancelledError:
            settled, claimed, _ = await _settle_task_despite_cancellation(claim_task)
            if settled and claimed is not None:
                await self._release_after_cancellation(claimed, worker_id=worker_id)
            raise
        if task is None:
            return None

        handler = self._handlers.get(task.task_type)
        if handler is None:
            return await asyncio.to_thread(
                self._repository.fail,
                task.task_id,
                worker_id=worker_id,
                lease_token=task.lease_token,
                safe_error_code="unsupported_task_type",
                retryable=False,
            )

        guard_task = asyncio.create_task(
            asyncio.to_thread(
                self._repository.acquire_execution_guard,
                task.task_id,
                blocking=False,
            )
        )
        try:
            execution_guard = await asyncio.shield(guard_task)
        except asyncio.CancelledError:
            settled, acquired_guard, _ = await _settle_task_despite_cancellation(
                guard_task
            )
            if settled and isinstance(acquired_guard, TaskExecutionGuard):
                acquired_guard.release()
            await self._release_after_cancellation(task, worker_id=worker_id)
            raise
        if execution_guard is None:
            # 旧执行仍持有跨进程锁时，绝不能启动第二个 handler。
            return task
        ownership_lost = False
        try:
            validation_task = asyncio.create_task(
                asyncio.to_thread(
                    self._repository.validate_claim,
                    task.task_id,
                    worker_id=worker_id,
                    lease_token=task.lease_token or "",
                )
            )
            try:
                task = await asyncio.shield(validation_task)
            except asyncio.CancelledError:
                await _settle_task_despite_cancellation(validation_task)
                await self._release_after_cancellation(task, worker_id=worker_id)
                raise
            return await self._run_claimed_handler(handler, task, worker_id)
        except TaskOwnershipError:
            ownership_lost = True
        finally:
            execution_guard.release()
        if ownership_lost:
            await asyncio.to_thread(self._repository.recover_interrupted)
            return await asyncio.to_thread(self._repository.get_task, task.task_id)
        raise RuntimeError("任务执行在未产生结果的情况下结束")

    async def _run_claimed_handler(
        self,
        handler: TaskHandler,
        task: TaskRecord,
        worker_id: str,
    ) -> TaskRecord:
        finalized = False
        try:
            outcome, deferred_cancellation = await self._execute_with_heartbeat(
                handler, task, worker_id
            )
            if outcome is None or outcome.status == "completed":
                result = await asyncio.to_thread(
                    self._repository.complete,
                    task.task_id,
                    worker_id=worker_id,
                    lease_token=task.lease_token,
                )
            elif outcome.status == "needs_input":
                result = await asyncio.to_thread(
                    self._repository.mark_needs_input,
                    task.task_id,
                    worker_id=worker_id,
                    lease_token=task.lease_token,
                    safe_error_code=outcome.safe_error_code,
                )
            else:
                raise ValueError(f"不支持的 handler 结果状态：{outcome.status}")
            finalized = True
            if deferred_cancellation:
                raise asyncio.CancelledError
            return result
        except asyncio.CancelledError:
            if not finalized:
                await self._release_after_cancellation(task, worker_id=worker_id)
            raise
        except SafeTaskError as exc:
            return await asyncio.to_thread(
                self._repository.fail,
                task.task_id,
                worker_id=worker_id,
                lease_token=task.lease_token,
                safe_error_code=exc.safe_error_code,
                retryable=exc.retryable,
            )
        except Exception:
            return await asyncio.to_thread(
                self._repository.fail,
                task.task_id,
                worker_id=worker_id,
                lease_token=task.lease_token,
                safe_error_code="internal_error",
                retryable=False,
            )

    async def _execute_with_heartbeat(
        self,
        handler: TaskHandler,
        task: TaskRecord,
        worker_id: str,
    ) -> tuple[TaskHandlerResult | None, bool]:
        is_async_handler = _is_async_callable(handler)
        handler_task = asyncio.create_task(self._invoke_handler(handler, task))
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(task.task_id, worker_id, task.lease_token or "")
        )
        try:
            try:
                done, _ = await asyncio.wait(
                    {handler_task, heartbeat_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                if is_async_handler:
                    raise
                settled, outcome_or_error, _ = await _settle_task_despite_cancellation(
                    handler_task
                )
                if not settled:
                    raise asyncio.CancelledError from None
                return cast(TaskHandlerResult | None, outcome_or_error), True
            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                if not is_async_handler:
                    settled, _, cancellation_requested = (
                        await _settle_task_despite_cancellation(handler_task)
                    )
                    if cancellation_requested:
                        raise asyncio.CancelledError
                    if not settled:
                        # 同步线程已结束后才允许释放 lease；handler 异常由统一失败分类处理。
                        if heartbeat_error is not None:
                            raise heartbeat_error
                        raise RuntimeError("heartbeat 意外停止")
                if heartbeat_error is not None:
                    raise heartbeat_error
                raise RuntimeError("heartbeat 意外停止")
            return await handler_task, False
        finally:
            if not handler_task.done():
                handler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await handler_task
            if not heartbeat_task.done():
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task

    async def _release_after_cancellation(
        self,
        task: TaskRecord,
        *,
        worker_id: str,
    ) -> None:
        release_task = asyncio.create_task(
            asyncio.to_thread(
                self._repository.release_cancelled,
                task.task_id,
                worker_id=worker_id,
                lease_token=task.lease_token or "",
            )
        )
        with suppress(Exception):
            await _settle_task_despite_cancellation(release_task)

    async def _heartbeat_loop(
        self, task_id: str, worker_id: str, lease_token: str
    ) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            await asyncio.to_thread(
                self._repository.renew_lease,
                task_id,
                worker_id=worker_id,
                lease_token=lease_token,
                lease_duration=self._lease_duration,
            )

    async def _invoke_handler(
        self,
        handler: TaskHandler,
        task: TaskRecord,
    ) -> TaskHandlerResult | None:
        if _is_async_callable(handler):
            outcome = handler(task)
        else:
            outcome = await asyncio.to_thread(handler, task)
        if inspect.isawaitable(outcome):
            return await outcome
        return outcome


def _validate_safe_error_code(
    safe_error_code: str | None,
    *,
    optional: bool = False,
) -> None:
    if safe_error_code is None:
        if optional:
            return
        raise InvalidSafeErrorCodeError("safe_error_code 不能为空")
    if not _SAFE_ERROR_CODE_PATTERN.fullmatch(safe_error_code):
        raise InvalidSafeErrorCodeError(
            "safe_error_code 仅允许小写字母、数字、下划线，且长度为 1 到 64"
        )
    if any(fragment in safe_error_code for fragment in _SENSITIVE_ERROR_FRAGMENTS):
        raise InvalidSafeErrorCodeError("safe_error_code 包含禁止的敏感分类片段")


def _validate_payload_value(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError("payload 的字段名必须是字符串")
            compact_key = re.sub(r"[^a-z0-9]", "", key.lower())
            if any(
                fragment in compact_key
                for fragment in _SENSITIVE_PAYLOAD_KEY_FRAGMENTS
            ):
                raise ValueError("payload 包含不允许持久化的敏感字段名")
            _validate_payload_value(nested)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _validate_payload_value(nested)
        return
    if isinstance(value, str) and _SENSITIVE_PAYLOAD_VALUE_PATTERN.search(value):
        raise ValueError("payload 包含不允许持久化的敏感凭据值")


def _is_async_callable(handler: TaskHandler) -> bool:
    if inspect.iscoroutinefunction(handler):
        return True
    call = getattr(handler, "__call__", None)
    return inspect.iscoroutinefunction(call)


async def _settle_task_despite_cancellation(
    task: asyncio.Task,
) -> tuple[bool, object, bool]:
    """等待不可中断的线程包装任务收敛，并记录期间收到的全部取消信号。"""

    cancellation_requested = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancellation_requested = True
        except Exception:
            break
    if task.cancelled():
        return False, asyncio.CancelledError(), cancellation_requested
    error = task.exception()
    if error is not None:
        return False, error, cancellation_requested
    return True, task.result(), cancellation_requested


__all__ = [
    "ClaimLimits",
    "IdempotencyConflictError",
    "InvalidSafeErrorCodeError",
    "InvalidTaskTransitionError",
    "PersistentTaskExecutor",
    "RecoverySummary",
    "SafeTaskError",
    "TaskHandlerResult",
    "TaskLifecycleObserver",
    "TaskLeaseExpiredError",
    "TaskOwnershipError",
    "TaskRecord",
    "TaskRepository",
    "build_idempotency_key",
]
