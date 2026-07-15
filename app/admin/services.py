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
class CapabilityAdminSummary:
    id: str
    name: str
    description: str
    status: str
    status_label: str
    evidence: str
    todo_id: str = ""
    next_action: str = ""
    runtime_status: str = ""
    runtime_label: str = ""


@dataclass(frozen=True)
class ArchitectureLayerSummary:
    key: str
    order: str
    name: str
    description: str
    capabilities: tuple[CapabilityAdminSummary, ...]


@dataclass(frozen=True)
class ArchitectureRelationSummary:
    source_id: str
    target_id: str
    label: str


@dataclass(frozen=True)
class TaskStatistics:
    total: int = 0
    completed: int = 0
    needs_input: int = 0
    failed: int = 0
    incomplete: int = 0
    unknown: int = 0
    legacy: int = 0


@dataclass(frozen=True)
class ProjectOverview:
    generated_at: str
    enabled_skill_count: int
    total_skill_count: int
    open_todo_count: int
    urgent_todo_count: int
    writing_job_count: int
    review_task_count: int
    writing_task_stats: TaskStatistics
    review_task_stats: TaskStatistics
    policy_count: int | None
    bank_count: int | None
    repository: RepositoryAdminSummary
    todos: tuple[TodoAdminSummary, ...]
    services: tuple[ServiceHealthSummary, ...]
    architecture_layers: tuple[ArchitectureLayerSummary, ...]
    architecture_relations: tuple[ArchitectureRelationSummary, ...]
    capability_status_counts: dict[str, int]
    modules: tuple[ModuleAdminSummary, ...]
    recent_changes: tuple[RecentChangeSummary, ...]


@dataclass(frozen=True)
class _CapabilitySpec:
    id: str
    name: str
    description: str
    default_status: str
    evidence_paths: tuple[str, ...] = ()
    todo_ids: tuple[str, ...] = ()
    todo_policy: str = "none"
    skill_ids: tuple[str, ...] = ()
    runtime_service: str = ""
    build_when_evidence_exists: bool = False


