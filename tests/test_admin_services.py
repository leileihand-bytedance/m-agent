from pathlib import Path
from datetime import datetime
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.services import (  # noqa: E402
    AdminPaths,
    build_project_overview,
    list_jobs,
    list_policy_users,
    list_service_health,
    list_skills,
    list_todos,
    set_skill_enabled,
    set_user_skills,
)


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
    set_user_skills(policy_path, "new-user", ["writer2"])

    users = list_policy_users(policy_path)
    assert users["test-user"] == ["direct_report", "writer1"]
    assert users["new-user"] == ["writer2"]


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

    services = list_service_health(
        heartbeat_dir,
        now=datetime(2026, 7, 13, 10, 2, 0),
        max_age_seconds=180,
    )

    assert [(item.service, item.status) for item in services] == [
        ("writing_bot", "healthy"),
        ("review_bot", "stale"),
        ("ops_bot", "missing"),
    ]


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
    assert overview.policy_count == 2
    assert overview.bank_count == 1
    assert any(module.name == "审核" and module.next_todo_id == "TODO-201" for module in overview.modules)


def test_project_overview_builds_layered_capability_map_from_real_status_sources(tmp_path):
    project_root = tmp_path / "project"
    skills_dir = project_root / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)
    _write_skill(skills_dir, "rewrite", enabled=False)
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
""",
        encoding="utf-8",
    )
    multi_file_reviewer = project_root / "app" / "review" / "multi_file_reviewer.py"
    multi_file_reviewer.parent.mkdir(parents=True)
    multi_file_reviewer.write_text("# implementation started\n", encoding="utf-8")
    heartbeat_dir = tmp_path / "heartbeats"
    heartbeat_dir.mkdir()
    (heartbeat_dir / "writing_bot.json").write_text(
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

    assert [layer.name for layer in overview.architecture_layers] == [
        "用户入口",
        "通用底座",
        "业务功能",
        "工具与知识库",
        "运维与数据",
    ]
    capabilities = {
        capability.id: capability
        for layer in overview.architecture_layers
        for capability in layer.capabilities
    }
    assert capabilities["writing_bot"].runtime_status == "healthy"
    assert capabilities["direct_report"].status == "optimizing"
    assert capabilities["direct_report"].todo_id == "TODO-001"
    assert capabilities["rewrite"].status == "disabled"
    assert capabilities["unified_entry"].status == "paused"
    assert capabilities["official_format_review"].status == "stable"
    assert capabilities["ppt_review"].status == "building"
    assert capabilities["multi_file_review"].status == "building"
    assert capabilities["attachment_delivery"].status == "planned"
    assert overview.capability_status_counts["building"] >= 1
    assert overview.capability_status_counts["stable"] >= 1
