from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Any

import yaml


@dataclass(frozen=True)
class AdminPaths:
    skills_dir: Path
    policy_path: Path
    jobs_dir: Path
    project_root: Path | None = None
    todo_path: Path | None = None
    review_tasks_dir: Path | None = None
    heartbeat_dir: Path | None = None
    policy_db_path: Path | None = None
    bank_db_path: Path | None = None
    heartbeat_max_age_seconds: int = 180


@dataclass(frozen=True)
class SkillAdminSummary:
    id: str
    name: str
    description: str
    enabled: bool
    triggers: list[str]
    allowed_tools: list[str]
    workflow: str
    skill_preview: str


@dataclass(frozen=True)
class JobAdminSummary:
    job_id: str
    channel: str
    sender_userid: str
    created_at: str
    message_preview: str
    skill_id: str
    title: str
    needs_clarification: bool
    message: str
    path: Path


@dataclass(frozen=True)
class TodoAdminSummary:
    todo_id: str
    title: str
    status: str
    priority: str
    owner: str
    next_action: str
    is_open: bool


@dataclass(frozen=True)
class ServiceHealthSummary:
    service: str
    name: str
    status: str
    updated_at: str
    age_seconds: int | None


@dataclass(frozen=True)
class RepositoryAdminSummary:
    available: bool
    branch: str = ""
    short_commit: str = ""
    latest_date: str = ""
    latest_subject: str = ""
    dirty_count: int = 0
    ahead: int | None = None
    behind: int | None = None

    @property
    def sync_label(self) -> str:
        if not self.available:
            return "未检测到 Git"
        if self.ahead is None or self.behind is None:
            return "未配置远端"
        if self.ahead == 0 and self.behind == 0:
            return "本地与已知远端记录同步"
        if self.behind:
            return f"远端领先 {self.behind} 个提交"
        return f"本地待推送 {self.ahead} 个提交"


@dataclass(frozen=True)
class RecentChangeSummary:
    commit: str
    date: str
    subject: str


@dataclass(frozen=True)
class ModuleAdminSummary:
    key: str
    name: str
    status: str
    current_summary: str
    latest_change: str
    next_todo_id: str
    next_todo_title: str


@dataclass(frozen=True)
class ProjectOverview:
    generated_at: str
    enabled_skill_count: int
    total_skill_count: int
    open_todo_count: int
    urgent_todo_count: int
    writing_job_count: int
    review_task_count: int
    policy_count: int | None
    bank_count: int | None
    repository: RepositoryAdminSummary
    todos: tuple[TodoAdminSummary, ...]
    services: tuple[ServiceHealthSummary, ...]
    modules: tuple[ModuleAdminSummary, ...]
    recent_changes: tuple[RecentChangeSummary, ...]


_TODO_HEADING_RE = re.compile(r"^###\s+(TODO-\d{3})[：:]\s*(.+?)\s*$", re.MULTILINE)
_CLOSED_TODO_STATUSES = {"已完成", "已取消", "已暂缓"}
_SERVICE_LABELS = (
    ("writing_bot", "写作 Bot"),
    ("review_bot", "审核 Bot"),
    ("ops_bot", "运维 Bot"),
)
_MODULE_SPECS = (
    ("platform", "底座", ("底座", "企业微信入口"), ("app/platform", "docs/agent-platform")),
    ("writing", "写作", ("直报", "简报", "写作"), ("app/writing", "skills/direct_report", "skills/writer1", "skills/writer2", "skills/rewrite")),
    ("review", "审核", ("审核",), ("app/review", "tests/test_review", "tests/test_official_format_review.py")),
    ("knowledge", "知识库", ("政策知识库", "微众银行信息库", "知识库"), ("app/policy_knowledge", "app/bank_knowledge", "docs/development/policy-knowledge-base.md", "docs/development/bank-knowledge-base.md")),
    ("operations", "入口与运维", ("运维", "企业微信入口"), ("app/platform/ops", "app/writing/bot.py", "app/review/main.py")),
    ("admin", "管理后台", ("管理后台",), ("app/admin", "docs/development/admin-console.md")),
)


