from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest

from app.platform.registry import SkillRegistry


def test_registry_loads_enabled_direct_report_skill():
    registry = SkillRegistry.from_directory(Path("skills"))

    skill = registry.get("direct_report")

    assert skill.id == "direct_report"
    assert skill.name == "直报写作"
    assert skill.enabled is True
    assert "web_reader" in skill.allowed_tools
    assert "llm_writer" in skill.allowed_tools
    assert skill.workflow == "skills.direct_report.workflow:run"


def test_registry_loads_enabled_rewrite_skill():
    registry = SkillRegistry.from_directory(Path("skills"))

    skill = registry.get("rewrite")

    assert skill.id == "rewrite"
    assert skill.name == "材料润色"
    assert skill.enabled is True
    assert skill.allowed_tools == ("bank_materials", "llm_writer")
    assert skill.workflow == "skills.rewrite.workflow:run"
    assert skill.supports_revision is True


def test_registry_can_limit_loaded_skills_to_entry_allowlist():
    registry = SkillRegistry.from_directory(
        Path("skills"),
        include_skill_ids={"rewrite"},
    )

    assert [skill.id for skill in registry.list_enabled()] == ["rewrite"]
    with pytest.raises(KeyError):
        registry.get("direct_report")


def test_registry_loads_enabled_research_synthesis_skill():
    registry = SkillRegistry.from_directory(Path("skills"))

    skill = registry.get("research_synthesis")

    assert skill.name == "综合调研整合"
    assert skill.enabled is True
    assert skill.allowed_tools == ("document_reader", "word_reader", "pdf_reader", "llm_writer")
    assert skill.workflow == "skills.research_synthesis.workflow:run"
    assert skill.supports_revision is False


def test_registry_lists_only_enabled_skills(tmp_path):
    skills_dir = tmp_path / "skills"
    enabled = skills_dir / "enabled_skill"
    disabled = skills_dir / "disabled_skill"
    enabled.mkdir(parents=True)
    disabled.mkdir(parents=True)
    (enabled / "config.yaml").write_text(
        "id: enabled_skill\n"
        "name: Enabled\n"
        "enabled: true\n"
        "description: Enabled skill\n"
        "triggers:\n"
        "  - enabled\n"
        "allowed_tools:\n"
        "  - web_reader\n"
        "workflow: enabled.workflow:run\n",
        encoding="utf-8",
    )
    (disabled / "config.yaml").write_text(
        "id: disabled_skill\n"
        "name: Disabled\n"
        "enabled: false\n"
        "description: Disabled skill\n"
        "triggers:\n"
        "  - disabled\n"
        "allowed_tools:\n"
        "  - web_reader\n"
        "workflow: disabled.workflow:run\n",
        encoding="utf-8",
    )

    registry = SkillRegistry.from_directory(skills_dir)

    assert [skill.id for skill in registry.list_enabled()] == ["enabled_skill"]