@dataclass(frozen=True)
class _ArchitectureLayerSpec:
    key: str
    order: str
    name: str
    description: str
    capabilities: tuple[_CapabilitySpec, ...]


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
_CAPABILITY_STATUS_LABELS = {
    "stable": "稳定运行",
    "optimizing": "已上线·优化中",
    "building": "建设中",
    "planned": "待建设",
    "paused": "已暂缓",
    "disabled": "已关闭",
}
_RUNTIME_STATUS_LABELS = {
    "healthy": "当前在线",
    "stale": "心跳超时",
    "missing": "未运行",
}
_ARCHITECTURE_LAYER_SPECS = (
    _ArchitectureLayerSpec(
        key="entry",
        order="01",
        name="用户入口",
        description="用户、管理员与系统交互的入口。",
        capabilities=(
            _CapabilitySpec(
                "writing_bot",
                "写作 Bot",
                "接收直报、简报、润色和连续改稿请求。",
                "optimizing",
                ("app/writing/bot.py",),
                ("TODO-001", "TODO-002", "TODO-003"),
                "optimize",
                runtime_service="writing_bot",
            ),
            _CapabilitySpec(
                "review_bot",
                "审核 Bot",
                "保持独立入口，承载内容、格式和多文件审核。",
                "optimizing",
                ("app/review/main.py",),
                ("TODO-023", "TODO-021", "TODO-020"),
                "optimize",
                runtime_service="review_bot",
            ),
            _CapabilitySpec(
                "ops_bot",
                "运维 Bot",
                "发送实时异常告警和工作日日报。",
                "stable",
                ("app/platform/ops/bot.py",),
                runtime_service="ops_bot",
            ),
            _CapabilitySpec(
                "admin_console",
                "本机项目控制台",
                "查看项目状态并管理 Skill 和用户权限。",
                "stable",
                ("app/admin/server.py",),
            ),
            _CapabilitySpec(
                "unified_entry",
                "统一企业微信入口",
                "长期将更多业务能力收口到统一入口。",
                "paused",
                ("app/platform/gateway/wecom.py",),
                ("TODO-004",),
                "work",
            ),
        ),
    ),
    _ArchitectureLayerSpec(
        key="platform",
        order="02",
        name="通用底座",
        description="所有业务能力共用的安全运行框架。",
        capabilities=(
            _CapabilitySpec(
                "gateway_identity",
                "入口适配与用户权限",
                "标准化企业微信消息并限制用户可用能力。",
                "stable",
                ("app/platform/gateway/wecom.py", "app/platform/identity.py"),
            ),
            _CapabilitySpec(
                "router_registry",
                "意图路由与 Skill 注册",
                "只在已登记、已授权的能力中识别用户意图。",
                "stable",
                ("app/platform/router.py", "app/platform/registry.py"),
            ),
            _CapabilitySpec(
                "runtime_gateway",
                "运行层与工具授权",
                "通过 ToolGateway 和 Pydantic AI 执行受限工作流。",
                "stable",
                ("app/platform/runtime.py", "app/platform/tools.py", "app/platform/pydantic_runtime.py"),
            ),
            _CapabilitySpec(
                "conversation_revision",
                "会话与稿件版本链",
                "保存活跃稿件并区分续改、回退和新任务。",
                "optimizing",
                ("app/platform/conversation.py", "app/platform/intent.py"),
                ("TODO-018",),
                "optimize",
            ),
            _CapabilitySpec(
                "task_intake",
                "公共任务组装",
                "共用安全暂存、恢复和文件限制，继续统一业务组装接口。",
                "building",
                ("app/platform/intake.py", "app/writing/intake.py", "app/review/intake.py"),
                ("TODO-003",),
                "work",
                build_when_evidence_exists=True,
            ),
            _CapabilitySpec(
                "task_execution",
                "后台任务执行与恢复",
                "持久化执行重任务，限制并发并支持去重、重启恢复和取消。",
                "planned",
                todo_ids=("TODO-027",),
                todo_policy="work",
            ),
            _CapabilitySpec(
                "document_service",
                "统一文档服务",
                "安全解析 DOCX、PDF、PPTX 并保留定位信息。",
                "building",
                ("app/platform/documents/service.py",),
                ("TODO-024",),
                "work",
                build_when_evidence_exists=True,
            ),
            _CapabilitySpec(
                "attachment_delivery",
                "公共附件回传",
                "统一处理大文件、图片压缩和多结果回传。",
                "planned",
                todo_ids=("TODO-017",),
                todo_policy="work",
            ),
        ),
    ),
    _ArchitectureLayerSpec(
        key="capabilities",
        order="03",
        name="业务功能",
        description="面向用户交付结果的写作与审核能力。",
        capabilities=(
            _CapabilitySpec(
                "direct_report",
                "直报写作",
                "根据链接、文字或文件生成直报并支持连续改稿。",
                "stable",
                ("skills/direct_report",),
                ("TODO-001",),
                "optimize",
                ("direct_report",),
            ),
            _CapabilitySpec(
                "brief_writing",
                "简报写作",
                "覆盖单素材和多素材简报，并支持连续改稿。",
                "stable",
                ("skills/writer1", "skills/writer2"),
                ("TODO-002",),
                "optimize",
                ("writer1", "writer2"),
            ),
            _CapabilitySpec(
                "rewrite",
                "材料润色",
                "对用户直接粘贴的初稿做独立润色和后续修改。",
                "stable",
                ("skills/rewrite",),
                skill_ids=("rewrite",),
            ),
            _CapabilitySpec(
                "general_review",
                "通用内容审核",
                "检查文字、逻辑、事实一致性并返回标注文档。",
                "stable",
                ("app/review/general_reviewer.py",),
                ("TODO-023",),
                "optimize",
            ),
            _CapabilitySpec(
                "official_format_review",
                "公文格式审核",
                "按显式指令检查字体、字号、层级和页面设置。",
                "stable",
                ("app/review/official_format_checker.py",),
                ("TODO-019",),
                "work",
            ),
            _CapabilitySpec(
                "multi_file_review",
                "多文件联合审核",
                "联合检查正文、附件引用和跨文件矛盾。",
                "planned",
                ("app/review/multi_file_reviewer.py",),
                ("TODO-021",),
                "work",
                build_when_evidence_exists=True,
            ),
            _CapabilitySpec(
                "ppt_review",
                "PPT 专项审核",
                "检查幻灯片文字、逻辑、版式并提供可交付结果。",
                "planned",
                todo_ids=("TODO-020",),
                todo_policy="work",
            ),
            _CapabilitySpec(
                "writing_final_review",
                "直报/简报成稿审核",
                "为写作结果增加各自口径的末端审核。",
                "planned",
                todo_ids=("TODO-016",),
                todo_policy="work",
            ),
        ),
    ),
    _ArchitectureLayerSpec(
        key="resources",
        order="04",
        name="工具与知识库",
        description="写作和审核共用的材料读取、搜索与背景信息。",
        capabilities=(
            _CapabilitySpec(
                "web_tools",
                "网页读取与联网搜索",
                "读取公开链接并在必要时补充外部背景。",
                "stable",
                ("app/platform/builtin_tools.py",),
                ("TODO-012",),
                "optimize",
            ),
            _CapabilitySpec(
                "docx_reader",
                "DOCX 读取",
                "提取正文、表格和基础结构并保留定位。",
                "stable",
                ("app/platform/documents/parsers/docx.py",),
            ),
            _CapabilitySpec(
                "pdf_ppt_reader",
                "PDF / PPTX 读取",
                "已可提取文本和结构，继续补 OCR 与页面渲染。",
                "building",
                ("app/platform/documents/parsers/pdf.py", "app/platform/documents/parsers/pptx.py"),
                ("TODO-024",),
                "work",
                build_when_evidence_exists=True,
            ),
            _CapabilitySpec(
                "policy_knowledge",
                "政策知识库",
                "为写作提供经过筛选的监管和宏观政策背景。",
                "stable",
                ("app/policy_knowledge",),
                ("TODO-008", "TODO-009"),
                "optimize",
            ),
            _CapabilitySpec(
                "bank_knowledge",
                "微众银行信息库",
                "提供银行自身产品、数据和事实口径。",
                "stable",
                ("app/bank_knowledge",),
                ("TODO-010",),
                "optimize",
            ),
        ),
    ),
    _ArchitectureLayerSpec(
        key="operations",
        order="05",
        name="运维与数据",
        description="保证项目可追踪、可告警、可交付和可维护。",
        capabilities=(
            _CapabilitySpec(
                "task_files",
                "任务与文件存储",
                "用户材料和系统产物统一保存到 M-Agent-Files。",
                "stable",
                ("app/platform/data_paths.py", "app/platform/storage.py"),
            ),
            _CapabilitySpec(
                "logs_identity",
                "日志与用户名映射",
                "记录开发期对话、任务和可读用户名。",
                "stable",
                ("app/platform/chat_log.py", "app/platform/user_registry.py"),
            ),
            _CapabilitySpec(
                "ops_monitoring",
                "告警、心跳与日报",
                "监控三个 Bot 并向管理员发送异常和日报。",
                "stable",
                ("app/platform/ops",),
                runtime_service="ops_bot",
            ),
            _CapabilitySpec(
                "delivery_rules",
                "测试、文档与 Git 闸门",
                "用自动化测试和受管推送保证交付可追溯。",
                "stable",
                ("scripts/project_docs.py", "docs/development/testing-and-delivery.md"),
            ),
            _CapabilitySpec(
                "retention_cleanup",
                "运行数据保留与清理",
                "按类别制定保留周期并先预演再清理。",
                "planned",
                todo_ids=("TODO-022",),
                todo_policy="work",
            ),
            _CapabilitySpec(
                "policy_admin",
                "政策库可视化管理",
                "查看、筛选、备份并勾选删除政策数据。",
                "planned",
                todo_ids=("TODO-007",),
                todo_policy="work",
            ),
        ),
    ),
)