def list_skills(skills_dir: Path) -> list[SkillAdminSummary]:
    summaries: list[SkillAdminSummary] = []
    if not skills_dir.exists():
        return []

    for config_path in sorted(skills_dir.glob("*/config.yaml")):
        raw = _read_yaml(config_path)
        skill_path = config_path.parent / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
        summaries.append(
            SkillAdminSummary(
                id=str(raw.get("id", config_path.parent.name)),
                name=str(raw.get("name", config_path.parent.name)),
                description=str(raw.get("description", "")),
                enabled=bool(raw.get("enabled", False)),
                triggers=_string_list(raw.get("triggers", [])),
                allowed_tools=_string_list(raw.get("allowed_tools", [])),
                workflow=str(raw.get("workflow", "")),
                skill_preview=skill_text[:1000],
            )
        )
    return summaries


def set_skill_enabled(skills_dir: Path, skill_id: str, enabled: bool) -> None:
    config_path = _skill_config_path(skills_dir, skill_id)
    raw = _read_yaml(config_path)
    raw["enabled"] = enabled
    _write_yaml(config_path, raw)


def list_policy_users(policy_path: Path) -> dict[str, list[str]]:
    raw = _read_yaml(policy_path)
    users = raw.get("users", {})
    if not isinstance(users, dict):
        return {}
    return {
        str(userid): _string_list(value.get("allowed_skills", []) if isinstance(value, dict) else [])
        for userid, value in users.items()
    }


def set_user_skills(policy_path: Path, userid: str, allowed_skills: list[str]) -> None:
    raw = _read_yaml(policy_path)
    raw.setdefault("allow_unknown_users", False)
    raw.setdefault("default_allowed_skills", [])
    users = raw.setdefault("users", {})
    if not isinstance(users, dict):
        users = {}
        raw["users"] = users
    users[userid] = {"allowed_skills": [skill for skill in allowed_skills if skill]}
    _write_yaml(policy_path, raw)


def list_jobs(paths: AdminPaths, limit: int = 20) -> list[JobAdminSummary]:
    jobs_dir = paths.jobs_dir
    if not jobs_dir.exists():
        return []

    summaries: list[JobAdminSummary] = []
    job_dirs = (path.parent for path in jobs_dir.glob("**/meta.json"))
    for job_dir in sorted(job_dirs, key=lambda path: path.name, reverse=True):
        meta = _read_json(job_dir / "meta.json")
        result = _read_json(job_dir / "output" / "result.json")
        output = result.get("output", {}) if isinstance(result.get("output", {}), dict) else {}
        summaries.append(
            JobAdminSummary(
                job_id=str(meta.get("job_id", job_dir.name)),
                channel=str(meta.get("channel", "")),
                sender_userid=str(meta.get("sender_userid", "")),
                created_at=str(meta.get("created_at", "")),
                message_preview=str(meta.get("message_preview", "")),
                skill_id=str(result.get("skill_id", "")),
                title=str(output.get("title", "")),
                needs_clarification=bool(result.get("needs_clarification", False)),
                message=str(result.get("message", "")),
                path=job_dir,
            )
        )
        if len(summaries) >= limit:
            break
    return summaries


def list_todos(todo_path: Path) -> list[TodoAdminSummary]:
    if not todo_path.exists():
        return []
    text = todo_path.read_text(encoding="utf-8")
    matches = list(_TODO_HEADING_RE.finditer(text))
    todos: list[TodoAdminSummary] = []
    for index, match in enumerate(matches):
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end() : section_end]
        status = _section_field(section, "状态")
        priority = _section_field(section, "优先级") or "P9"
        owner = _section_field(section, "归属")
        todos.append(
            TodoAdminSummary(
                todo_id=match.group(1),
                title=match.group(2).strip(),
                status=status or "未标记",
                priority=priority,
                owner=owner or "未归属",
                next_action=_section_next_action(section),
                is_open=status not in _CLOSED_TODO_STATUSES,
            )
        )
    return sorted(todos, key=_todo_sort_key)


def list_service_health(
    heartbeat_dir: Path | None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 180,
) -> list[ServiceHealthSummary]:
    current = now or datetime.now()
    summaries: list[ServiceHealthSummary] = []
    for service, name in _SERVICE_LABELS:
        heartbeat_path = heartbeat_dir / f"{service}.json" if heartbeat_dir else None
        payload = _read_json(heartbeat_path) if heartbeat_path else {}
        updated_at = str(payload.get("updated_at", "") or "")
        parsed = _parse_datetime(updated_at)
        age_seconds = max(0, int((current - parsed).total_seconds())) if parsed else None
        if age_seconds is None:
            status = "missing"
        elif age_seconds <= max_age_seconds:
            status = "healthy"
        else:
            status = "stale"
        summaries.append(
            ServiceHealthSummary(
                service=service,
                name=name,
                status=status,
                updated_at=updated_at,
                age_seconds=age_seconds,
            )
        )
    return summaries


