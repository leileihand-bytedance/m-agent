from pathlib import Path
from datetime import datetime
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.services import (  # noqa: E402
    _COMPONENT_GROUP_SPECS,
    AdminPaths,
    build_project_overview,
    list_jobs,
    list_policy_users,
    list_service_health,
    list_skills,
    list_todos,
    set_skill_enabled,
    set_user_skills,
    summarize_review_capabilities,
    summarize_review_tasks,
    summarize_writing_tasks,
)


def test_review_capability_statistics_are_independent_and_include_delivery(tmp_path):
    root = tmp_path / "review"
    word_task = root / "2026" / "07" / "word-task"
    ppt_task = root / "2026" / "07" / "ppt-task"
    for task_dir in (word_task, ppt_task):
        task_dir.mkdir(parents=True)

    (word_task / "meta.json").write_text(
        json.dumps(
            {
                "capability_id": "general_word_review",
                "observability": {
                    "elapsed_ms": 2500,
                    "model_calls": 3,
                    "model_failures": 1,
                    "finding_count": 4,
                },
            }
        ),
        encoding="utf-8",
    )
    (word_task / "status.json").write_text(
        json.dumps(
            {"processing_status": "completed", "delivery_status": "delivered"}
        ),
        encoding="utf-8",
    )
    (ppt_task / "meta.json").write_text(
        json.dumps(
            {
                "task_type": "review_pptx",
                "observability": {
                    "elapsed_ms": 1000,
                    "model_calls": 2,
                    "model_failures": 0,
                    "finding_count": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    (ppt_task / "status.json").write_text(
        json.dumps({"processing_status": "failed", "delivery_status": "failed"}),
        encoding="utf-8",
    )

    by_id = {item.capability_id: item for item in summarize_review_capabilities(root)}

    assert len(by_id) == 8
    assert by_id["general_word_review"].total == 1
    assert by_id["general_word_review"].completed == 1
    assert by_id["general_word_review"].delivered == 1
    assert by_id["general_word_review"].average_elapsed_ms == 2500
    assert by_id["general_word_review"].model_calls == 3
    assert by_id["general_word_review"].model_failures == 1
    assert by_id["general_word_review"].finding_count == 4
    assert by_id["ppt_review"].failed == 1
    assert by_id["ppt_review"].delivery_failed == 1
    assert by_id["general_text_review"].total == 0


def _write_skill(root: Path, skill_id: str, *, enabled: bool) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"# {skill_id}\n\n说明文字", encoding="utf-8")
    (skill_dir / "config.yaml").write_text(
        "\n".join(
            [
                f"id: {skill_id}",
                f"name: {skill_id} 名称",
                "description: 测试 skill",
                f"enabled: {'true' if enabled else 'false'}",
                "triggers:",
                "  - 测试",
                "allowed_tools:",
                "  - web_reader",
                "workflow: skills.test.workflow:run",
            ]
        ),
        encoding="utf-8",
    )


def test_list_skills_reads_configs_and_skill_preview(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)
    _write_skill(skills_dir, "writer1", enabled=False)

    skills = list_skills(skills_dir)

    assert [skill.id for skill in skills] == ["direct_report", "writer1"]
    assert skills[0].enabled is True
    assert skills[1].enabled is False
    assert skills[0].triggers == ["测试"]
    assert skills[0].allowed_tools == ["web_reader"]
    assert "说明文字" in skills[0].skill_preview


def test_set_skill_enabled_updates_yaml_without_touching_other_fields(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "writer1", enabled=False)

    set_skill_enabled(skills_dir, "writer1", True)

    skills = list_skills(skills_dir)
    assert skills[0].enabled is True
    config_text = (skills_dir / "writer1" / "config.yaml").read_text(encoding="utf-8")
    assert "workflow: skills.test.workflow:run" in config_text


def test_policy_users_can_be_listed_and_updated(tmp_path):
    policy_path = tmp_path / "platform-policy.yaml"
    policy_path.write_text(
        "\n".join(
            [
                "allow_unknown_users: false",
                "default_allowed_skills: []",
                "users:",
                "  test-user:",
                "    allowed_skills:",
                "      - direct_report",
            ]
        ),
        encoding="utf-8",
    )

    users = list_policy_users(policy_path)
    assert users == {"test-user": ["direct_report"]}

    set_user_skills(policy_path, "test-user", ["direct_report", "writer1"])
    set_user_skills(policy_path, "new-user", ["writer1"])

    users = list_policy_users(policy_path)
    assert users["test-user"] == ["direct_report", "writer1"]
    assert users["new-user"] == ["writer1"]


def test_list_jobs_reads_recent_job_meta_and_result(tmp_path):
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "20260703-001"
    output_dir = job_dir / "output"
    output_dir.mkdir(parents=True)
    (job_dir / "meta.json").write_text(
        json.dumps(
            {
                "job_id": "20260703-001",
                "channel": "wecom",
                "sender_userid": "user-001",
                "created_at": "2026-07-03 20:31:32",
                "message_preview": "写直报",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (output_dir / "result.json").write_text(
        json.dumps(
            {
                "skill_id": "direct_report",
                "needs_clarification": False,
                "message": "已生成",
                "output": {"title": "标题"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    jobs = list_jobs(AdminPaths(skills_dir=tmp_path / "skills", policy_path=tmp_path / "policy.yaml", jobs_dir=jobs_dir))

    assert len(jobs) == 1
    assert jobs[0].job_id == "20260703-001"
    assert jobs[0].skill_id == "direct_report"
    assert jobs[0].title == "标题"


def test_summarize_writing_tasks_uses_content_free_status_files(tmp_path):
    root = tmp_path / "writing"

    def write_job(job_id: str, status: str | None) -> None:
        job_dir = root / "2026" / "07" / job_id
        (job_dir / "output").mkdir(parents=True)
        (job_dir / "meta.json").write_text("{}", encoding="utf-8")
        if status is not None:
            (job_dir / "status.json").write_text(
                json.dumps({"processing_status": status}, ensure_ascii=False),
                encoding="utf-8",
            )

    write_job("20260714-completed", "completed")
    write_job("20260714-clarify", "needs_input")
    write_job("20260714-failed", "failed")
    write_job("20260714-incomplete", "processing")
    write_job("20260714-queued", "queued")
    write_job("20260714-running", "running")
    write_job("20260714-no-result", None)

    summary = summarize_writing_tasks(root)

    assert summary.total == 7
    assert summary.completed == 1
    assert summary.needs_input == 1
    assert summary.failed == 1
    assert summary.incomplete == 3
    assert summary.unknown == 1
    assert summary.legacy == 0


def test_summarize_review_tasks_supports_legacy_and_current_archives(tmp_path):
    root = tmp_path / "review"

    legacy = root / "2026" / "07" / "20260708-001"
    (legacy / "output").mkdir(parents=True)
    (legacy / "meta.md").write_text("旧审核元信息", encoding="utf-8")
    (legacy / "output" / "report.md").write_text("审核结果", encoding="utf-8")

    current = root / "2026" / "07" / "20260714-001"
    (current / "output").mkdir(parents=True)
    (current / "meta.json").write_text("{}", encoding="utf-8")
    (current / "output" / "report.md").write_text("审核结果", encoding="utf-8")

    incomplete = root / "2026" / "07" / "20260714-002"
    incomplete.mkdir(parents=True)
    (incomplete / "meta.json").write_text("{}", encoding="utf-8")

    output_only = root / "2026" / "07" / "20260714-003"
    (output_only / "output").mkdir(parents=True)
    (output_only / "output" / "report.md").write_text("审核结果", encoding="utf-8")

    summary = summarize_review_tasks(root)

    assert summary.total == 4
    assert summary.completed == 3
    assert summary.needs_input == 0
    assert summary.incomplete == 1
    assert summary.unknown == 0
    assert summary.legacy == 1


def test_list_todos_parses_fields_and_prioritizes_open_work(tmp_path):
    todo_path = tmp_path / "TODO.md"
    todo_path.write_text(
        """# 待办

### TODO-101：普通优化

状态：未开始

优先级：P2

归属：写作

目标：

- 已完成 v1：搭好基础结构。
- 待补真实样本回归。

### TODO-102：当前主线

状态：进行中

优先级：P0

归属：审核 / 测试

目标：

- 强化“背景 -> 成效 -> 下一步”的结构。

### TODO-103：历史任务

状态：已完成

优先级：P1

归属：底座
""",
        encoding="utf-8",
    )

    todos = list_todos(todo_path)

    assert [item.todo_id for item in todos] == ["TODO-102", "TODO-101", "TODO-103"]
    assert todos[0].title == "当前主线"
    assert todos[0].owner == "审核 / 测试"
    assert todos[0].next_action == "强化“背景 -> 成效 -> 下一步”的结构。"
    assert todos[1].next_action == "待补真实样本回归。"
    assert todos[2].is_open is False


def test_list_service_health_distinguishes_healthy_stale_and_missing(tmp_path):
    heartbeat_dir = tmp_path / "heartbeats"
    heartbeat_dir.mkdir()
    (heartbeat_dir / "writing_bot.json").write_text(
        json.dumps({"service": "writing_bot", "updated_at": "2026-07-13 10:00:00"}),
        encoding="utf-8",
    )
    (heartbeat_dir / "review_bot.json").write_text(
        json.dumps({"service": "review_bot", "updated_at": "2026-07-13 09:50:00"}),
        encoding="utf-8",
    )
    (heartbeat_dir / "rewrite_bot.json").write_text(
        json.dumps({"service": "rewrite_bot", "updated_at": "2026-07-13 09:59:00"}),
        encoding="utf-8",
    )

    services = list_service_health(
        heartbeat_dir,
        now=datetime(2026, 7, 13, 10, 2, 0),
        max_age_seconds=180,
    )

    assert [(item.service, item.status) for item in services] == [
        ("writing_bot", "healthy"),
        ("review_bot", "stale"),
        ("rewrite_bot", "healthy"),
        ("ops_bot", "missing"),
    ]
    assert services[2].name == "材料润色 Bot"


def test_list_service_health_treats_malformed_heartbeat_as_missing(tmp_path):
    heartbeat_dir = tmp_path / "heartbeats"
    heartbeat_dir.mkdir()
    (heartbeat_dir / "writing_bot.json").write_text("{incomplete", encoding="utf-8")

    services = list_service_health(heartbeat_dir)

    assert services[0].service == "writing_bot"
    assert services[0].status == "missing"


def test_build_project_overview_uses_runtime_counts_without_reading_content(tmp_path):
    project_root = tmp_path / "project"
    skills_dir = project_root / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)
    _write_skill(skills_dir, "writer1", enabled=False)
    todo_path = project_root / "docs" / "development" / "TODO.md"
    todo_path.parent.mkdir(parents=True)
    todo_path.write_text(
        """### TODO-201：审核质量基线

状态：进行中

优先级：P0

归属：审核

目标：

- 固化真实样本。
""",
        encoding="utf-8",
    )
    writing_job = tmp_path / "data" / "tasks" / "writing" / "2026" / "07" / "job-1"
    writing_job.mkdir(parents=True)
    (writing_job / "meta.json").write_text("{}", encoding="utf-8")
    review_task = tmp_path / "data" / "tasks" / "review" / "2026" / "07" / "review-1"
    review_task.mkdir(parents=True)
    (review_task / "meta.json").write_text("{}", encoding="utf-8")
    policy_db = tmp_path / "data" / "policy.sqlite3"
    with sqlite3.connect(policy_db) as connection:
        connection.execute("CREATE TABLE policy_documents (id INTEGER)")
        connection.executemany("INSERT INTO policy_documents VALUES (?)", [(1,), (2,)])
    bank_db = tmp_path / "data" / "bank.sqlite3"
    with sqlite3.connect(bank_db) as connection:
        connection.execute("CREATE TABLE bank_entries (id INTEGER)")
        connection.execute("INSERT INTO bank_entries VALUES (1)")

    overview = build_project_overview(
        AdminPaths(
            skills_dir=skills_dir,
            policy_path=project_root / "config" / "policy.yaml",
            jobs_dir=tmp_path / "data" / "tasks" / "writing",
            project_root=project_root,
            todo_path=todo_path,
            review_tasks_dir=tmp_path / "data" / "tasks" / "review",
            heartbeat_dir=tmp_path / "data" / "heartbeats",
            policy_db_path=policy_db,
            bank_db_path=bank_db,
        )
    )

    assert overview.enabled_skill_count == 1
    assert overview.total_skill_count == 2
    assert overview.open_todo_count == 1
    assert overview.writing_job_count == 1
    assert overview.review_task_count == 1
    assert overview.writing_task_stats.total == 1
    assert overview.writing_task_stats.completed == 0
    assert overview.review_task_stats.total == 1
    assert overview.review_task_stats.completed == 0
    assert overview.policy_count == 2
    assert overview.bank_count == 1
    assert any(module.name == "审核" and module.next_todo_id == "TODO-201" for module in overview.modules)
    assert any(
        module.key == "operations" and "写作、审核、润色、运维共 4 个服务" in module.current_summary
        for module in overview.modules
    )


def test_project_overview_builds_layered_capability_map_from_real_status_sources(tmp_path):
    project_root = tmp_path / "project"
    skills_dir = project_root / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)
    _write_skill(skills_dir, "rewrite", enabled=False)
    _write_skill(skills_dir, "research_synthesis", enabled=True)
    _write_skill(skills_dir, "shenyinxie_news", enabled=True)
    _write_skill(skills_dir, "internal_weekly", enabled=True)
    todo_path = project_root / "docs" / "development" / "TODO.md"
    todo_path.parent.mkdir(parents=True)
    todo_path.write_text(
        """### TODO-001：优化直报质量

状态：进行中

优先级：P2

归属：直报

目标：

- 补充真实样本回归。

### TODO-004：统一企业微信入口

状态：已暂缓

优先级：P2

归属：企业微信入口

### TODO-019：公文格式审核

状态：已完成

优先级：P1

归属：审核

### TODO-020：PPT 审核

状态：进行中

优先级：P2

归属：审核

目标：

- 完成逐页审核。

### TODO-021：多文件联合审核

状态：未开始

优先级：P1

归属：审核

目标：

- 完成真实材料验收。

### TODO-017：公共附件回传

状态：未开始

优先级：P1

归属：底座

### TODO-027：后台任务执行

状态：未开始

优先级：P1

归属：底座

### TODO-029：静态 HTML 审核

状态：已完成

优先级：P2

归属：审核

### TODO-030：深银协动态

状态：进行中

优先级：P1

归属：功能区

### TODO-031：共享审核核心

状态：未开始

优先级：P1

归属：审核
    """,
        encoding="utf-8",
    )
    multi_file_reviewer = project_root / "app" / "review" / "multi_file_reviewer.py"
    multi_file_reviewer.parent.mkdir(parents=True)
    multi_file_reviewer.write_text("# implementation started\n", encoding="utf-8")
    task_execution = project_root / "app" / "platform" / "task_execution.py"
    task_execution.parent.mkdir(parents=True, exist_ok=True)
    task_execution.write_text("# persistent task execution\n", encoding="utf-8")
    attachment_delivery = project_root / "app" / "platform" / "attachment_delivery.py"
    attachment_delivery.write_text("# shared attachment delivery\n", encoding="utf-8")
    heartbeat_dir = tmp_path / "heartbeats"
    heartbeat_dir.mkdir()
    (heartbeat_dir / "writing_bot.json").write_text(
        json.dumps({"updated_at": "2099-01-01 00:00:00"}),
        encoding="utf-8",
    )
    (heartbeat_dir / "rewrite_bot.json").write_text(
        json.dumps({"updated_at": "2099-01-01 00:00:00"}),
        encoding="utf-8",
    )

    overview = build_project_overview(
        AdminPaths(
            skills_dir=skills_dir,
            policy_path=project_root / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=project_root,
            todo_path=todo_path,
            heartbeat_dir=heartbeat_dir,
        )
    )

    assert [group.name for group in overview.component_groups] == [
        "业务入口",
        "智能体底座",
        "业务能力",
        "领域公共组件",
        "共享工具服务",
        "知识资产",
        "管理与治理",
    ]
    capabilities = {
        capability.id: capability
        for group in overview.component_groups
        for capability in group.capabilities
    }
    assert capabilities["writing_bot"].runtime_status == "healthy"
    assert capabilities["rewrite_bot"].runtime_status == "healthy"
    assert capabilities["direct_report"].status == "optimizing"
    assert capabilities["direct_report"].todo_id == "TODO-001"
    assert capabilities["rewrite"].status == "disabled"
    assert capabilities["research_synthesis"].status == "optimizing"
    assert capabilities["shenyinxie_news"].status == "optimizing"
    assert capabilities["internal_weekly"].status == "optimizing"
    assert capabilities["direct_report"].execution_mode == "persistent"
    assert capabilities["direct_report"].execution_mode_label == "持久队列"
    assert capabilities["brief_writing"].execution_mode == "persistent"
    assert capabilities["shenyinxie_news"].execution_mode == "persistent"
    assert capabilities["multi_file_review"].execution_mode == "persistent"
    assert capabilities["research_synthesis"].execution_mode == "persistent"
    assert capabilities["internal_weekly"].execution_mode == "persistent"
    assert capabilities["rewrite"].execution_mode == "realtime"
    assert capabilities["html_review"].status == "stable"
    assert capabilities["shared_review_core"].status == "optimizing"
    assert capabilities["unified_entry"].status == "paused"
    assert capabilities["official_format_review"].status == "stable"
    assert capabilities["ppt_review"].status == "optimizing"
    assert capabilities["multi_file_review"].status == "optimizing"
    assert {
        "general_text_review",
        "general_word_review",
        "html_review",
        "neican_review",
        "halfmonthly_review",
        "official_format_review",
        "ppt_review",
        "multi_file_review",
    }.issubset(capabilities)
    assert "general_review" not in capabilities
    assert capabilities["attachment_delivery"].status == "building"
    assert capabilities["task_execution"].status == "building"
    assert capabilities["task_relations"].status == "stable"
    assert overview.component_status_counts["building"] >= 1
    assert overview.component_status_counts["stable"] >= 1

    groups_by_capability = {
        capability.id: group.name
        for group in overview.component_groups
        for capability in group.capabilities
    }
    assert groups_by_capability["admin_console"] == "管理与治理"
    assert groups_by_capability["ops_bot"] == "管理与治理"
    assert groups_by_capability["task_files"] == "智能体底座"
    assert groups_by_capability["task_relations"] == "智能体底座"
    assert groups_by_capability["document_service"] == "共享工具服务"
    assert groups_by_capability["shared_review_core"] == "领域公共组件"
    assert groups_by_capability["policy_admin"] == "知识资产"
    assert "docx_reader" not in capabilities
    assert "pdf_ppt_reader" not in capabilities


def test_project_overview_architecture_separates_runtime_and_governance_planes(tmp_path):
    project_root = tmp_path / "project"
    skills_dir = project_root / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)

    overview = build_project_overview(
        AdminPaths(
            skills_dir=skills_dir,
            policy_path=project_root / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=project_root,
            todo_path=project_root / "docs" / "development" / "TODO.md",
        )
    )

    architecture_nodes = {node.id: node for node in overview.architecture_nodes}
    assert 20 <= len(architecture_nodes) <= 26
    assert architecture_nodes["business_entry"].plane == "runtime"
    assert architecture_nodes["agent_runtime"].group == "platform"
    assert architecture_nodes["direct_report"].group == "capabilities"
    assert architecture_nodes["brief_writing"].group == "capabilities"
    assert architecture_nodes["general_review"].group == "capabilities"
    assert architecture_nodes["multi_file_review"].group == "capabilities"
    assert architecture_nodes["document_service"].group == "services"
    assert architecture_nodes["policy_knowledge"].group == "knowledge"
    assert architecture_nodes["admin_console"].plane == "governance"
    assert architecture_nodes["knowledge_governance"].plane == "governance"
    assert "general_word_review" not in architecture_nodes
    assert len(overview.architecture_relations) >= 15
    assert all(
        relation.source_id in architecture_nodes and relation.target_id in architecture_nodes
        for relation in overview.architecture_relations
    )
    assert any(
        relation.source_id == "business_entry"
        and relation.target_id == "platform_access"
        and relation.label == "提交请求"
        for relation in overview.architecture_relations
    )
    assert any(
        relation.source_id == "policy_knowledge"
        and relation.target_id == "writing_domain"
        and relation.label == "提供政策背景"
        for relation in overview.architecture_relations
    )
    assert any(
        relation.source_id == "result_delivery"
        and relation.target_id == "business_entry"
        and relation.label == "返回结果"
        for relation in overview.architecture_relations
    )
    assert any(
        relation.source_id == "knowledge_governance"
        and relation.target_id == "policy_knowledge"
        and relation.relation_type == "governance"
        for relation in overview.architecture_relations
    )
    assert any(
        relation.source_id == "ops_observability"
        and relation.target_id == "agent_runtime"
        and relation.relation_type == "governance"
        for relation in overview.architecture_relations
    )


def test_real_skill_configs_are_all_represented_in_component_inventory():
    project_root = Path(__file__).resolve().parent.parent
    configured_skill_ids = {skill.id for skill in list_skills(project_root / "skills")}
    represented_skill_ids = {
        skill_id
        for group in _COMPONENT_GROUP_SPECS
        for capability in group.capabilities
        for skill_id in capability.skill_ids
    }

    assert configured_skill_ids <= represented_skill_ids
