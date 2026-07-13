from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AccessPolicy:
    allow_unknown_users: bool
    default_allowed_skills: tuple[str, ...]
    user_allowed_skills: dict[str, tuple[str, ...]]

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "AccessPolicy":
        users_raw = raw.get("users", {})
        users: dict[str, tuple[str, ...]] = {}
        if isinstance(users_raw, dict):
            for userid, value in users_raw.items():
                if not isinstance(value, dict):
                    continue
                users[str(userid)] = tuple(
                    str(item) for item in value.get("allowed_skills", []) if str(item).strip()
                )

        return cls(
            allow_unknown_users=bool(raw.get("allow_unknown_users", False)),
            default_allowed_skills=tuple(
                str(item)
                for item in raw.get("default_allowed_skills", [])
                if str(item).strip()
            ),
            user_allowed_skills=users,
        )

    @classmethod
    def from_file(cls, path: Path) -> "AccessPolicy":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raw = {}
        return cls.from_dict(raw)

    @classmethod
    def allow_all_for_skills(cls, skills: list[str] | tuple[str, ...]) -> "AccessPolicy":
        return cls(
            allow_unknown_users=True,
            default_allowed_skills=tuple(skills),
            user_allowed_skills={},
        )

    def can_use_skill(self, sender_userid: str, skill_id: str) -> bool:
        if sender_userid in self.user_allowed_skills:
            return skill_id in self.user_allowed_skills[sender_userid]
        if not self.allow_unknown_users:
            return False
        return skill_id in self.default_allowed_skills