def build_project_overview(paths: AdminPaths) -> ProjectOverview:
    project_root = paths.project_root or paths.skills_dir.parent
    todo_path = paths.todo_path or project_root / "docs" / "development" / "TODO.md"
    skills = list_skills(paths.skills_dir)
    todos = list_todos(todo_path)
    services = list_service_health(
        paths.heartbeat_dir,
        max_age_seconds=paths.heartbeat_max_age_seconds,
    )
    writing_job_count = _count_meta_files(paths.jobs_dir)
    review_task_count = _count_meta_files(paths.review_tasks_dir)
    policy_count = _sqlite_table_count(paths.policy_db_path, "policy_documents")
    bank_count = _sqlite_table_count(paths.bank_db_path, "bank_entries")
    repository = repository_summary(project_root)
    recent_changes = tuple(list_recent_changes(project_root, limit=8))
    open_todos = [todo for todo in todos if todo.is_open]
    modules = tuple(
        _build_module_summaries(
            project_root=project_root,
            skills=skills,
            todos=open_todos,
            services=services,
            writing_job_count=writing_job_count,
            review_task_count=review_task_count,
            policy_count=policy_count,
            bank_count=bank_count,
        )
    )
    return ProjectOverview(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        enabled_skill_count=sum(1 for skill in skills if skill.enabled),
        total_skill_count=len(skills),
        open_todo_count=len(open_todos),
        urgent_todo_count=sum(1 for todo in open_todos if todo.priority in {"P0", "P1"}),
        writing_job_count=writing_job_count,
        review_task_count=review_task_count,
        policy_count=policy_count,
        bank_count=bank_count,
        repository=repository,
        todos=tuple(todos),
        services=tuple(services),
        modules=modules,
        recent_changes=recent_changes,
    )


def repository_summary(project_root: Path) -> RepositoryAdminSummary:
    if not (project_root / ".git").exists():
        return RepositoryAdminSummary(available=False)
    branch = _run_git(project_root, "rev-parse", "--abbrev-ref", "HEAD")
    latest = _run_git(project_root, "log", "-1", "--date=short", "--format=%h%x1f%ad%x1f%s")
    latest_parts = latest.split("\x1f", 2) if latest else []
    status_lines = [line for line in _run_git(project_root, "status", "--porcelain").splitlines() if line]
    ahead: int | None = None
    behind: int | None = None
    sync_counts = _run_git(project_root, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if sync_counts:
        try:
            ahead_text, behind_text = sync_counts.split()
            ahead, behind = int(ahead_text), int(behind_text)
        except (ValueError, TypeError):
            ahead, behind = None, None
    return RepositoryAdminSummary(
        available=True,
        branch=branch,
        short_commit=latest_parts[0] if len(latest_parts) >= 1 else "",
        latest_date=latest_parts[1] if len(latest_parts) >= 2 else "",
        latest_subject=latest_parts[2] if len(latest_parts) >= 3 else "",
        dirty_count=len(status_lines),
        ahead=ahead,
        behind=behind,
    )


def list_recent_changes(project_root: Path, *, limit: int = 8) -> list[RecentChangeSummary]:
    if not (project_root / ".git").exists():
        return []
    output = _run_git(
        project_root,
        "log",
        f"-{max(1, limit)}",
        "--date=short",
        "--format=%h%x1f%ad%x1f%s",
    )
    changes: list[RecentChangeSummary] = []
    for line in output.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) == 3:
            changes.append(RecentChangeSummary(commit=parts[0], date=parts[1], subject=parts[2]))
    return changes


