from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

from pydantic import BaseModel, Field

from app.platform.intent import ConversationIntent, classify_conversation_intent
from app.platform.router import URL_RE
from app.platform.skill_ids import canonical_skill_id


class TaskRelation(str, Enum):
    CONTINUE = "continue"
    ADD_MATERIAL = "add_material"
    DERIVE = "derive"
    NEW_TASK = "new_task"
    ANSWER_CLARIFICATION = "answer_clarification"
    SWITCH = "switch"
    CANCEL = "cancel"
    NEEDS_CLARIFICATION = "needs_clarification"


class MaterialRole(str, Enum):
    NONE = "none"
    SUPPLEMENT = "supplement"
    REPLACE = "replace"
    REFERENCE = "reference"
    NEW_TASK = "new_task"


class RelationAction(str, Enum):
    EXECUTE = "execute"
    ASK = "ask"
    SELECT = "select"
    CANCEL = "cancel"
    NOOP = "noop"


class TaskCardStatus(str, Enum):
    ASSEMBLING = "assembling"
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_INPUT = "needs_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskMaterial:
    kind: str
    label: str
    role: MaterialRole
    source_job_id: str = ""


@dataclass(frozen=True)
class TaskCard:
    task_id: str
    channel: str
    user_id: str
    display_order: int
    title: str
    skill_id: str
    status: TaskCardStatus
    current_job_id: str
    current_version: int
    content_summary: str
    parent_task_id: str
    pending_question: str
    resume_context: dict[str, object]
    execution_task_id: str
    created_at: str
    updated_at: str
    selected_at: str
    materials: tuple[TaskMaterial, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PendingRelationDecision:
    channel: str
    user_id: str
    original_text: str
    candidate_task_ids: tuple[str, ...]
    route_skill_id: str
    has_new_material: bool
    question: str
    created_at: str


@dataclass(frozen=True)
class TaskRelationDecision:
    relation: TaskRelation
    target_task_id: str = ""
    material_role: MaterialRole = MaterialRole.NONE
    suggested_skill_id: str = ""
    action: RelationAction = RelationAction.NOOP
    confidence: float = 1.0
    reason: str = ""
    question: str = ""
    effective_text: str = ""
    parent_task_id: str = ""

    @property
    def needs_clarification(self) -> bool:
        return self.action is RelationAction.ASK


SemanticRelationClassifier = Callable[..., Mapping[str, object] | TaskRelationDecision]
StructuredRelationRunner = Callable[..., Mapping[str, object]]


class SemanticTaskRelationOutput(BaseModel):
    relation: TaskRelation
    target_task_id: str = ""
    material_role: MaterialRole = MaterialRole.NONE
    suggested_skill_id: str = ""
    action: RelationAction
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    question: str = ""
    parent_task_id: str = ""


class PydanticTaskRelationClassifier:
    """只向模型提供用户自己的任务摘要，并要求返回受约束的关系判断。"""

    def __init__(self, runner: StructuredRelationRunner) -> None:
        self._runner = runner

    def __call__(
        self,
        *,
        text: str,
        tasks: Sequence[Mapping[str, object]],
        route_skill_id: str,
        has_new_material: bool,
        selected_task_id: str = "",
    ) -> Mapping[str, object]:
        candidates = [
            {
                "task_id": str(item.get("task_id", ""))[:120],
                "title": str(item.get("title", ""))[:160],
                "skill_id": str(item.get("skill_id", ""))[:80],
                "status": str(item.get("status", ""))[:40],
                "content_summary": str(item.get("content_summary", ""))[:240],
                "pending_question": str(item.get("pending_question", ""))[:240],
                "is_selected": str(item.get("task_id", "")) == selected_task_id,
            }
            for item in list(tasks)[:8]
        ]
        instructions = """你是 M-Agent 的任务关系分类器，不负责执行任务。
判断用户本条消息与候选任务的关系，并严格返回结构化结果。

允许的关系：continue、add_material、derive、new_task、answer_clarification、switch、cancel、needs_clarification。
材料角色：none、supplement、replace、reference、new_task。
执行动作：execute、ask、select、cancel、noop。

约束：
1. 只能从候选任务中选择 target_task_id，不能创造或猜测任务编号。
2. 目标不唯一、证据冲突或置信度不足时，返回 needs_clarification + ask，并只提出一个区分问题。
3. 明确的新任务可以不带 target_task_id；其他 execute/select/cancel 必须带目标任务。
4. 上传了材料不等于新任务，要结合用户表述判断 supplement、replace、reference 或 new_task。
5. 不做权限判断，不执行工具，不读取文件，不根据候选之外的信息推断。
6. `is_selected=true` 表示用户当前正在处理的稿件。“继续改、再改、接着改、这篇、这个直报/简报”等承接表达默认指向它；只有标题、任务序号、文种或主题明确指向其他候选时才覆盖。
7. confidence 必须反映证据强度，不要为了减少追问而虚高。"""
        prompt = _json_dumps(
            {
                "user_message": text[:2000],
                "route_skill_id": route_skill_id[:80],
                "has_new_material": bool(has_new_material),
                "candidate_tasks": candidates,
            }
        )
        return self._runner(
            instructions=instructions,
            prompt=prompt,
            output_type=SemanticTaskRelationOutput,
        )


class TaskRelationRepository:
    """按入口和企业微信 userid 隔离的公共任务关系仓库。"""

    def __init__(self, db_path: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        self._db_path = Path(db_path)
        self._busy_timeout_ms = max(0, int(busy_timeout_ms))
        self._db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self._db_path.parent, 0o700)
        self._initialize()
        os.chmod(self._db_path, 0o600)

    def create_task(
        self,
        *,
        task_id: str,
        channel: str,
        user_id: str,
        skill_id: str,
        title: str,
        status: TaskCardStatus,
        current_job_id: str,
        parent_task_id: str = "",
        materials: Iterable[tuple[str, str, MaterialRole]] = (),
        execution_task_id: str = "",
    ) -> TaskCard:
        _require_identity(task_id=task_id, channel=channel, user_id=user_id)
        skill_id = canonical_skill_id(skill_id) or skill_id
        timestamp = _timestamp()
        clean_title = _safe_title(title, skill_id=skill_id)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM task_cards WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if existing is not None:
                _ensure_owner(existing, channel=channel, user_id=user_id)
                conn.commit()
                return self._row_to_card(conn, existing)
            display_order = int(
                conn.execute(
                    "SELECT COALESCE(MAX(display_order), 0) + 1 FROM task_cards WHERE channel = ? AND user_id = ?",
                    (channel, user_id),
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO task_cards (
                    task_id, channel, user_id, display_order, title, skill_id, status,
                    current_job_id, current_version, content_summary, parent_task_id,
                    pending_question, resume_context_json, execution_task_id,
                    created_at, updated_at, selected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, '', '{}', ?, ?, ?, ?)
                """,
                (
                    task_id,
                    channel,
                    user_id,
                    display_order,
                    clean_title,
                    skill_id,
                    status.value,
                    current_job_id,
                    1 if current_job_id else 0,
                    parent_task_id,
                    execution_task_id,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            if current_job_id:
                conn.execute(
                    "INSERT INTO task_versions (task_id, version, job_id, relation, created_at) VALUES (?, 1, ?, ?, ?)",
                    (task_id, current_job_id, TaskRelation.NEW_TASK.value, timestamp),
                )
            self._insert_materials(conn, task_id, current_job_id, materials)
            row = self._fetch_card_row(conn, task_id)
            conn.commit()
        return self._row_to_card_from_db(row)

    def bind_job(
        self,
        *,
        task_id: str,
        job_id: str,
        relation: TaskRelation,
        status: TaskCardStatus,
        execution_task_id: str = "",
        materials: Iterable[tuple[str, str, MaterialRole]] = (),
    ) -> TaskCard:
        if not job_id.strip():
            raise ValueError("job_id 不能为空")
        timestamp = _timestamp()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetch_card_row(conn, task_id)
            existing_version = conn.execute(
                "SELECT version FROM task_versions WHERE task_id = ? AND job_id = ?",
                (task_id, job_id),
            ).fetchone()
            if existing_version is None:
                version = int(row["current_version"]) + 1
                conn.execute(
                    "INSERT INTO task_versions (task_id, version, job_id, relation, created_at) VALUES (?, ?, ?, ?, ?)",
                    (task_id, version, job_id, relation.value, timestamp),
                )
            else:
                version = int(existing_version["version"])
            conn.execute(
                """
                UPDATE task_cards
                SET current_job_id = ?, current_version = ?, status = ?,
                    execution_task_id = CASE WHEN ? = '' THEN execution_task_id ELSE ? END,
                    pending_question = '', resume_context_json = '{}', updated_at = ?, selected_at = ?
                WHERE task_id = ?
                """,
                (
                    job_id,
                    version,
                    status.value,
                    execution_task_id,
                    execution_task_id,
                    timestamp,
                    timestamp,
                    task_id,
                ),
            )
            self._insert_materials(conn, task_id, job_id, materials)
            updated = self._fetch_card_row(conn, task_id)
            conn.commit()
        return self._row_to_card_from_db(updated)

    def record_result(
        self,
        *,
        task_id: str,
        job_id: str,
        title: str,
        body: str,
        status: TaskCardStatus,
        pending_question: str = "",
        resume_context: Mapping[str, object] | None = None,
    ) -> TaskCard:
        timestamp = _timestamp()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetch_card_row(conn, task_id)
            version_row = conn.execute(
                "SELECT version FROM task_versions WHERE task_id = ? AND job_id = ?",
                (task_id, job_id),
            ).fetchone()
            if version_row is None:
                version = int(row["current_version"]) + 1
                conn.execute(
                    "INSERT INTO task_versions (task_id, version, job_id, relation, created_at) VALUES (?, ?, ?, ?, ?)",
                    (task_id, version, job_id, TaskRelation.CONTINUE.value, timestamp),
                )
            else:
                version = int(version_row["version"])
            effective_title = _safe_title(title, skill_id=str(row["skill_id"]))
            if not title.strip():
                effective_title = str(row["title"])
            conn.execute(
                """
                UPDATE task_cards
                SET title = ?, status = ?, current_job_id = ?, current_version = ?,
                    content_summary = ?, pending_question = ?, resume_context_json = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    effective_title,
                    status.value,
                    job_id,
                    version,
                    _content_summary(title=effective_title, body=body),
                    pending_question.strip(),
                    _json_dumps(dict(resume_context or {})),
                    timestamp,
                    task_id,
                ),
            )
            updated = self._fetch_card_row(conn, task_id)
            conn.commit()
        return self._row_to_card_from_db(updated)

    def set_pending_question(
        self,
        *,
        task_id: str,
        question: str,
        resume_context: Mapping[str, object],
    ) -> TaskCard:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._fetch_card_row(conn, task_id)
            timestamp = _timestamp()
            conn.execute(
                """
                UPDATE task_cards
                SET status = ?, pending_question = ?, resume_context_json = ?,
                    updated_at = ?, selected_at = ?
                WHERE task_id = ?
                """,
                (
                    TaskCardStatus.NEEDS_INPUT.value,
                    question.strip(),
                    _json_dumps(dict(resume_context)),
                    timestamp,
                    timestamp,
                    task_id,
                ),
            )
            row = self._fetch_card_row(conn, task_id)
            conn.commit()
        return self._row_to_card_from_db(row)

    def set_status(
        self,
        task_id: str,
        status: TaskCardStatus,
        *,
        execution_task_id: str = "",
    ) -> TaskCard:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._fetch_card_row(conn, task_id)
            timestamp = _timestamp()
            conn.execute(
                """
                UPDATE task_cards
                SET status = ?, execution_task_id = CASE WHEN ? = '' THEN execution_task_id ELSE ? END,
                    updated_at = ?
                WHERE task_id = ?
                """,
                (status.value, execution_task_id, execution_task_id, timestamp, task_id),
            )
            row = self._fetch_card_row(conn, task_id)
            conn.commit()
        return self._row_to_card_from_db(row)

    def select_task(self, *, task_id: str, channel: str, user_id: str) -> TaskCard:
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._fetch_card_row(conn, task_id)
            _ensure_owner(row, channel=channel, user_id=user_id)
            timestamp = _timestamp()
            conn.execute(
                "UPDATE task_cards SET selected_at = ?, updated_at = ? WHERE task_id = ?",
                (timestamp, timestamp, task_id),
            )
            updated = self._fetch_card_row(conn, task_id)
            conn.commit()
        return self._row_to_card_from_db(updated)

    def selected_task(self, *, channel: str, user_id: str) -> TaskCard | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT * FROM task_cards
                WHERE channel = ? AND user_id = ? AND status <> ?
                ORDER BY selected_at DESC, updated_at DESC, display_order DESC
                LIMIT 1
                """,
                (channel, user_id, TaskCardStatus.CANCELLED.value),
            ).fetchone()
            return self._row_to_card(conn, row) if row is not None else None

    def get_task(self, task_id: str, *, channel: str = "", user_id: str = "") -> TaskCard:
        with closing(self._connect()) as conn:
            row = self._fetch_card_row(conn, task_id)
            if channel or user_id:
                _ensure_owner(row, channel=channel, user_id=user_id)
            return self._row_to_card(conn, row)

    def list_tasks(
        self,
        *,
        channel: str,
        user_id: str,
        limit: int = 8,
        include_cancelled: bool = False,
    ) -> list[TaskCard]:
        if limit < 1:
            return []
        clause = "" if include_cancelled else "AND status <> ?"
        params: list[object] = [channel, user_id]
        if not include_cancelled:
            params.append(TaskCardStatus.CANCELLED.value)
        params.append(min(limit, 50))
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM task_cards
                WHERE channel = ? AND user_id = ? {clause}
                ORDER BY updated_at DESC, display_order DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [self._row_to_card(conn, row) for row in rows]

    def version_job_id(self, task_id: str, version: int | None = None) -> str:
        with closing(self._connect()) as conn:
            if version is None:
                row = conn.execute(
                    "SELECT job_id FROM task_versions WHERE task_id = ? ORDER BY version DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT job_id FROM task_versions WHERE task_id = ? AND version = ?",
                    (task_id, version),
                ).fetchone()
        return str(row["job_id"]) if row is not None else ""

    def save_pending_decision(
        self,
        *,
        channel: str,
        user_id: str,
        original_text: str,
        candidate_task_ids: Sequence[str],
        route_skill_id: str,
        has_new_material: bool,
        question: str,
    ) -> None:
        timestamp = _timestamp()
        route_skill_id = canonical_skill_id(route_skill_id) or route_skill_id
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO pending_relations (
                    channel, user_id, original_text, candidate_task_ids_json,
                    route_skill_id, has_new_material, question, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, user_id) DO UPDATE SET
                    original_text = excluded.original_text,
                    candidate_task_ids_json = excluded.candidate_task_ids_json,
                    route_skill_id = excluded.route_skill_id,
                    has_new_material = excluded.has_new_material,
                    question = excluded.question,
                    created_at = excluded.created_at
                """,
                (
                    channel,
                    user_id,
                    original_text.strip(),
                    _json_dumps(list(candidate_task_ids)),
                    route_skill_id,
                    1 if has_new_material else 0,
                    question.strip(),
                    timestamp,
                ),
            )
            conn.commit()

    def pending_decision(self, *, channel: str, user_id: str) -> PendingRelationDecision | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM pending_relations WHERE channel = ? AND user_id = ?",
                (channel, user_id),
            ).fetchone()
        if row is None:
            return None
        raw_candidates = _json_loads(str(row["candidate_task_ids_json"]), default=[])
        return PendingRelationDecision(
            channel=str(row["channel"]),
            user_id=str(row["user_id"]),
            original_text=str(row["original_text"]),
            candidate_task_ids=tuple(str(item) for item in raw_candidates if str(item).strip()),
            route_skill_id=canonical_skill_id(str(row["route_skill_id"])) or "",
            has_new_material=bool(row["has_new_material"]),
            question=str(row["question"]),
            created_at=str(row["created_at"]),
        )

    def clear_pending_decision(self, *, channel: str, user_id: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "DELETE FROM pending_relations WHERE channel = ? AND user_id = ?",
                (channel, user_id),
            )
            conn.commit()

    def record_decision(
        self,
        *,
        channel: str,
        user_id: str,
        decision: TaskRelationDecision,
        source: str,
        pending_recovery: bool = False,
        user_correction: bool = False,
    ) -> None:
        """记录可聚合的判断指标，不保存用户消息正文或材料内容。"""
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO relation_decisions (
                    channel, user_id, relation, target_task_id, action, source,
                    confidence, needs_clarification, pending_recovery,
                    user_correction, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel,
                    user_id,
                    decision.relation.value,
                    decision.target_task_id,
                    decision.action.value,
                    source[:40],
                    decision.confidence,
                    1 if decision.needs_clarification else 0,
                    1 if pending_recovery else 0,
                    1 if user_correction else 0,
                    _timestamp(),
                ),
            )
            conn.commit()

    def relation_metrics(self, *, channel: str, user_id: str = "") -> dict[str, int]:
        where = "channel = ?"
        params: list[object] = [channel]
        if user_id:
            where += " AND user_id = ?"
            params.append(user_id)
        with closing(self._connect()) as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(needs_clarification), 0) AS clarifications,
                    COALESCE(SUM(pending_recovery), 0) AS pending_recoveries,
                    COALESCE(SUM(user_correction), 0) AS user_corrections,
                    COALESCE(SUM(CASE WHEN source = 'semantic' THEN 1 ELSE 0 END), 0)
                        AS semantic_decisions
                FROM relation_decisions
                WHERE {where}
                """,
                params,
            ).fetchone()
        return {
            "total": int(row["total"]),
            "clarifications": int(row["clarifications"]),
            "pending_recoveries": int(row["pending_recoveries"]),
            "user_corrections": int(row["user_corrections"]),
            "semantic_decisions": int(row["semantic_decisions"]),
        }

    def _initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_cards (
                    task_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    display_order INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_job_id TEXT NOT NULL,
                    current_version INTEGER NOT NULL,
                    content_summary TEXT NOT NULL,
                    parent_task_id TEXT NOT NULL,
                    pending_question TEXT NOT NULL,
                    resume_context_json TEXT NOT NULL,
                    execution_task_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    selected_at TEXT NOT NULL,
                    UNIQUE(channel, user_id, display_order)
                );
                CREATE INDEX IF NOT EXISTS idx_task_cards_user_updated
                ON task_cards(channel, user_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS task_versions (
                    task_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    job_id TEXT NOT NULL UNIQUE,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, version),
                    FOREIGN KEY(task_id) REFERENCES task_cards(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS task_materials (
                    material_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    source_job_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(task_id, source_job_id, kind, label, role),
                    FOREIGN KEY(task_id) REFERENCES task_cards(task_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS pending_relations (
                    channel TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    original_text TEXT NOT NULL,
                    candidate_task_ids_json TEXT NOT NULL,
                    route_skill_id TEXT NOT NULL,
                    has_new_material INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(channel, user_id)
                );

                CREATE TABLE IF NOT EXISTS relation_decisions (
                    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    target_task_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    needs_clarification INTEGER NOT NULL,
                    pending_recovery INTEGER NOT NULL,
                    user_correction INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_relation_decisions_scope_time
                ON relation_decisions(channel, user_id, created_at DESC);
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path,
            timeout=max(self._busy_timeout_ms, 1) / 1000,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _fetch_card_row(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM task_cards WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"未知任务卡片：{task_id}")
        return row

    @staticmethod
    def _insert_materials(
        conn: sqlite3.Connection,
        task_id: str,
        source_job_id: str,
        materials: Iterable[tuple[str, str, MaterialRole]],
    ) -> None:
        timestamp = _timestamp()
        for kind, label, role in materials:
            clean_kind = str(kind).strip()
            clean_label = str(label).strip()
            if not clean_kind or not clean_label:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO task_materials (
                    task_id, source_job_id, kind, label, role, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, source_job_id, clean_kind, clean_label[:500], role.value, timestamp),
            )

    def _row_to_card(self, conn: sqlite3.Connection, row: sqlite3.Row) -> TaskCard:
        material_rows = conn.execute(
            "SELECT * FROM task_materials WHERE task_id = ? ORDER BY material_id",
            (str(row["task_id"]),),
        ).fetchall()
        return self._build_card(row, material_rows)

    def _row_to_card_from_db(self, row: sqlite3.Row) -> TaskCard:
        with closing(self._connect()) as conn:
            return self._row_to_card(conn, row)

    @staticmethod
    def _build_card(row: sqlite3.Row, material_rows: Sequence[sqlite3.Row]) -> TaskCard:
        context = _json_loads(str(row["resume_context_json"]), default={})
        if not isinstance(context, dict):
            context = {}
        if context.get("skill_id"):
            context["skill_id"] = canonical_skill_id(str(context["skill_id"]))
        return TaskCard(
            task_id=str(row["task_id"]),
            channel=str(row["channel"]),
            user_id=str(row["user_id"]),
            display_order=int(row["display_order"]),
            title=str(row["title"]),
            skill_id=canonical_skill_id(str(row["skill_id"])) or "",
            status=TaskCardStatus(str(row["status"])),
            current_job_id=str(row["current_job_id"]),
            current_version=int(row["current_version"]),
            content_summary=str(row["content_summary"]),
            parent_task_id=str(row["parent_task_id"]),
            pending_question=str(row["pending_question"]),
            resume_context=context,
            execution_task_id=str(row["execution_task_id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            selected_at=str(row["selected_at"]),
            materials=tuple(
                TaskMaterial(
                    kind=str(item["kind"]),
                    label=str(item["label"]),
                    role=MaterialRole(str(item["role"])),
                    source_job_id=str(item["source_job_id"]),
                )
                for item in material_rows
            ),
        )


class TaskRelationService:
    """先用硬规则处理明确指令，模糊场景才交给可选语义分类器。"""

    def __init__(
        self,
        repository: TaskRelationRepository,
        *,
        semantic_classifier: SemanticRelationClassifier | None = None,
        automatic_confidence: float = 0.78,
    ) -> None:
        self.repository = repository
        self._semantic_classifier = semantic_classifier
        self._automatic_confidence = min(1.0, max(0.0, automatic_confidence))

    def resolve_text(
        self,
        *,
        channel: str,
        user_id: str,
        text: str,
        route_skill_id: str | None,
        has_new_material: bool = False,
        persist: bool = True,
    ) -> TaskRelationDecision:
        normalized = text.strip()
        cards = self.repository.list_tasks(channel=channel, user_id=user_id, limit=8)
        pending = self.repository.pending_decision(channel=channel, user_id=user_id)

        if pending is not None:
            decision = self._resolve_pending_answer(
                pending=pending,
                cards=cards,
                answer=normalized,
                route_skill_id=route_skill_id or "",
            )
            if decision is not None:
                if persist:
                    self.repository.clear_pending_decision(channel=channel, user_id=user_id)
                    if decision.action is RelationAction.SELECT and decision.target_task_id:
                        self.repository.select_task(
                            task_id=decision.target_task_id,
                            channel=channel,
                            user_id=user_id,
                        )
                    self.repository.record_decision(
                        channel=channel,
                        user_id=user_id,
                        decision=decision,
                        source="pending_answer",
                        pending_recovery=True,
                        user_correction=decision.relation is TaskRelation.NEW_TASK,
                    )
                return decision

        pending_cards = [card for card in cards if card.pending_question]
        if pending_cards and not _looks_like_explicit_new_task(normalized):
            target = _select_target(normalized, pending_cards, selected=None)
            if target is None and len(pending_cards) == 1:
                target = pending_cards[0]
            if target is not None:
                if persist:
                    self.repository.select_task(task_id=target.task_id, channel=channel, user_id=user_id)
                decision = TaskRelationDecision(
                    relation=TaskRelation.ANSWER_CLARIFICATION,
                    target_task_id=target.task_id,
                    suggested_skill_id=target.skill_id,
                    action=RelationAction.EXECUTE,
                    confidence=1.0,
                    reason="回答任务待确认问题",
                    effective_text=normalized,
                )
                if persist:
                    self.repository.record_decision(
                        channel=channel,
                        user_id=user_id,
                        decision=decision,
                        source="pending_question",
                        pending_recovery=True,
                    )
                return decision

        selected = self.repository.selected_task(channel=channel, user_id=user_id)
        decision = self._resolve_explicit(
            text=normalized,
            cards=cards,
            selected=selected,
            route_skill_id=route_skill_id or "",
            has_new_material=has_new_material,
        )
        source = "rule"
        if decision.relation is TaskRelation.NEEDS_CLARIFICATION and self._semantic_classifier:
            semantic = self._semantic_decision(
                text=normalized,
                cards=cards,
                selected=selected,
                route_skill_id=route_skill_id or "",
                has_new_material=has_new_material,
            )
            if semantic is not None:
                decision = semantic
                source = "semantic"

        decision = _guard_unfinished_target(decision, cards)

        if persist:
            if decision.action is RelationAction.ASK:
                self.repository.save_pending_decision(
                    channel=channel,
                    user_id=user_id,
                    original_text=normalized,
                    candidate_task_ids=(
                        [decision.target_task_id]
                        if decision.target_task_id
                        else [card.task_id for card in cards]
                    ),
                    route_skill_id=route_skill_id or "",
                    has_new_material=has_new_material,
                    question=decision.question,
                )
            elif decision.action is RelationAction.SELECT and decision.target_task_id:
                self.repository.select_task(
                    task_id=decision.target_task_id,
                    channel=channel,
                    user_id=user_id,
                )
            self.repository.record_decision(
                channel=channel,
                user_id=user_id,
                decision=decision,
                source=source,
            )
        return decision

    def _resolve_explicit(
        self,
        *,
        text: str,
        cards: list[TaskCard],
        selected: TaskCard | None,
        route_skill_id: str,
        has_new_material: bool,
    ) -> TaskRelationDecision:
        if not text:
            return _ask_decision(cards, reason="消息为空")

        if _looks_like_list_request(text):
            return TaskRelationDecision(
                relation=TaskRelation.NEEDS_CLARIFICATION,
                action=RelationAction.ASK,
                confidence=1.0,
                reason="用户请求查看任务",
                question=_task_list_message(cards),
                effective_text=text,
            )

        if _looks_like_explicit_new_task(text):
            if any(marker in text for marker in _DERIVE_MARKERS):
                target = _select_target(text, cards, selected=selected)
                if target is not None:
                    return TaskRelationDecision(
                        relation=TaskRelation.DERIVE,
                        target_task_id=target.task_id,
                        parent_task_id=target.task_id,
                        material_role=MaterialRole.NEW_TASK,
                        suggested_skill_id=route_skill_id or target.skill_id,
                        action=RelationAction.EXECUTE,
                        confidence=0.98,
                        reason="用户明确要求沿用旧任务派生新稿",
                        effective_text=text,
                    )
            return TaskRelationDecision(
                relation=TaskRelation.NEW_TASK,
                material_role=MaterialRole.NEW_TASK if has_new_material else MaterialRole.NONE,
                suggested_skill_id=route_skill_id,
                action=RelationAction.EXECUTE,
                confidence=1.0,
                reason="用户明确要求新建独立任务",
                effective_text=text,
            )

        if _looks_like_cancel(text):
            target = _select_target(text, cards, selected=selected)
            if target is None:
                return _ask_decision(cards, reason="取消指令缺少唯一目标", effective_text=text)
            return TaskRelationDecision(
                relation=TaskRelation.CANCEL,
                target_task_id=target.task_id,
                suggested_skill_id=target.skill_id,
                action=RelationAction.CANCEL,
                confidence=1.0,
                reason="用户明确取消任务",
                effective_text=text,
            )

        if _looks_like_switch(text):
            target = _select_target(text, cards, selected=selected)
            if target is None:
                return _ask_decision(cards, reason="切换指令缺少唯一目标", effective_text=text)
            return TaskRelationDecision(
                relation=TaskRelation.SWITCH,
                target_task_id=target.task_id,
                suggested_skill_id=target.skill_id,
                action=RelationAction.SELECT,
                confidence=1.0,
                reason="用户明确切换任务",
                effective_text=text,
            )

        if has_new_material and _looks_like_material_relation(text):
            target = _select_target(text, cards, selected=selected)
            if target is None:
                return _ask_decision(cards, reason="补充材料缺少唯一目标", effective_text=text)
            role = _material_role(text)
            return TaskRelationDecision(
                relation=TaskRelation.ADD_MATERIAL,
                target_task_id=target.task_id,
                material_role=role,
                suggested_skill_id=target.skill_id,
                action=RelationAction.EXECUTE,
                confidence=0.98,
                reason="用户明确指定新材料与旧任务的关系",
                effective_text=text,
            )

        if not cards:
            return TaskRelationDecision(
                relation=TaskRelation.NEW_TASK,
                material_role=MaterialRole.NEW_TASK if has_new_material else MaterialRole.NONE,
                suggested_skill_id=route_skill_id,
                action=RelationAction.EXECUTE,
                confidence=1.0,
                reason="当前没有可关联的历史任务",
                effective_text=text,
            )

        target = _select_target(text, cards, selected=selected)
        intent = classify_conversation_intent(
            text=text,
            has_active_conversation=True,
            route_skill_id=route_skill_id or None,
            route_needs_clarification=not bool(route_skill_id),
        )
        if intent is ConversationIntent.REVISE_PREVIOUS:
            if target is None:
                if len(cards) == 1:
                    target = cards[0]
                else:
                    return _ask_decision(cards, reason="改稿要求可能对应多项任务", effective_text=text)
            return TaskRelationDecision(
                relation=TaskRelation.CONTINUE,
                target_task_id=target.task_id,
                suggested_skill_id=target.skill_id,
                action=RelationAction.EXECUTE,
                confidence=0.95,
                reason="已定位旧任务并识别为续改",
                effective_text=text,
            )

        if route_skill_id:
            return TaskRelationDecision(
                relation=TaskRelation.NEW_TASK,
                material_role=MaterialRole.NEW_TASK if has_new_material else MaterialRole.NONE,
                suggested_skill_id=route_skill_id,
                action=RelationAction.EXECUTE,
                confidence=0.95,
                reason="路由已明确识别新的业务能力",
                effective_text=text,
            )

        return _ask_decision(cards, reason="无法确定消息与现有任务的关系", effective_text=text)

    def _resolve_pending_answer(
        self,
        *,
        pending: PendingRelationDecision,
        cards: list[TaskCard],
        answer: str,
        route_skill_id: str,
    ) -> TaskRelationDecision | None:
        if _looks_like_explicit_new_task(answer):
            return TaskRelationDecision(
                relation=TaskRelation.NEW_TASK,
                material_role=MaterialRole.NEW_TASK if pending.has_new_material else MaterialRole.NONE,
                suggested_skill_id=route_skill_id or pending.route_skill_id,
                action=RelationAction.EXECUTE,
                confidence=1.0,
                reason="用户纠正为独立新任务",
                effective_text=pending.original_text,
            )
        candidate_set = set(pending.candidate_task_ids)
        candidates = [card for card in cards if card.task_id in candidate_set]
        target = _select_target(answer, candidates, selected=None)
        if target is None:
            return None
        relation = TaskRelation.ADD_MATERIAL if pending.has_new_material else TaskRelation.ANSWER_CLARIFICATION
        role = _material_role(pending.original_text) if pending.has_new_material else MaterialRole.NONE
        return TaskRelationDecision(
            relation=relation,
            target_task_id=target.task_id,
            material_role=role,
            suggested_skill_id=pending.route_skill_id or target.skill_id,
            action=RelationAction.EXECUTE,
            confidence=1.0,
            reason="用户回答关系确认问题",
            effective_text=pending.original_text,
        )

    def _semantic_decision(
        self,
        *,
        text: str,
        cards: list[TaskCard],
        selected: TaskCard | None,
        route_skill_id: str,
        has_new_material: bool,
    ) -> TaskRelationDecision | None:
        try:
            raw = self._semantic_classifier(
                text=text,
                tasks=[
                    {
                        "task_id": card.task_id,
                        "title": card.title,
                        "skill_id": card.skill_id,
                        "status": card.status.value,
                        "content_summary": card.content_summary[:240],
                        "pending_question": card.pending_question,
                    }
                    for card in cards
                ],
                route_skill_id=route_skill_id,
                has_new_material=has_new_material,
                selected_task_id=selected.task_id if selected is not None else "",
            )
            decision = raw if isinstance(raw, TaskRelationDecision) else _decision_from_mapping(raw)
        except Exception:
            return None
        valid_ids = {card.task_id for card in cards}
        if decision.target_task_id and decision.target_task_id not in valid_ids:
            return None
        if decision.confidence < self._automatic_confidence:
            return None
        if decision.action is RelationAction.EXECUTE and decision.relation is not TaskRelation.NEW_TASK:
            if not decision.target_task_id:
                return None
        return decision


_NEW_TASK_MARKERS = (
    "新任务",
    "另写一份",
    "另写一篇",
    "另起一份",
    "另起一篇",
    "重新写一份",
    "重新写一篇",
    "不是改旧稿",
    "不要改旧稿",
)
_DERIVE_MARKERS = ("沿用", "参考上一", "基于上一", "按上一", "用原来的结构")
_CANCEL_MARKERS = ("取消", "不要做了", "不用做了", "停止这个任务", "结束这个任务")
_SWITCH_MARKERS = ("切换到", "切到", "回到", "转到", "继续处理")
_MATERIAL_RELATION_MARKERS = ("补到", "加到", "加入", "补充到", "替换", "作为参考", "参考材料")
_DEICTIC_MARKERS = (
    "这篇稿",
    "这份稿",
    "当前稿",
    "当前任务",
    "刚才那份",
    "上一份",
    "上一篇",
    "那个稿",
    "再改",
    "继续改",
    "接着改",
    "这个直报",
    "这份直报",
    "这篇直报",
    "这个简报",
    "这份简报",
    "这篇简报",
)
_GENERIC_TITLE_MARKERS = (
    "深圳前海微众银行",
    "微众银行",
    "简报写作任务",
    "多素材简报任务",
    "直报写作任务",
    "材料润色任务",
    "内容处理任务",
    "修改稿",
    "初稿",
    "正文",
)


def _looks_like_explicit_new_task(text: str) -> bool:
    if any(marker in text for marker in _NEW_TASK_MARKERS):
        return True
    return bool(URL_RE.search(text)) and any(marker in text for marker in ("写", "生成", "起草", "新建"))


def _looks_like_cancel(text: str) -> bool:
    return any(marker in text for marker in _CANCEL_MARKERS)


def _looks_like_switch(text: str) -> bool:
    return any(marker in text for marker in _SWITCH_MARKERS)


def _looks_like_material_relation(text: str) -> bool:
    return any(marker in text for marker in _MATERIAL_RELATION_MARKERS)


def _looks_like_list_request(text: str) -> bool:
    return any(marker in text for marker in ("任务列表", "有哪些任务", "我有几个任务", "我有几篇稿", "看看任务"))


def _material_role(text: str) -> MaterialRole:
    if any(marker in text for marker in ("替换", "换掉", "取代")):
        return MaterialRole.REPLACE
    if any(marker in text for marker in ("作为参考", "参考材料", "只参考")):
        return MaterialRole.REFERENCE
    return MaterialRole.SUPPLEMENT


def _select_target(text: str, cards: Sequence[TaskCard], *, selected: TaskCard | None) -> TaskCard | None:
    if not cards:
        return None
    numbered = _numbered_target(text, cards)
    if numbered is not None:
        return numbered

    scored = [(card, _task_match_score(text, card)) for card in cards]
    scored.sort(key=lambda item: item[1], reverse=True)
    if scored and scored[0][1] >= 2.0:
        if len(scored) == 1 or scored[0][1] >= scored[1][1] + 1.0:
            return scored[0][0]

    if selected is not None and any(marker in text for marker in _DEICTIC_MARKERS):
        return next((card for card in cards if card.task_id == selected.task_id), None)
    if len(cards) == 1:
        return cards[0]
    return None


def _numbered_target(text: str, cards: Sequence[TaskCard]) -> TaskCard | None:
    patterns = (
        r"第\s*(\d+)\s*(?:篇|份|个|项|任务)",
        r"任务\s*(\d+)",
        r"第\s*([一二两三四五六七八九十])\s*(?:篇|份|个|项|任务)",
    )
    chinese = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        raw = match.group(1)
        index = int(raw) if raw.isdigit() else chinese.get(raw, 0)
        if 1 <= index <= len(cards):
            return cards[index - 1]
    return None


def _task_match_score(text: str, card: TaskCard) -> float:
    normalized_text = _normalize_match_text(text)
    normalized_title = _normalize_match_text(card.title)
    if normalized_title and normalized_title in normalized_text:
        return 20.0
    score = 0.0
    for size, weight in ((4, 3.0), (3, 2.0), (2, 1.0)):
        title_parts = _ngrams(normalized_title, size)
        if title_parts:
            score += len(title_parts & _ngrams(normalized_text, size)) * weight
    skill_markers = {
        "direct_report": ("直报", "报送"),
        "writer1": ("简报", "单素材", "多素材", "整合"),
        "rewrite": ("润色", "改写"),
        "research_synthesis": ("调研", "整合"),
        "shenyinxie_news": ("深银协", "协会动态"),
        "internal_weekly": ("内参", "周报"),
    }
    if any(marker in text for marker in skill_markers.get(card.skill_id, ())):
        score += 1.5
    return score


def _normalize_match_text(text: str) -> str:
    compact = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text).lower()
    for marker in _GENERIC_TITLE_MARKERS:
        compact = compact.replace(marker, "")
    return compact


def _ngrams(text: str, size: int) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _ask_decision(
    cards: Sequence[TaskCard],
    *,
    reason: str,
    effective_text: str = "",
) -> TaskRelationDecision:
    return TaskRelationDecision(
        relation=TaskRelation.NEEDS_CLARIFICATION,
        action=RelationAction.ASK,
        confidence=0.0,
        reason=reason,
        question=_task_question(cards),
        effective_text=effective_text,
    )


def _task_question(cards: Sequence[TaskCard]) -> str:
    if not cards:
        return "我还不确定这是新任务还是对已有任务的继续处理。请说明要新建任务，或补充具体需求。"
    options = "；".join(f"{index}.《{card.title}》" for index, card in enumerate(cards[:5], 1))
    return f"我需要确认你指的是哪一项：{options}。请回复标题关键词，或说明“另写一份”。"


def _task_list_message(cards: Sequence[TaskCard]) -> str:
    if not cards:
        return "当前没有可继续处理的任务。"
    labels = {
        TaskCardStatus.ASSEMBLING: "收集中",
        TaskCardStatus.QUEUED: "排队中",
        TaskCardStatus.RUNNING: "处理中",
        TaskCardStatus.NEEDS_INPUT: "待补充",
        TaskCardStatus.COMPLETED: "已完成",
        TaskCardStatus.FAILED: "失败",
        TaskCardStatus.CANCELLED: "已取消",
    }
    lines = ["当前任务："]
    lines.extend(
        f"{index}.《{card.title}》（{labels[card.status]}）"
        for index, card in enumerate(cards[:8], 1)
    )
    lines.append("回复标题关键词即可切换，或直接说明要修改哪一项。")
    return "\n".join(lines)


def _decision_from_mapping(raw: Mapping[str, object]) -> TaskRelationDecision:
    return TaskRelationDecision(
        relation=TaskRelation(str(raw.get("relation", TaskRelation.NEEDS_CLARIFICATION.value))),
        target_task_id=str(raw.get("target_task_id", "") or ""),
        material_role=MaterialRole(str(raw.get("material_role", MaterialRole.NONE.value))),
        suggested_skill_id=str(raw.get("suggested_skill_id", "") or ""),
        action=RelationAction(str(raw.get("action", RelationAction.ASK.value))),
        confidence=float(raw.get("confidence", 0.0) or 0.0),
        reason=str(raw.get("reason", "") or ""),
        question=str(raw.get("question", "") or ""),
        effective_text=str(raw.get("effective_text", "") or ""),
        parent_task_id=str(raw.get("parent_task_id", "") or ""),
    )


def _guard_unfinished_target(
    decision: TaskRelationDecision,
    cards: Sequence[TaskCard],
) -> TaskRelationDecision:
    if decision.relation not in {TaskRelation.CONTINUE, TaskRelation.ADD_MATERIAL}:
        return decision
    target = next(
        (card for card in cards if card.task_id == decision.target_task_id),
        None,
    )
    if target is None or target.status not in {
        TaskCardStatus.QUEUED,
        TaskCardStatus.RUNNING,
    }:
        return decision
    return TaskRelationDecision(
        relation=TaskRelation.NEEDS_CLARIFICATION,
        target_task_id=target.task_id,
        material_role=decision.material_role,
        suggested_skill_id=target.skill_id,
        action=RelationAction.ASK,
        confidence=1.0,
        reason="目标任务当前版本尚未完成",
        question=(
            f"《{target.title}》还在生成中。等当前版本完成后，请回复“继续”，"
            "我会接着处理这项要求。"
        ),
        effective_text=decision.effective_text,
    )


def _content_summary(*, title: str, body: str) -> str:
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    headings = [line for line in lines if re.match(r"^(?:[一二三四五六七八九十]+、|\d+[.、])", line)]
    selected = headings[:4] or lines[:3]
    summary = "；".join(selected)
    if not summary:
        summary = title
    return summary[:500]


def _safe_title(title: str, *, skill_id: str) -> str:
    clean = " ".join(title.split()).strip()
    if clean:
        return clean[:160]
    labels = {
        "direct_report": "直报写作任务",
        "writer1": "简报写作任务",
        "rewrite": "材料润色任务",
        "research_synthesis": "综合调研整合任务",
        "shenyinxie_news": "深银协动态任务",
        "internal_weekly": "内参周报任务",
    }
    return labels.get(skill_id, "内容处理任务")


def _ensure_owner(row: sqlite3.Row, *, channel: str, user_id: str) -> None:
    if channel and str(row["channel"]) != channel:
        raise PermissionError("任务不属于当前入口")
    if user_id and str(row["user_id"]) != user_id:
        raise PermissionError("任务不属于当前用户")


def _require_identity(*, task_id: str, channel: str, user_id: str) -> None:
    if not task_id.strip() or not channel.strip() or not user_id.strip():
        raise ValueError("task_id、channel 和 user_id 不能为空")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_loads(raw: str, *, default: Any) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


__all__ = [
    "MaterialRole",
    "PendingRelationDecision",
    "PydanticTaskRelationClassifier",
    "RelationAction",
    "TaskCard",
    "TaskCardStatus",
    "TaskMaterial",
    "TaskRelation",
    "TaskRelationDecision",
    "TaskRelationRepository",
    "TaskRelationService",
    "SemanticTaskRelationOutput",
]
