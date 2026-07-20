from pathlib import Path
import os
import re
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.server import AdminPaths, _require_csrf, render_dashboard  # noqa: E402
from app.platform.delivery_recovery import DeliveryRecoveryCandidate  # noqa: E402


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


def test_render_dashboard_does_not_show_dedicated_review_capability_statistics(tmp_path):
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

    assert 'href="#review-statistics"' not in html
    assert '<section id="review-statistics">' not in html
    assert "审核模块统计" not in html


def test_render_dashboard_shows_safe_delivery_recovery_actions(tmp_path):
    pending = (
        DeliveryRecoveryCandidate(
            source="writing",
            task_id="task-safe-001",
            task_type="writing_writer1",
            updated_at="2026-07-20T09:00:00+00:00",
            queue_status="failed",
            delivery_status="delivery_unknown",
            safe_error_code="delivery_status_uncertain",
            item_count=2,
        ),
        DeliveryRecoveryCandidate(
            source="review",
            task_id="task-safe-002",
            task_type="review_docx",
            updated_at="2026-07-20T08:00:00+00:00",
            queue_status="failed",
            delivery_status="confirmed_not_delivered",
            safe_error_code="delivery_not_delivered",
            item_count=1,
        ),
    )

    html = render_dashboard(
        AdminPaths(
            skills_dir=tmp_path / "skills",
            policy_path=tmp_path / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=tmp_path,
        ),
        delivery_recoveries=pending,
        csrf_token="safe-csrf-token",
    )

    assert 'id="delivery-recovery"' in html
    assert "task-safe-001" in html
    assert "送达未知" in html
    assert "确认未收到并重发" in html
    assert "确认已送达" in html
    assert "关闭未知状态" in html
    assert "task-safe-002" in html
    assert "明确未送达" in html
    assert 'name="csrf_token" value="safe-csrf-token"' in html
    assert "user-1" not in html
    assert "已经生成的结果" not in html


def test_admin_post_actions_require_matching_page_token():
    _require_csrf({"csrf_token": ["expected"]}, "expected")

    with pytest.raises(ValueError, match="刷新"):
        _require_csrf({"csrf_token": ["wrong"]}, "expected")


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
    assert "业务运行面" in html
    assert "管理与治理面" in html
    assert "功能与模块状态" in html
    assert 'class="architecture-status-content"' in html
    assert "业务入口" in html
    assert "智能体底座" in html
    assert "共享工具服务" in html
    assert "知识资产" in html
    assert "领域公共组件" in html
    assert "工具与知识库" not in html
    assert "运维与数据" not in html
    assert "材料润色 Bot" in html
    assert "综合调研整合" in html
    assert "深银协动态" in html
    assert "静态 HTML 审核" in html
    assert "审核共享核心" in html
    assert "单份 PPTX 低级错误审核" in html
    assert "持久队列" in html
    assert "实时执行" in html
    assert 'data-execution-mode="persistent"' in html
    assert 'data-execution-mode="realtime"' in html
    assert "检查幻灯片文字、逻辑、版式并提供可交付结果" not in html
    assert 'data-component-status="stable"' in html
    assert 'data-component-status="building"' in html
    assert 'data-component-filter="all"' in html
    assert 'data-component-filter="building"' in html
    assert "稳定运行" in html
    assert "建设中" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_render_dashboard_includes_controlled_detailed_architecture_diagram(tmp_path):
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

    assert 'id="architecture-diagram"' in html
    assert 'id="architecture-flow-svg"' in html
    assert 'id="architecture-graph-data"' in html
    assert "architecture-plane-key" not in html
    assert "受控 Agent 运行架构" in html
    assert 'data-component-status="stable"' in html
    assert 'data-component-filter="all"' in html
    assert 'data-architecture-view="graph"' not in html
    assert 'data-architecture-node="direct_report"' in html
    assert 'data-architecture-node="general_review"' in html
    assert 'data-architecture-node="writing_domain"' in html
    assert 'data-architecture-node="review_domain"' in html
    assert 'data-architecture-node="result_delivery"' in html
    assert 'class="architecture-domain-card architecture-domain-card--writing"' in html
    assert 'class="architecture-domain-card architecture-domain-card--review"' in html
    assert 'class="architecture-main-flow architecture-main-flow--vertical"' in html
    assert 'class="architecture-agent-core"' in html
    assert 'class="architecture-control-gates"' in html
    assert 'class="architecture-foundation"' in html
    assert 'class="architecture-governance-grid"' in html
    assert "身份隔离" in html
    assert "工具白名单" in html
    assert "结构化输出" in html
    assert "幂等与恢复" in html
    assert "交付确认" in html
    assert "成稿、改稿与专题内容生产" in html
    assert "通用、专项、格式与跨文件审核" in html
    assert "意图路由 · 多任务关系 · 材料组装 · 持久队列" in html
    assert "Pydantic AI · Skill 合约 · ToolGateway · 结构化输出" in html
    assert "统一文档服务" in html
    assert "政策知识库" in html
    assert "运维与可观测性" in html
    assert "roundedOrthogonalPath" in html
    assert "branchPairStyles" in html
    assert "branchPairStyles.has(pair)" in html
    assert "straightPathIsClear" not in html
    assert "segmentIntersectsBox" not in html
    assert "architecture-edge--flow" in html
    assert "@keyframes architecture-information-flow" in html
    assert "prefers-reduced-motion: reduce" in html
    assert '<script src="/static/vendor/vis-network.min.js"></script>' not in html
    assert "vis.Network" not in html
    assert "unpkg.com" not in html
    assert '"id":"business_entry"' in html
    assert '"id":"admin_console"' in html
    assert '"plane":"runtime"' in html
    assert '"plane":"governance"' in html
    assert '"source_id":"business_entry"' in html
    assert '"relation_type":"governance"' in html


def test_architecture_diagram_renders_all_registered_nodes_with_limited_flow_lines(tmp_path):
    project_root = Path(__file__).resolve().parent.parent
    html = render_dashboard(
        AdminPaths(
            skills_dir=tmp_path / "skills",
            policy_path=tmp_path / "policy.yaml",
            jobs_dir=tmp_path / "jobs",
            project_root=project_root,
        )
    )
    rendered_node_ids = re.findall(
        r'<button[^>]+data-architecture-node="([^"]+)"',
        html,
    )

    assert len(rendered_node_ids) == 24
    assert len(rendered_node_ids) == len(set(rendered_node_ids))
    assert {
        "business_entry",
        "platform_access",
        "platform_orchestration",
        "agent_runtime",
        "writing_domain",
        "review_domain",
        "result_delivery",
        "direct_report",
        "brief_writing",
        "rewrite",
        "thematic_content",
        "general_review",
        "special_review",
        "format_review",
        "multi_file_review",
        "document_service",
        "web_retrieval",
        "policy_knowledge",
        "bank_knowledge",
        "admin_console",
        "ops_observability",
        "data_governance",
        "engineering_governance",
        "knowledge_governance",
    } == set(rendered_node_ids)
    assert '["writing_domain>direct_report", "writing"]' not in html
    assert '["review_domain>general_review", "review"]' not in html
    assert not (project_root / "app/admin/static/vendor/vis-network.min.js").exists()