_ARCHITECTURE_RELATION_SPECS = (
    ("writing_bot", "gateway_identity", "身份校验"),
    ("review_bot", "gateway_identity", "身份校验"),
    ("unified_entry", "gateway_identity", "统一接入"),
    ("writing_bot", "task_intake", "组装请求"),
    ("review_bot", "task_intake", "组装请求"),
    ("gateway_identity", "router_registry", "授权路由"),
    ("task_intake", "router_registry", "提交任务"),
    ("task_intake", "task_execution", "提交后台任务"),
    ("task_execution", "runtime_gateway", "恢复执行"),
    ("router_registry", "runtime_gateway", "选择能力"),
    ("runtime_gateway", "direct_report", "执行 Skill"),
    ("runtime_gateway", "brief_writing", "执行 Skill"),
    ("runtime_gateway", "rewrite", "执行 Skill"),
    ("conversation_revision", "direct_report", "续改稿件"),
    ("conversation_revision", "brief_writing", "续改稿件"),
    ("conversation_revision", "rewrite", "续改稿件"),
    ("review_bot", "general_review", "执行审核"),
    ("review_bot", "official_format_review", "执行审核"),
    ("review_bot", "multi_file_review", "执行审核"),
    ("review_bot", "ppt_review", "执行审核"),
    ("document_service", "direct_report", "提供材料"),
    ("document_service", "brief_writing", "提供材料"),
    ("document_service", "general_review", "提供材料"),
    ("document_service", "multi_file_review", "提供材料"),
    ("document_service", "ppt_review", "提供材料"),
    ("web_tools", "direct_report", "提供素材"),
    ("web_tools", "brief_writing", "提供素材"),
    ("policy_knowledge", "direct_report", "提供背景"),
    ("policy_knowledge", "brief_writing", "提供背景"),
    ("bank_knowledge", "direct_report", "提供事实"),
    ("bank_knowledge", "brief_writing", "提供事实"),
    ("docx_reader", "document_service", "标准解析"),
    ("pdf_ppt_reader", "document_service", "标准解析"),
    ("direct_report", "writing_final_review", "成稿检查"),
    ("brief_writing", "writing_final_review", "成稿检查"),
    ("general_review", "attachment_delivery", "回传结果"),
    ("official_format_review", "attachment_delivery", "回传结果"),
    ("multi_file_review", "attachment_delivery", "回传结果"),
    ("runtime_gateway", "task_files", "保存任务"),
    ("writing_bot", "logs_identity", "记录对话"),
    ("review_bot", "logs_identity", "记录任务"),
    ("writing_bot", "ops_monitoring", "上报状态"),
    ("review_bot", "ops_monitoring", "上报状态"),
    ("ops_monitoring", "ops_bot", "发送告警"),
    ("task_files", "retention_cleanup", "到期清理"),
    ("policy_admin", "policy_knowledge", "治理数据"),
    ("admin_console", "router_registry", "管理 Skill"),
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


def summarize_writing_tasks(root: Path | None) -> TaskStatistics:
    if root is None or not root.exists():
        return TaskStatistics()

    total = 0
    completed = 0
    needs_input = 0
    failed = 0
    incomplete = 0
    unknown = 0
    for meta_path in root.glob("**/meta.json"):
        total += 1
        status = str(_read_json(meta_path.parent / "status.json").get("processing_status", ""))
        if status == "completed":
            completed += 1
        elif status == "needs_input":
            needs_input += 1
        elif status == "failed":
            failed += 1
        elif status in {"processing", "incomplete"}:
            incomplete += 1
        else:
            unknown += 1

    return TaskStatistics(
        total=total,
        completed=completed,
        needs_input=needs_input,
        failed=failed,
        incomplete=incomplete,
        unknown=unknown,
    )


def summarize_review_tasks(root: Path | None) -> TaskStatistics:
    if root is None or not root.exists():
        return TaskStatistics()

    task_dirs = {path.parent for path in root.glob("**/meta.json")}
    task_dirs.update(path.parent for path in root.glob("**/meta.md"))
    task_dirs.update(path.parent.parent for path in root.glob("**/output/report.md"))

    completed = 0
    failed = 0
    incomplete = 0
    for task_dir in task_dirs:
        status = str(_read_json(task_dir / "status.json").get("processing_status", ""))
        if status == "completed" or (task_dir / "output" / "report.md").is_file():
            completed += 1
        elif status == "failed":
            failed += 1
        else:
            incomplete += 1
    legacy = sum(
        (task_dir / "meta.md").is_file() and not (task_dir / "meta.json").is_file()
        for task_dir in task_dirs
    )
    total = len(task_dirs)
    return TaskStatistics(
        total=total,
        completed=completed,
        failed=failed,
        incomplete=incomplete,
        legacy=legacy,
    )


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
    writing_task_stats = summarize_writing_tasks(paths.jobs_dir)
    review_task_stats = summarize_review_tasks(paths.review_tasks_dir)
    writing_job_count = writing_task_stats.total
    review_task_count = review_task_stats.total
    policy_count = _sqlite_table_count(paths.policy_db_path, "policy_documents")
    bank_count = _sqlite_table_count(paths.bank_db_path, "bank_entries")
    repository = repository_summary(project_root)
    recent_changes = tuple(list_recent_changes(project_root, limit=8))
    open_todos = [todo for todo in todos if todo.is_open]
    architecture_layers = tuple(
        _build_architecture_layers(
            project_root=project_root,
            skills=skills,
            todos=todos,
            services=services,
        )
    )
    architecture_relations = tuple(_build_architecture_relations(architecture_layers))
    capability_status_counts = _count_capability_statuses(architecture_layers)
    modules = tuple(
        _build_module_summaries(
            project_root=project_root,
            skills=skills,
            todos=open_todos,
            services=services,
            writing_task_stats=writing_task_stats,
            review_task_stats=review_task_stats,
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
        writing_task_stats=writing_task_stats,
        review_task_stats=review_task_stats,
        policy_count=policy_count,
        bank_count=bank_count,
        repository=repository,
        todos=tuple(todos),
        services=tuple(services),
        architecture_layers=architecture_layers,
        architecture_relations=architecture_relations,
        capability_status_counts=capability_status_counts,
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


def _build_architecture_layers(
    *,
    project_root: Path,
    skills: list[SkillAdminSummary],
    todos: list[TodoAdminSummary],
    services: list[ServiceHealthSummary],
) -> list[ArchitectureLayerSummary]:
    skills_by_id = {skill.id: skill for skill in skills}
    todos_by_id = {todo.todo_id: todo for todo in todos}
    services_by_id = {service.service: service for service in services}
    layers: list[ArchitectureLayerSummary] = []
    for layer_spec in _ARCHITECTURE_LAYER_SPECS:
        capabilities = tuple(
            _build_capability_summary(
                project_root=project_root,
                spec=spec,
                skills_by_id=skills_by_id,
                todos_by_id=todos_by_id,
                services_by_id=services_by_id,
            )
            for spec in layer_spec.capabilities
        )
        layers.append(
            ArchitectureLayerSummary(
                key=layer_spec.key,
                order=layer_spec.order,
                name=layer_spec.name,
                description=layer_spec.description,
                capabilities=capabilities,
            )
        )
    return layers


def _build_capability_summary(
    *,
    project_root: Path,
    spec: _CapabilitySpec,
    skills_by_id: dict[str, SkillAdminSummary],
    todos_by_id: dict[str, TodoAdminSummary],
    services_by_id: dict[str, ServiceHealthSummary],
) -> CapabilityAdminSummary:
    related_todos = [todos_by_id[todo_id] for todo_id in spec.todo_ids if todo_id in todos_by_id]
    evidence_exists = any((project_root / relative_path).exists() for relative_path in spec.evidence_paths)
    status = _resolve_capability_status(
        spec,
        related_todos=related_todos,
        evidence_exists=evidence_exists,
        skills_by_id=skills_by_id,
    )
    next_todo = next((todo for todo in related_todos if todo.is_open), related_todos[0] if related_todos else None)
    service = services_by_id.get(spec.runtime_service)
    runtime_status = service.status if service else ""
    return CapabilityAdminSummary(
        id=spec.id,
        name=spec.name,
        description=spec.description,
        status=status,
        status_label=_CAPABILITY_STATUS_LABELS[status],
        evidence=_capability_evidence(
            project_root=project_root,
            spec=spec,
            next_todo=next_todo,
            skills_by_id=skills_by_id,
        ),
        todo_id=next_todo.todo_id if next_todo else "",
        next_action=next_todo.next_action if next_todo and next_todo.is_open else "",
        runtime_status=runtime_status,
        runtime_label=_RUNTIME_STATUS_LABELS.get(runtime_status, ""),
    )


def _build_architecture_relations(
    layers: tuple[ArchitectureLayerSummary, ...],
) -> list[ArchitectureRelationSummary]:
    capability_ids = {
        capability.id
        for layer in layers
        for capability in layer.capabilities
    }
    relations: list[ArchitectureRelationSummary] = []
    for source_id, target_id, label in _ARCHITECTURE_RELATION_SPECS:
        unknown_ids = {source_id, target_id} - capability_ids
        if unknown_ids:
            raise ValueError(f"架构关系引用了未知能力：{', '.join(sorted(unknown_ids))}")
        relations.append(
            ArchitectureRelationSummary(
                source_id=source_id,
                target_id=target_id,
                label=label,
            )
        )
    return relations


def _resolve_capability_status(
    spec: _CapabilitySpec,
    *,
    related_todos: list[TodoAdminSummary],
    evidence_exists: bool,
    skills_by_id: dict[str, SkillAdminSummary],
) -> str:
    if spec.skill_ids:
        related_skills = [skills_by_id[skill_id] for skill_id in spec.skill_ids if skill_id in skills_by_id]
        if not related_skills:
            return "planned"
        if not any(skill.enabled for skill in related_skills):
            return "disabled"

    if spec.todo_policy == "optimize":
        if any(todo.status in {"未开始", "进行中"} for todo in related_todos):
            return "optimizing"
        return spec.default_status

    if spec.todo_policy == "work" and related_todos:
        statuses = {todo.status for todo in related_todos}
        if "进行中" in statuses:
            return "building"
        if "已暂缓" in statuses:
            return "paused"
        if "未开始" in statuses:
            if spec.build_when_evidence_exists and evidence_exists:
                return "building"
            return "planned"
        if "已完成" in statuses:
            return "stable"
    return spec.default_status


def _capability_evidence(
    *,
    project_root: Path,
    spec: _CapabilitySpec,
    next_todo: TodoAdminSummary | None,
    skills_by_id: dict[str, SkillAdminSummary],
) -> str:
    evidence: list[str] = []
    related_skills = [skills_by_id[skill_id] for skill_id in spec.skill_ids if skill_id in skills_by_id]
    if related_skills:
        enabled_count = sum(1 for skill in related_skills if skill.enabled)
        evidence.append(f"Skill {enabled_count}/{len(related_skills)} 已启用")
    existing_paths = [path for path in spec.evidence_paths if (project_root / path).exists()]
    if existing_paths:
        evidence.append("代码：" + "、".join(existing_paths[:2]))
    if next_todo:
        evidence.append(f"{next_todo.todo_id}（{next_todo.status}）")
    return " · ".join(evidence) or "项目架构基线"


def _count_capability_statuses(
    layers: tuple[ArchitectureLayerSummary, ...],
) -> dict[str, int]:
    counts = {status: 0 for status in _CAPABILITY_STATUS_LABELS}
    for layer in layers:
        for capability in layer.capabilities:
            counts[capability.status] += 1
    return counts


def _build_module_summaries(
    *,
    project_root: Path,
    skills: list[SkillAdminSummary],
    todos: list[TodoAdminSummary],
    services: list[ServiceHealthSummary],
    writing_task_stats: TaskStatistics,
    review_task_stats: TaskStatistics,
    policy_count: int | None,
    bank_count: int | None,
) -> list[ModuleAdminSummary]:
    enabled_skills = [skill.name for skill in skills if skill.enabled]
    healthy_services = sum(1 for service in services if service.status == "healthy")
    service_total = len(services)
    writing_summary = (
        f"已启用 {len(enabled_skills)} 个 skill；累计创建 {writing_task_stats.total} 个写作任务，"
        f"完成成稿 {writing_task_stats.completed} 个，待补充 {writing_task_stats.needs_input} 个，"
        f"失败 {writing_task_stats.failed} 个，处理中或中断 {writing_task_stats.incomplete} 个，"
        f"历史状态待补齐 {writing_task_stats.unknown} 个。"
    )
    review_summary = (
        f"独立审核 Bot 继续运行；累计归档 {review_task_stats.total} 个审核任务，"
        f"已生成审核报告 {review_task_stats.completed} 个，失败 {review_task_stats.failed} 个，"
        f"未形成报告 {review_task_stats.incomplete} 个"
        f"（含旧格式历史归档 {review_task_stats.legacy} 个）。"
    )
    current_summaries = {
        "platform": "统一路由、权限、ToolGateway、会话和 DOCX/PDF/PPTX 文档服务已可用。",
        "writing": writing_summary,
        "review": review_summary,
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
        for marker in ("待补", "待完成", "尚未完成"):
            marker_index = goal.find(marker)
            if marker_index >= 0:
                return goal[marker_index:]
        if goal.startswith("下一步"):
            return goal
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
