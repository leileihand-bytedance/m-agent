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
