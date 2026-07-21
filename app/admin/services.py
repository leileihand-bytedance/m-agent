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

from app.review.capabilities import REVIEW_CAPABILITIES, infer_review_capability
from app.review.task_execution import REVIEW_TASK_TYPES
from app.writing.task_execution import QUEUEABLE_WRITING_SKILLS


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
    execution_mode: str = ""
    execution_mode_label: str = ""


@dataclass(frozen=True)
class ComponentGroupSummary:
    key: str
    order: str
    name: str
    description: str
    capabilities: tuple[CapabilityAdminSummary, ...]


@dataclass(frozen=True)
class ArchitectureNodeSummary:
    id: str
    name: str
    description: str
    plane: str
    plane_name: str
    group: str
    group_name: str
    evidence: str
    x: int
    y: int


@dataclass(frozen=True)
class ArchitectureRelationSummary:
    source_id: str
    target_id: str
    label: str
    relation_type: str = "runtime"


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
class ReviewCapabilityStatistics:
    capability_id: str
    capability_name: str
    total: int = 0
    completed: int = 0
    failed: int = 0
    incomplete: int = 0
    delivered: int = 0
    delivery_failed: int = 0
    model_calls: int = 0
    model_failures: int = 0
    finding_count: int = 0
    total_elapsed_ms: float = 0.0
    elapsed_sample_count: int = 0

    @property
    def average_elapsed_ms(self) -> float:
        if self.elapsed_sample_count == 0:
            return 0.0
        return self.total_elapsed_ms / self.elapsed_sample_count


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
    review_capability_stats: tuple[ReviewCapabilityStatistics, ...]
    policy_count: int | None
    bank_count: int | None
    repository: RepositoryAdminSummary
    todos: tuple[TodoAdminSummary, ...]
    services: tuple[ServiceHealthSummary, ...]
    architecture_nodes: tuple[ArchitectureNodeSummary, ...]
    architecture_relations: tuple[ArchitectureRelationSummary, ...]
    component_groups: tuple[ComponentGroupSummary, ...]
    component_status_counts: dict[str, int]
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
class _ComponentGroupSpec:
    key: str
    order: str
    name: str
    description: str
    capabilities: tuple[_CapabilitySpec, ...]


@dataclass(frozen=True)
class _ArchitectureNodeSpec:
    id: str
    name: str
    description: str
    plane: str
    group: str
    evidence_paths: tuple[str, ...]
    x: int
    y: int


