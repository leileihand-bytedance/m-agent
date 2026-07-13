from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.registry import SkillRegistry  # noqa: E402


def test_writer_skills_are_installed_and_enabled_for_brief_flow():
    writer1 = Path("skills/writer1")
    writer2 = Path("skills/writer2")
    rewrite = Path("skills/rewrite")

    assert (writer1 / "SKILL.md").exists()
    assert (writer1 / "config.yaml").exists()
    assert (writer1 / "knowledge" / "writing-materials.json").exists()
    assert (writer1 / "knowledge" / "domain-terms.json").exists()
    assert (writer1 / "knowledge" / "policy-backgrounds.json").exists()

    assert (writer2 / "SKILL.md").exists()
    assert (writer2 / "config.yaml").exists()
    assert (writer1 / "workflow.py").exists()
    assert (writer2 / "workflow.py").exists()
    assert (rewrite / "SKILL.md").exists()
    assert (rewrite / "config.yaml").exists()
    assert (rewrite / "workflow.py").exists()

    registry = SkillRegistry.from_directory(Path("skills"))
    enabled_skill_ids = {skill.id for skill in registry.list_enabled()}
    assert "writer1" in enabled_skill_ids
    assert "writer2" in enabled_skill_ids
    assert "direct_report" in enabled_skill_ids
    assert "rewrite" in enabled_skill_ids
