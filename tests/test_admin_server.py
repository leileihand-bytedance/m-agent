from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.server import AdminPaths, VIS_NETWORK_ASSET, render_dashboard  # noqa: E402


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
        AdminPaths(skills_dir=skills_dir, policy_path=policy_path, jobs_dir=tmp_path / "jobs"),
        show_sensitive=True,
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
        ),
        show_sensitive=True,
    )

    assert "暂无 Skill" in html
    assert "暂无用户权限配置" in html
    assert "暂无任务记录" in html


def test_render_dashboard_does_not_load_or_render_users_and_jobs_by_default(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("默认页面不应读取敏感管理数据")

    monkeypatch.setattr("app.admin.server.list_policy_users", fail_if_called)
    monkeypatch.setattr("app.admin.server.list_jobs", fail_if_called)

    html = render_dashboard(
        AdminPaths(
            skills_dir=skills_dir,
            policy_path=tmp_path / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=tmp_path,
        )
    )

    assert 'id="users"' not in html
    assert 'id="jobs"' not in html
    assert 'href="#users"' not in html
    assert 'href="#jobs"' not in html
    assert 'href="/?show_sensitive=1#users"' in html
    assert "显示用户权限与任务记录" in html


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
    writing_job = tmp_path / "jobs" / "2026" / "07" / "20260714-writing"
    writing_job.mkdir(parents=True)
    (writing_job / "meta.json").write_text("{}", encoding="utf-8")
    (writing_job / "status.json").write_text(
        '{"processing_status": "completed"}',
        encoding="utf-8",
    )
    legacy_review = tmp_path / "review" / "2026" / "07" / "20260714-001"
    (legacy_review / "output").mkdir(parents=True)
    (legacy_review / "meta.md").write_text("历史元信息", encoding="utf-8")
    (legacy_review / "output" / "report.md").write_text("审核报告", encoding="utf-8")

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
    assert '<section id="overview">' not in html
    assert 'href="#overview"' not in html
    assert '<a href="#architecture">项目总览</a>' in html
    assert "已启用能力" not in html
    assert "累计任务" not in html
    assert "Git 状态" not in html
    assert "板块进展" in html
    assert "下一步待办" in html
    assert "运行状态" in html
    assert "补齐审核质量基线" in html
    assert "底座" in html
    assert "写作" in html
    assert "审核" in html
    assert "累计创建 1 个写作任务，完成成稿 1 个" in html
    assert "累计归档 1 个审核任务，已生成审核报告 1 个" in html
    assert "旧格式历史归档 1 个" in html


def test_render_dashboard_shows_independent_review_capability_statistics(tmp_path):
    review_dir = tmp_path / "review" / "2026" / "07" / "task-1"
    review_dir.mkdir(parents=True)
    (review_dir / "meta.json").write_text(
        '{"capability_id":"general_word_review","observability":{"elapsed_ms":1200,"model_calls":2,"model_failures":0,"finding_count":3}}',
        encoding="utf-8",
    )
    (review_dir / "status.json").write_text(
        '{"processing_status":"completed","delivery_status":"delivered"}',
        encoding="utf-8",
    )

    html = render_dashboard(
        AdminPaths(
            skills_dir=tmp_path / "skills",
            policy_path=tmp_path / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=tmp_path,
            review_tasks_dir=tmp_path / "review",
        )
    )

    assert 'href="#review-statistics"' in html
    assert '<section id="review-statistics">' in html
    assert "通用 Word 审核" in html
    assert "模型调用" in html
    assert "已交付" in html


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

    assert '<h2>项目总览</h2>' in html
    assert "整体架构与功能模块" not in html
    assert "用户入口" in html
    assert "通用底座" in html
    assert "工具与知识库" in html
    assert "运维与数据" in html
    assert "材料润色 Bot" in html
    assert "综合调研整合" in html
    assert "深银协动态" in html
    assert "静态 HTML 审核" in html
    assert "审核共享核心" in html
    assert "单份 PPTX 低级错误审核" in html
    assert "检查幻灯片文字、逻辑、版式并提供可交付结果" not in html
    assert 'data-capability-status="stable"' in html
    assert 'data-capability-status="building"' in html
    assert 'data-capability-filter="all"' in html
    assert 'data-capability-filter="building"' in html
    assert "稳定运行" in html
    assert "建设中" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_render_dashboard_includes_local_interactive_architecture_graph(tmp_path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "direct_report", enabled=True)

    html = render_dashboard(
        AdminPaths(
            skills_dir=skills_dir,
            policy_path=tmp_path / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=tmp_path,
        )
    )

    assert 'id="architecture-network"' in html
    assert 'id="architecture-graph-data"' in html
    assert 'data-architecture-view="graph"' in html
    assert 'data-architecture-view="list"' in html
    assert "关系图" in html
    assert "状态清单" in html
    assert '<script src="/static/vendor/vis-network.min.js"></script>' in html
    assert "unpkg.com" not in html
    assert '"source_id":"writing_bot"' in html


def test_interactive_graph_dependency_is_vendored_with_license_files():
    vendor_dir = VIS_NETWORK_ASSET.parent

    assert VIS_NETWORK_ASSET.is_file()
    assert "@version 10.1.0" in VIS_NETWORK_ASSET.read_text(encoding="utf-8")[:500]
    assert (vendor_dir / "LICENSE-vis-network-MIT.txt").is_file()
    assert (vendor_dir / "LICENSE-vis-network-APACHE-2.0.txt").is_file()
