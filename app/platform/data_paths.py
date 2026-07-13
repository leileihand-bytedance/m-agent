from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


DATA_ROOT_ENV = "M_AGENT_DATA_DIR"


@dataclass(frozen=True)
class DataPaths:
    """M-Agent 所有非 Git 运行数据的统一路径。"""

    root: Path
    writing_jobs: Path
    review_tasks: Path
    policy_db: Path
    bank_db: Path
    policy_wiki: Path
    chat_logs: Path
    conversations: Path
    ops_events: Path
    ops_state: Path
    heartbeats: Path
    logs: Path
    user_registry: Path
    legacy: Path

    @classmethod
    def from_values(
        cls,
        values: Mapping[str, str],
        *,
        project_root: Path,
    ) -> "DataPaths":
        raw_root = str(values.get(DATA_ROOT_ENV, "") or "").strip()
        root = Path(raw_root).expanduser() if raw_root else project_root.parent / "M-Agent-Files"
        if not root.is_absolute():
            root = project_root / root
        root = root.resolve(strict=False)
        return cls(
            root=root,
            writing_jobs=root / "tasks" / "writing",
            review_tasks=root / "tasks" / "review",
            policy_db=root / "knowledge" / "policy" / "policies.sqlite3",
            bank_db=root / "knowledge" / "bank" / "bank.sqlite3",
            policy_wiki=root / "knowledge" / "policy-wiki",
            chat_logs=root / "runtime" / "chat-logs",
            conversations=root / "runtime" / "conversations",
            ops_events=root / "runtime" / "ops" / "events",
            ops_state=root / "runtime" / "ops" / "state.json",
            heartbeats=root / "runtime" / "ops" / "heartbeats",
            logs=root / "runtime" / "logs",
            user_registry=root / "runtime" / "users" / "review_users.yaml",
            legacy=root / "legacy",
        )

    def managed_paths(self) -> tuple[Path, ...]:
        return (
            self.root,
            self.writing_jobs,
            self.review_tasks,
            self.policy_db,
            self.bank_db,
            self.policy_wiki,
            self.chat_logs,
            self.conversations,
            self.ops_events,
            self.ops_state,
            self.heartbeats,
            self.logs,
            self.user_registry,
            self.legacy,
        )

    def prepare(self) -> None:
        directories = (
            self.root,
            self.writing_jobs,
            self.review_tasks,
            self.policy_db.parent,
            self.bank_db.parent,
            self.policy_wiki,
            self.chat_logs,
            self.conversations,
            self.ops_events,
            self.ops_state.parent,
            self.heartbeats,
            self.logs,
            self.user_registry.parent,
            self.legacy,
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        readme = self.root / "README.txt"
        if not readme.exists():
            readme.write_text(
                "M-Agent 运行数据目录（不纳入 Git）\n\n"
                "tasks：用户上传文件和系统生成结果。\n"
                "knowledge：政策库、微众银行信息库和政策 Wiki。\n"
                "runtime：日志、会话、用户名表和运维状态。\n"
                "legacy：迁移保留的历史运行数据。\n\n"
                "请不要把本目录复制进代码仓库或提交到 Git。\n",
                encoding="utf-8",
            )


def configured_path(
    values: Mapping[str, str],
    key: str,
    default: Path,
    *,
    project_root: Path,
) -> Path:
    raw = str(values.get(key, "") or "").strip()
    if not raw:
        return default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)