_TODO_HEADING_RE = re.compile(r"^###\s+(TODO-\d{3})[：:]\s*(.+?)\s*$", re.MULTILINE)
_CLOSED_TODO_STATUSES = {"已完成", "已取消", "已暂缓"}
_SERVICE_LABELS = (
    ("writing_bot", "写作 Bot"),
    ("review_bot", "审核 Bot"),
    ("rewrite_bot", "材料润色 Bot"),
    ("ops_bot", "运维 Bot"),
)
_MODULE_SPECS = (
    ("platform", "底座", ("底座", "企业微信入口"), ("app/platform", "docs/agent-platform")),
    (
        "writing",
        "写作",
        ("直报", "简报", "写作", "功能区"),
        (
            "app/writing",
            "app/rewrite_bot",
            "skills/direct_report",
            "skills/writer1",
            "skills/rewrite",
            "skills/research_synthesis",
            "skills/shenyinxie_news",
            "skills/internal_weekly",
        ),
    ),
    ("review", "审核", ("审核",), ("app/review", "tests/test_review", "tests/test_official_format_review.py")),
    ("knowledge", "知识库", ("政策知识库", "微众银行信息库", "知识库"), ("app/policy_knowledge", "app/bank_knowledge", "docs/knowledge/policy.md", "docs/knowledge/bank.md")),
    (
        "operations",
        "入口与运维",
        ("运维", "企业微信入口"),
        ("app/platform/ops", "app/writing/bot.py", "app/review/main.py", "app/rewrite_bot/bot.py"),
    ),
    ("admin", "管理后台", ("管理后台",), ("app/admin", "docs/operations/admin-console.md")),
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
_EXECUTION_MODE_LABELS = {
    "persistent": "持久队列",
    "realtime": "实时执行",
}
_COMPONENT_GROUP_SPECS = (
    _ComponentGroupSpec(
        key="entry",
        order="01",
        name="业务入口",
        description="业务用户提交写作、审核和润色需求的入口。",
        capabilities=(
            _CapabilitySpec(
                "writing_bot",
                "写作 Bot",
                "接收直报、简报、综合调研、深银协动态、内参周报和连续改稿请求。",
                "optimizing",
                ("app/writing/bot.py",),
                ("TODO-001", "TODO-002", "TODO-003"),
                "optimize",
                runtime_service="writing_bot",
            ),
            _CapabilitySpec(
                "rewrite_bot",
                "材料润色 Bot",
                "独立接收文字润色请求，先确认修改方向并隔离其他写作会话。",
                "optimizing",
                ("app/rewrite_bot/bot.py", "app/rewrite_bot/intake.py"),
                skill_ids=("rewrite",),
                runtime_service="rewrite_bot",
            ),
            _CapabilitySpec(
                "review_bot",
                "审核 Bot",
                "保持独立入口，承载文字、Word、HTML、PPTX、格式和多文件审核。",
                "optimizing",
                ("app/review/main.py",),
                ("TODO-023", "TODO-021", "TODO-020"),
                "optimize",
                runtime_service="review_bot",
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
    _ComponentGroupSpec(
        key="platform",
        order="02",
        name="智能体底座",
        description="控制任务如何安全接收、路由、执行、恢复和保存状态。",
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
                "task_relations",
                "任务关系与多任务卡片",
                "按用户维护多项任务，识别续改、补料、派生、新建、追问恢复、切换和取消。",
                "stable",
                ("app/platform/task_relations.py",),
            ),
            _CapabilitySpec(
                "task_intake",
                "公共任务组装",
                "共用安全暂存、恢复、文件限制和结构化任务提交协议。",
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
                ("app/platform/task_execution.py",),
                todo_ids=("TODO-027",),
                todo_policy="work",
                build_when_evidence_exists=True,
            ),
            _CapabilitySpec(
                "task_files",
                "任务状态与文件存储",
                "为任务、会话和结果提供统一目录、状态索引和受限文件边界。",
                "stable",
                ("app/platform/data_paths.py", "app/platform/storage.py"),
            ),
        ),
    ),
    _ComponentGroupSpec(
        key="capabilities",
        order="03",
        name="业务能力",
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
                ("skills/writer1",),
                ("TODO-002",),
                "optimize",
                ("writer1",),
            ),
            _CapabilitySpec(
                "research_synthesis",
                "综合调研整合",
                "按用户提纲建立证据台账，跨部门整合材料并生成 Word 初稿。",
                "optimizing",
                ("skills/research_synthesis",),
                ("TODO-027",),
                "optimize",
                ("research_synthesis",),
            ),
            _CapabilitySpec(
                "rewrite",
                "材料润色",
                "对用户直接粘贴的初稿做独立润色和后续修改。",
                "optimizing",
                ("skills/rewrite",),
                skill_ids=("rewrite",),
            ),
            _CapabilitySpec(
                "shenyinxie_news",
                "深银协动态",
                "按指定半月检索权威报道，筛选微众银行成果并生成带来源的 Word。",
                "stable",
                ("skills/shenyinxie_news",),
                ("TODO-030",),
                "optimize",
                ("shenyinxie_news",),
            ),
            _CapabilitySpec(
                "internal_weekly",
                "内参周报",
                "检索上一自然周公开信息，生成带溯源清单的内容核对稿；Word 模板输出待后续阶段接入。",
                "optimizing",
                ("skills/internal_weekly",),
                skill_ids=("internal_weekly",),
            ),
            _CapabilitySpec(
                "general_text_review",
                "通用文字审核",
                "检查直接发送文字中的低级错误、语病和逻辑一致性并返回文字结果。",
                "stable",
                ("app/review/general_reviewer.py",),
                ("TODO-023",),
                "optimize",
            ),
            _CapabilitySpec(
                "general_word_review",
                "通用 Word 审核",
                "检查 Word 中的低级错误、语病和逻辑一致性并返回标注文档。",
                "stable",
                ("app/review/general_reviewer.py", "app/review/error_marker.py"),
                ("TODO-023",),
                "optimize",
            ),
            _CapabilitySpec(
                "html_review",
                "静态 HTML 审核",
                "安全提取可见文字，复用通用规则并按网页 PPT 页码或段落返回问题。",
                "stable",
                ("app/review/html_parser.py", "app/review/general_reviewer.py"),
                ("TODO-029",),
                "work",
            ),
            _CapabilitySpec(
                "neican_review",
                "内参审核",
                "执行内参两阶段内容审核并保留专属规则和输出方式。",
                "stable",
                ("app/review/reviewer.py",),
                ("TODO-031",),
                "optimize",
            ),
            _CapabilitySpec(
                "halfmonthly_review",
                "半月报审核",
                "执行半月报内容、板块顺序和领导职务等专属审核。",
                "stable",
                ("app/review/halfmonthly_reviewer.py",),
                ("TODO-031",),
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
                "stable",
                ("app/review/multi_file_reviewer.py",),
                ("TODO-021",),
                "optimize",
            ),
            _CapabilitySpec(
                "ppt_review",
                "单份 PPTX 低级错误审核",
                "审核单份 PPTX 的可编辑文字、表格、可读图表和内部一致性，不检查视觉版式。",
                "stable",
                ("app/review/ppt/reviewer.py",),
                todo_ids=("TODO-020",),
                todo_policy="optimize",
            ),
        ),
    ),
    _ComponentGroupSpec(
        key="domain_components",
        order="04",
        name="领域公共组件",
        description="业务域内部复用的审核与成稿处理组件，不作为用户可直接选择的能力。",
        capabilities=(
            _CapabilitySpec(
                "shared_review_core",
                "审核共享核心",
                "统一问题结构、模型调用、证据校验、去重和运行指标，专属业务规则继续隔离。",
                "optimizing",
                todo_ids=("TODO-031",),
                todo_policy="optimize",
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
    _ComponentGroupSpec(
        key="resources",
        order="05",
        name="共享工具服务",
        description="业务能力通过受限授权调用的文档、网页和结果交付服务。",
        capabilities=(
            _CapabilitySpec(
                "document_service",
                "统一文档服务",
                "安全解析 DOCX、PDF、PPTX，按需 OCR 并生成逐页渲染资产。",
                "building",
                ("app/platform/documents/service.py", "app/platform/documents/enrichment.py"),
                ("TODO-024",),
                "work",
                build_when_evidence_exists=True,
            ),
            _CapabilitySpec(
                "web_tools",
                "网页读取与联网搜索",
                "通过受限网页读取和 DeepSeek 原生搜索获取公开素材并核验原文。",
                "stable",
                ("app/platform/builtin_tools.py",),
                ("TODO-012",),
                "optimize",
            ),
            _CapabilitySpec(
                "attachment_delivery",
                "结果与附件交付",
                "统一处理文字和附件回传、目录校验、串行上传、超时、重试和交付状态。",
                "planned",
                ("app/platform/attachment_delivery.py",),
                todo_ids=("TODO-017",),
                todo_policy="work",
                build_when_evidence_exists=True,
            ),
        ),
    ),
    _ComponentGroupSpec(
        key="knowledge",
        order="06",
        name="知识资产",
        description="经过筛选、可持续更新并由业务能力检索使用的内部知识。",
        capabilities=(
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
            _CapabilitySpec(
                "policy_admin",
                "政策库可视化治理",
                "查看、筛选、备份并勾选删除政策数据。",
                "planned",
                todo_ids=("TODO-007",),
                todo_policy="work",
            ),
        ),
    ),
    _ComponentGroupSpec(
        key="operations",
        order="07",
        name="管理与治理",
        description="面向管理员的运行监控、数据治理和研发交付保障。",
        capabilities=(
            _CapabilitySpec(
                "admin_console",
                "本机项目控制台",
                "查看项目状态并管理 Skill、权限和治理入口。",
                "stable",
                ("app/admin/server.py",),
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
                "logs_metrics",
                "日志、指标与运行状态",
                "记录不含业务正文的运行状态、指标，并按权限保留开发期对话日志。",
                "stable",
                ("app/platform/chat_log.py", "app/platform/task_status.py", "app/review/observability.py"),
            ),
            _CapabilitySpec(
                "ops_monitoring",
                "告警、心跳与日报",
                "汇总 Bot 心跳，并向管理员发送已配置服务的异常和工作日日报。",
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
        ),
    ),
)

_ARCHITECTURE_PLANE_LABELS = {
    "runtime": "业务运行面",
    "governance": "管理与治理面",
}
_ARCHITECTURE_GROUP_LABELS = {
    "entry": "业务入口",
    "platform": "智能体底座",
    "capabilities": "业务能力",
    "services": "共享工具服务",
    "knowledge": "知识资产",
    "governance": "管理与治理",
}
_ARCHITECTURE_NODE_SPECS = (
    _ArchitectureNodeSpec(
        "business_entry",
        "业务入口",
        "接收写作、审核和润色请求，并把结果返回对应企业微信会话。",
        "runtime",
        "entry",
        ("app/writing/bot.py", "app/review/main.py", "app/rewrite_bot/bot.py"),
        -620,
        0,
    ),
    _ArchitectureNodeSpec(
        "platform_access",
        "接入与权限",
        "标准化入口消息，识别用户身份并限制可用能力。",
        "runtime",
        "platform",
        ("app/platform/gateway/wecom.py", "app/platform/identity.py"),
        -420,
        -160,
    ),
    _ArchitectureNodeSpec(
        "platform_orchestration",
        "任务编排与上下文",
        "组装多条消息，完成路由、会话版本、任务排队和重启恢复。",
        "runtime",
        "platform",
        ("app/platform/intake.py", "app/platform/router.py", "app/platform/conversation.py", "app/platform/task_execution.py"),
        -420,
        0,
    ),
    _ArchitectureNodeSpec(
        "agent_runtime",
        "Agent 运行与授权",
        "调用 Pydantic AI 和模型，只向 Skill 暴露已授权工具。",
        "runtime",
        "platform",
        ("app/platform/runtime.py", "app/platform/pydantic_runtime.py", "app/platform/tools.py"),
        -420,
        160,
    ),
    _ArchitectureNodeSpec(
        "writing_domain",
        "写作能力",
        "承载直报、简报、综合调研、深银协动态、内参周报和材料润色。",
        "runtime",
        "capabilities",
        ("skills", "app/writing"),
        -220,
        -100,
    ),
    _ArchitectureNodeSpec(
        "review_domain",
        "审核能力",
        "承载八类审核及审核领域内部共享核心。",
        "runtime",
        "capabilities",
        ("app/review",),
        -220,
        170,
    ),
    _ArchitectureNodeSpec(
        "direct_report",
        "直报写作",
        "根据单条或多条素材形成问题鲜明、建议可执行的直报，并支持连续改稿。",
        "runtime",
        "capabilities",
        ("skills/direct_report",),
        0,
        -250,
    ),
    _ArchitectureNodeSpec(
        "brief_writing",
        "简报写作",
        "处理单素材和多素材简报，结合政策与微众银行信息形成规范稿件。",
        "runtime",
        "capabilities",
        ("skills/writer1",),
        0,
        -160,
    ),
    _ArchitectureNodeSpec(
        "rewrite",
        "材料润色",
        "围绕用户已有初稿和修改要求进行独立润色及后续改稿。",
        "runtime",
        "capabilities",
        ("skills/rewrite", "app/rewrite_bot"),
        0,
        -70,
    ),
    _ArchitectureNodeSpec(
        "thematic_content",
        "专题内容生产",
        "覆盖综合调研、深银协动态和内参周报等有专门规则的内容生产。",
        "runtime",
        "capabilities",
        ("skills/research_synthesis", "skills/shenyinxie_news", "skills/internal_weekly"),
        0,
        20,
    ),
    _ArchitectureNodeSpec(
        "general_review",
        "通用内容审核",
        "审核文字、Word、静态 HTML 和单份 PPTX 中的内容问题。",
        "runtime",
        "capabilities",
        ("app/review/general_reviewer.py", "app/review/html_parser.py", "app/review/ppt"),
        0,
        120,
    ),
    _ArchitectureNodeSpec(
        "special_review",
        "专类材料审核",
        "按内参、半月报等材料的专属结构和口径执行审核。",
        "runtime",
        "capabilities",
        ("app/review/reviewer.py", "app/review/halfmonthly_reviewer.py"),
        0,
        210,
    ),
    _ArchitectureNodeSpec(
        "format_review",
        "公文格式审核",
        "按明确指令检查字体、字号、标题层级和页面设置。",
        "runtime",
        "capabilities",
        ("app/review/official_format_checker.py",),
        0,
        300,
    ),
    _ArchitectureNodeSpec(
        "multi_file_review",
        "多文件联合审核",
        "联合检查正文、附件引用和跨文件矛盾。",
        "runtime",
        "capabilities",
        ("app/review/multi_file_reviewer.py",),
        0,
        390,
    ),
    _ArchitectureNodeSpec(
        "document_service",
        "统一文档服务",
        "统一解析 DOCX、PDF、PPTX，并按需提供 OCR 和页面渲染。",
        "runtime",
        "services",
        ("app/platform/documents",),
        220,
        -160,
    ),
    _ArchitectureNodeSpec(
        "web_retrieval",
        "网页读取与联网搜索",
        "在公网和来源规则边界内搜索、读取并核验公开材料。",
        "runtime",
        "services",
        ("app/platform/builtin_tools.py",),
        220,
        0,
    ),
    _ArchitectureNodeSpec(
        "result_delivery",
        "结果与附件交付",
        "统一回传文字和附件，并维护交付检查点与失败告警。",
        "runtime",
        "services",
        ("app/platform/attachment_delivery.py",),
        220,
        160,
    ),
    _ArchitectureNodeSpec(
        "policy_knowledge",
        "政策知识库",
        "提供经过筛选的监管和宏观政策背景。",
        "runtime",
        "knowledge",
        ("app/policy_knowledge", "docs/knowledge/policy.md"),
        440,
        -80,
    ),
    _ArchitectureNodeSpec(
        "bank_knowledge",
        "微众银行信息库",
        "提供银行自身产品、数据和事实口径。",
        "runtime",
        "knowledge",
        ("app/bank_knowledge", "docs/knowledge/bank.md"),
        440,
        80,
    ),
    _ArchitectureNodeSpec(
        "admin_console",
        "本机管理台",
        "集中查看架构、能力状态、运行健康和治理入口。",
        "governance",
        "governance",
        ("app/admin",),
        -420,
        560,
    ),
    _ArchitectureNodeSpec(
        "ops_observability",
        "运维与可观测性",
        "汇总日志、指标、心跳、告警和工作日日报。",
        "governance",
        "governance",
        ("app/platform/ops", "app/platform/task_status.py", "app/review/observability.py"),
        -210,
        560,
    ),
    _ArchitectureNodeSpec(
        "data_governance",
        "运行数据治理",
        "管理任务文件、备份、保留周期和清理边界。",
        "governance",
        "governance",
        ("app/platform/data_paths.py", "app/platform/storage.py"),
        0,
        560,
    ),
    _ArchitectureNodeSpec(
        "engineering_governance",
        "研发交付治理",
        "通过自动化测试、文档闸门和受管 Git 流程保证交付可追溯。",
        "governance",
        "governance",
        ("scripts/project_docs.py", "docs/development/testing-and-delivery.md"),
        210,
        560,
    ),
    _ArchitectureNodeSpec(
        "knowledge_governance",
        "知识库治理",
        "管理知识来源、更新、质量筛选、备份和可视化维护。",
        "governance",
        "governance",
        ("docs/knowledge",),
        420,
        560,
    ),
)

_ARCHITECTURE_RELATION_SPECS = (
    ("business_entry", "platform_access", "提交请求", "runtime"),
    ("platform_access", "platform_orchestration", "授权并组装", "runtime"),
    ("platform_orchestration", "agent_runtime", "调度执行", "runtime"),
    ("agent_runtime", "writing_domain", "执行写作", "runtime"),
    ("agent_runtime", "review_domain", "执行审核", "runtime"),
    ("writing_domain", "direct_report", "编排直报", "runtime"),
    ("writing_domain", "brief_writing", "编排简报", "runtime"),
    ("writing_domain", "rewrite", "编排润色", "runtime"),
    ("writing_domain", "thematic_content", "编排专题任务", "runtime"),
    ("review_domain", "general_review", "编排通用审核", "runtime"),
    ("review_domain", "special_review", "编排专类审核", "runtime"),
    ("review_domain", "format_review", "编排格式审核", "runtime"),
    ("review_domain", "multi_file_review", "编排联合审核", "runtime"),
    ("writing_domain", "document_service", "读取材料", "runtime"),
    ("review_domain", "document_service", "读取材料", "runtime"),
    ("writing_domain", "web_retrieval", "检索与核验", "runtime"),
    ("policy_knowledge", "writing_domain", "提供政策背景", "runtime"),
    ("bank_knowledge", "writing_domain", "提供银行事实", "runtime"),
    ("writing_domain", "result_delivery", "交付结果", "runtime"),
    ("review_domain", "result_delivery", "交付结果", "runtime"),
    ("result_delivery", "business_entry", "返回结果", "runtime"),
    ("admin_console", "ops_observability", "查看运行", "governance"),
    ("admin_console", "data_governance", "查看数据", "governance"),
    ("admin_console", "engineering_governance", "查看交付", "governance"),
    ("admin_console", "knowledge_governance", "管理知识", "governance"),
    ("ops_observability", "business_entry", "监控入口", "governance"),
    ("ops_observability", "agent_runtime", "监控运行", "governance"),
    ("data_governance", "platform_orchestration", "治理任务数据", "governance"),
    ("engineering_governance", "agent_runtime", "约束交付", "governance"),
    ("knowledge_governance", "policy_knowledge", "治理政策库", "governance"),
    ("knowledge_governance", "bank_knowledge", "治理银行信息库", "governance"),
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
        elif status in {"queued", "running", "processing", "incomplete"}:
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


def summarize_review_capabilities(
    root: Path | None,
) -> tuple[ReviewCapabilityStatistics, ...]:
    counters: dict[str, dict[str, int | float]] = {
        capability.id: {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "incomplete": 0,
            "delivered": 0,
            "delivery_failed": 0,
            "model_calls": 0,
            "model_failures": 0,
            "finding_count": 0,
            "total_elapsed_ms": 0.0,
            "elapsed_sample_count": 0,
        }
        for capability in REVIEW_CAPABILITIES
    }
    if root is not None and root.exists():
        for meta_path in root.glob("**/meta.json"):
            task_dir = meta_path.parent
            meta = _read_json(meta_path)
            capability = infer_review_capability(meta)
            if capability is None:
                continue
            counter = counters[capability.id]
            counter["total"] += 1

            status = _read_json(task_dir / "status.json")
            processing_status = str(status.get("processing_status", ""))
            if processing_status == "completed" or (task_dir / "output" / "report.md").is_file():
                counter["completed"] += 1
            elif processing_status == "failed":
                counter["failed"] += 1
            else:
                counter["incomplete"] += 1

            delivery_status = str(status.get("delivery_status", ""))
            if delivery_status == "delivered":
                counter["delivered"] += 1
            elif delivery_status == "failed":
                counter["delivery_failed"] += 1

            observability = meta.get("observability", {})
            if not isinstance(observability, dict):
                observability = {}
            counter["model_calls"] += _non_negative_int(observability.get("model_calls"))
            counter["model_failures"] += _non_negative_int(
                observability.get("model_failures")
            )
            counter["finding_count"] += _non_negative_int(
                observability.get("finding_count", meta.get("finding_count", 0))
            )
            elapsed_ms = _non_negative_float(observability.get("elapsed_ms"))
            if elapsed_ms is not None:
                counter["total_elapsed_ms"] += elapsed_ms
                counter["elapsed_sample_count"] += 1

    return tuple(
        ReviewCapabilityStatistics(
            capability_id=capability.id,
            capability_name=capability.name,
            **counters[capability.id],
        )
        for capability in REVIEW_CAPABILITIES
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
    review_capability_stats = summarize_review_capabilities(paths.review_tasks_dir)
    writing_job_count = writing_task_stats.total
    review_task_count = review_task_stats.total
    policy_count = _sqlite_table_count(paths.policy_db_path, "policy_documents")
    bank_count = _sqlite_table_count(paths.bank_db_path, "bank_entries")
    repository = repository_summary(project_root)
    recent_changes = tuple(list_recent_changes(project_root, limit=8))
    open_todos = [todo for todo in todos if todo.is_open]
    component_groups = tuple(
        _build_component_groups(
            project_root=project_root,
            skills=skills,
            todos=todos,
            services=services,
        )
    )
    architecture_nodes = tuple(_build_architecture_nodes(project_root))
    architecture_relations = tuple(_build_architecture_relations(architecture_nodes))
    component_status_counts = _count_capability_statuses(component_groups)
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
        review_capability_stats=review_capability_stats,
        policy_count=policy_count,
        bank_count=bank_count,
        repository=repository,
        todos=tuple(todos),
        services=tuple(services),
        architecture_nodes=architecture_nodes,
        architecture_relations=architecture_relations,
        component_groups=component_groups,
        component_status_counts=component_status_counts,
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


def _build_component_groups(
    *,
    project_root: Path,
    skills: list[SkillAdminSummary],
    todos: list[TodoAdminSummary],
    services: list[ServiceHealthSummary],
) -> list[ComponentGroupSummary]:
    skills_by_id = {skill.id: skill for skill in skills}
    todos_by_id = {todo.todo_id: todo for todo in todos}
    services_by_id = {service.service: service for service in services}
    groups: list[ComponentGroupSummary] = []
    for group_spec in _COMPONENT_GROUP_SPECS:
        capabilities = tuple(
            _build_capability_summary(
                project_root=project_root,
                spec=spec,
                skills_by_id=skills_by_id,
                todos_by_id=todos_by_id,
                services_by_id=services_by_id,
            )
            for spec in group_spec.capabilities
        )
        groups.append(
            ComponentGroupSummary(
                key=group_spec.key,
                order=group_spec.order,
                name=group_spec.name,
                description=group_spec.description,
                capabilities=capabilities,
            )
        )
    return groups


def _build_architecture_nodes(project_root: Path) -> list[ArchitectureNodeSummary]:
    nodes: list[ArchitectureNodeSummary] = []
    for spec in _ARCHITECTURE_NODE_SPECS:
        evidence = "、".join(
            relative_path
            for relative_path in spec.evidence_paths
            if (project_root / relative_path).exists()
        )
        nodes.append(
            ArchitectureNodeSummary(
                id=spec.id,
                name=spec.name,
                description=spec.description,
                plane=spec.plane,
                plane_name=_ARCHITECTURE_PLANE_LABELS[spec.plane],
                group=spec.group,
                group_name=_ARCHITECTURE_GROUP_LABELS[spec.group],
                evidence=f"代码/文档：{evidence}" if evidence else "规划结构",
                x=spec.x,
                y=spec.y,
            )
        )
    return nodes


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
    execution_mode = _capability_execution_mode(spec)
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
        execution_mode=execution_mode,
        execution_mode_label=_EXECUTION_MODE_LABELS.get(execution_mode, ""),
    )


def _capability_execution_mode(spec: _CapabilitySpec) -> str:
    if spec.skill_ids:
        return (
            "persistent"
            if all(skill_id in QUEUEABLE_WRITING_SKILLS for skill_id in spec.skill_ids)
            else "realtime"
        )
    review_capability = next(
        (item for item in REVIEW_CAPABILITIES if item.id == spec.id),
        None,
    )
    if review_capability is None:
        return ""
    return (
        "persistent"
        if review_capability.task_type in REVIEW_TASK_TYPES
        else "realtime"
    )


def _build_architecture_relations(
    nodes: tuple[ArchitectureNodeSummary, ...],
) -> list[ArchitectureRelationSummary]:
    node_ids = {node.id for node in nodes}
    relations: list[ArchitectureRelationSummary] = []
    for source_id, target_id, label, relation_type in _ARCHITECTURE_RELATION_SPECS:
        unknown_ids = {source_id, target_id} - node_ids
        if unknown_ids:
            raise ValueError(f"架构关系引用了未知节点：{', '.join(sorted(unknown_ids))}")
        relations.append(
            ArchitectureRelationSummary(
                source_id=source_id,
                target_id=target_id,
                label=label,
                relation_type=relation_type,
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
    groups: tuple[ComponentGroupSummary, ...],
) -> dict[str, int]:
    counts = {status: 0 for status in _CAPABILITY_STATUS_LABELS}
    for group in groups:
        for capability in group.capabilities:
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
        "operations": f"写作、审核、润色、运维共 {service_total} 个服务，当前 {healthy_services} 个心跳正常。",
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


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _non_negative_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, number)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
