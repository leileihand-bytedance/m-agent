from collections.abc import Collection
from pathlib import Path

import yaml

from app.platform.models import SkillDefinition


class SkillRegistry:
    def __init__(self, skills: list[SkillDefinition]):
        self._skills = {skill.id: skill for skill in skills}

    @classmethod
    def from_directory(
        cls,
        skills_dir: Path,
        *,
        include_skill_ids: Collection[str] | None = None,
    ) -> "SkillRegistry":
        skills: list[SkillDefinition] = []
        if not skills_dir.exists():
            return cls([])

        included = set(include_skill_ids) if include_skill_ids is not None else None
        for config_path in sorted(skills_dir.glob("*/config.yaml")):
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            skill_id = str(raw["id"])
            if included is not None and skill_id not in included:
                continue
            skill = SkillDefinition(
                id=skill_id,
                name=str(raw["name"]),
                description=str(raw.get("description", "")),
                enabled=bool(raw.get("enabled", False)),
                triggers=tuple(str(item) for item in raw.get("triggers", [])),
                allowed_tools=tuple(str(item) for item in raw.get("allowed_tools", [])),
                workflow=str(raw["workflow"]),
                directory=config_path.parent,
                supports_revision=bool(raw.get("supports_revision", False)),
                inputs=tuple(str(item) for item in raw.get("inputs", [])),
                outputs=tuple(str(item) for item in raw.get("outputs", [])),
            )
            skills.append(skill)
        return cls(skills)

    def get(self, skill_id: str) -> SkillDefinition:
        return self._skills[skill_id]

    def list_enabled(self) -> list[SkillDefinition]:
        return [skill for skill in self._skills.values() if skill.enabled]