def _build_module_summaries(
    *,
    project_root: Path,
    skills: list[SkillAdminSummary],
    todos: list[TodoAdminSummary],
    services: list[ServiceHealthSummary],
    writing_job_count: int,
    review_task_count: int,
    policy_count: int | None,
    bank_count: int | None,
) -> list[ModuleAdminSummary]:
    enabled_skills = [skill.name for skill in skills if skill.enabled]
    healthy_services = sum(1 for service in services if service.status == "healthy")
    service_total = len(services)
    current_summaries = {
        "platform": "统一路由、权限、ToolGateway、会话和 DOCX/PDF/PPTX 文档服务已可用。",
        "writing": f"已启用 {len(enabled_skills)} 个 skill；累计记录 {writing_job_count} 个写作任务。",
        "review": f"独立审核 Bot 继续运行；累计归档 {review_task_count} 个审核任务。",
        "knowledge": f"政策库 {_display_count(policy_count)} 条，微众银行信息库 {_display_count(bank_count)} 条。",
        "operations": f"写作、审核、运维共 {service_total} 个服务，当前 {healthy_services} 个心跳正常。",
        "admin": "本机控制台提供项目观察、Skill 开关、用户权限和任务摘要。",
    }
    summaries: list[ModuleAdminSummary] = []
    for key, name, owner_keywords, git_paths in _MODULE_SPECS:
        module_todos = [todo for todo in todos if any(keyword in todo.owner for keyword in owner_keywords)]
        next_todo = module_todos[0] if module_todos else None
        latest_change = _latest_module_change(project_root, git_paths)
        summaries.append(
            ModuleAdminSummary(
                key=key,
                name=name,
                status=_module_status(next_todo),
                current_summary=current_summaries[key],
                latest_change=latest_change,
                next_todo_id=next_todo.todo_id if next_todo else "",
                next_todo_title=next_todo.title if next_todo else "暂无开放待办",
            )
        )
    return summaries


def _section_field(section: str, field: str) -> str:
    match = re.search(rf"^{re.escape(field)}[：:]\s*(.+?)\s*$", section, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _section_next_action(section: str) -> str:
    goal_match = re.search(r"^目标[：:]\s*$", section, re.MULTILINE)
    search_text = section[goal_match.end() :] if goal_match else section
    goals: list[str] = []
    for raw_line in search_text.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            goals.append(line[2:].strip())
        if goal_match and line.endswith("：") and not line.startswith("-"):
            break
    for goal in goals:
        for marker in ("待补", "待完成", "尚未完成", "下一步"):
            marker_index = goal.find(marker)
            if marker_index >= 0:
                return goal[marker_index:]
    for goal in goals:
        if not goal.startswith(("已完成", "已取消", "已暂缓")):
            return goal
    if goals:
        return goals[0]
    return "查看待办详情"


def _todo_sort_key(todo: TodoAdminSummary) -> tuple[int, int, str]:
    priority_match = re.fullmatch(r"P(\d+)", todo.priority.upper())
    priority = int(priority_match.group(1)) if priority_match else 99
    return (0 if todo.is_open else 1, priority, todo.todo_id)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _count_meta_files(root: Path | None) -> int:
    if root is None or not root.exists():
        return 0
    return sum(1 for _ in root.glob("**/meta.json"))


def _sqlite_table_count(path: Path | None, table: str) -> int | None:
    if path is None or not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
        return int(row[0]) if row else 0
    except (sqlite3.Error, OSError, ValueError):
        return None


def _display_count(value: int | None) -> str:
    return str(value) if value is not None else "未检测"


def _module_status(todo: TodoAdminSummary | None) -> str:
    if todo is None:
        return "稳定"
    if todo.priority == "P0":
        return "重点推进"
    if todo.priority == "P1":
        return "建设中"
    return "持续优化"


def _latest_module_change(project_root: Path, paths: tuple[str, ...]) -> str:
    if not (project_root / ".git").exists():
        return "暂无 Git 记录"
    output = _run_git(
        project_root,
        "log",
        "-1",
        "--date=short",
        "--format=%ad%x1f%s",
        "--",
        *paths,
    )
    if not output:
        return "暂无 Git 记录"
    parts = output.split("\x1f", 1)
    return f"{parts[0]} · {parts[1]}" if len(parts) == 2 else output


def _run_git(project_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _skill_config_path(skills_dir: Path, skill_id: str) -> Path:
    if "/" in skill_id or "\\" in skill_id or skill_id in {"", ".", ".."}:
        raise ValueError("Invalid skill id")
    config_path = skills_dir / skill_id / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Skill config not found: {skill_id}")
    return config_path


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _write_yaml(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
