from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin.services import (  # noqa: E402
    AdminPaths,
    list_jobs,
    list_policy_users,
    list_skills,
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
