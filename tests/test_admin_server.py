from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.server import AdminPaths, render_dashboard  # noqa: E402


def _write_skill(root: Path, skill_id: str, *, enabled: bool) -> None:
    skill_dir = root / skill_id
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"# {skill_id}\n\n<script>bad</script>", encoding="utf-8")
    (skill_dir / "config.yaml").write_text(
        "\n".join(
            [
                f"id: {skill_id}",
                f"name: {skill_id} 名称",
                "description: 测试描述",
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


def test_render_dashboard_lists_skills_users_and_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-render")
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)
    _write_skill(skills_dir, "writer1", enabled=False)

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

    html = render_dashboard(
        AdminPaths(skills_dir=skills_dir, policy_path=policy_path, jobs_dir=tmp_path / "jobs")
    )

    assert "direct_report 名称" in html
    assert "writer1 名称" in html
    assert "test-user" in html
    assert "direct_report" in html
    assert "should-not-render" not in html
    assert "<script>bad</script>" not in html
    assert "&lt;script&gt;bad&lt;/script&gt;" in html


def test_render_dashboard_empty_state(tmp_path):
    html = render_dashboard(
        AdminPaths(
            skills_dir=tmp_path / "missing-skills",
            policy_path=tmp_path / "missing-policy.yaml",
            jobs_dir=tmp_path / "missing-jobs",
        )
    )

    assert "暂无 Skill" in html
    assert "暂无用户权限配置" in html
    assert "暂无任务记录" in html


def test_render_dashboard_shows_project_overview_modules_todos_and_runtime(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)
    todo_path = tmp_path / "docs" / "development" / "TODO.md"
    todo_path.parent.mkdir(parents=True)
    todo_path.write_text(
        """### TODO-301：补齐审核质量基线

状态：进行中

优先级：P0

归属：审核

目标：

- 用真实文件回归。
""",
        encoding="utf-8",
    )

    html = render_dashboard(
        AdminPaths(
            skills_dir=skills_dir,
            policy_path=tmp_path / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=tmp_path,
            todo_path=todo_path,
            review_tasks_dir=tmp_path / "review",
            heartbeat_dir=tmp_path / "heartbeats",
        )
    )

    assert "项目总览" in html
    assert "板块进展" in html
    assert "下一步待办" in html
    assert "运行状态" in html
    assert "补齐审核质量基线" in html
    assert "底座" in html
    assert "写作" in html
    assert "审核" in html


def test_render_dashboard_shows_filterable_architecture_and_capability_statuses(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)
    todo_path = tmp_path / "docs" / "development" / "TODO.md"
    todo_path.parent.mkdir(parents=True)
    todo_path.write_text(
        """### TODO-001：优化直报质量

状态：进行中

优先级：P2

归属：直报

目标：

- 建立质量回归，拒绝 <script>alert(1)</script>。
""",
        encoding="utf-8",
    )

    html = render_dashboard(
        AdminPaths(
            skills_dir=skills_dir,
            policy_path=tmp_path / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=tmp_path,
            todo_path=todo_path,
            heartbeat_dir=tmp_path / "heartbeats",
        )
    )

    assert "整体架构与功能模块" in html
    assert "用户入口" in html
    assert "通用底座" in html
    assert "工具与知识库" in html
    assert "运维与数据" in html
    assert 'data-capability-status="stable"' in html
    assert 'data-capability-status="building"' in html
    assert 'data-capability-filter="all"' in html
    assert 'data-capability-filter="building"' in html
    assert "稳定运行" in html
    assert "建设中" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
