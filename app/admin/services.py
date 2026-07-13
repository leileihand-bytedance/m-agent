from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AdminPaths:
    skills_dir: Path
    policy_path: Path
    jobs_dir: Path


@dataclass(frozen=True)
class SkillAdminSummary:
    id: str
    name: str
    description: str
    enabled: bool
    triggers: list[str]
    allowed_tools: list[str]
    workflow: str
    skill_preview: str


@dataclass(frozen=True)
class JobAdminSummary:
    job_id: str
    channel: str
    sender_userid: str
    created_at: str
    message_preview: str
    skill_id: str
    title: str
    needs_clarification: bool
    message: str
    path: Path


def list_skills(skills_dir: Path) -> list[SkillAdminSummary]:
    summaries: list[SkillAdminSummary] = []
    if not skills_dir.exists():
        return []

    for config_path in sorted(skills_dir.glob("*/config.yaml")):
        raw = _read_yaml(config_path)
        skill_path = config_path.parent / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
        summaries.append(
            SkillAdminSummary(
                id=str(raw.get("id", config_path.parent.name)),
                name=str(raw.get("name", config_path.parent.name)),
                description=str(raw.get("description", "")),
                enabled=bool(raw.get("enabled", False)),
                triggers=_string_list(raw.get("triggers", [])),
                allowed_tools=_string_list(raw.get("allowed_tools", [])),
                workflow=str(raw.get("workflow", "")),
                skill_preview=skill_text[:1000],
            )
        )
    return summaries


def set_skill_enabled(skills_dir: Path, skill_id: str, enabled: bool) -> None:
    config_path = _skill_config_path(skills_dir, skill_id)
    raw = _read_yaml(config_path)
    raw["enabled"] = enabled
    _write_yaml(config_path, raw)


def list_policy_users(policy_path: Path) -> dict[str, list[str]]:
    raw = _read_yaml(policy_path)
    users = raw.get("users", {})
    if not isinstance(users, dict):
        return {}
    return {
        str(userid): _string_list(value.get("allowed_skills", []) if isinstance(value, dict) else [])
        for userid, value in users.items()
    }


def set_user_skills(policy_path: Path, userid: str, allowed_skills: list[str]) -> None:
    raw = _read_yaml(policy_path)
    raw.setdefault("allow_unknown_users", False)
    raw.setdefault("default_allowed_skills", [])
    users = raw.setdefault("users", {})
    if not isinstance(users, dict):
        users = {}
        raw["users"] = users
    users[userid] = {"allowed_skills": [skill for skill in allowed_skills if skill]}
    _write_yaml(policy_path, raw)


def list_jobs(paths: AdminPaths, limit: int = 20) -> list[JobAdminSummary]:
    jobs_dir = paths.jobs_dir
    if not jobs_dir.exists():
        return []

    summaries: list[JobAdminSummary] = []
    job_dirs = (path.parent for path in jobs_dir.glob("**/meta.json"))
    for job_dir in sorted(job_dirs, key=lambda path: path.name, reverse=True):
        meta = _read_json(job_dir / "meta.json")
        result = _read_json(job_dir / "output" / "result.json")
        output = result.get("output", {}) if isinstance(result.get("output", {}), dict) else {}
        summaries.append(
            JobAdminSummary(
                job_id=str(meta.get("job_id", job_dir.name)),
                channel=str(meta.get("channel", "")),
                sender_userid=str(meta.get("sender_userid", "")),
                created_at=str(meta.get("created_at", "")),
                message_preview=str(meta.get("message_preview", "")),
                skill_id=str(result.get("skill_id", "")),
                title=str(output.get("title", "")),
                needs_clarification=bool(result.get("needs_clarification", False)),
                message=str(result.get("message", "")),
                path=job_dir,
            )
        )
        if len(summaries) >= limit:
            break
    return summaries


def _skill_config_path(skills_dir: Path, skill_id: str) -> Path:
    if "/" in skill_id or "\\" in skill_id or skill_id in {"", ".", ".."}:
        raise ValueError("Invalid skill id")
    config_path = skills_dir / skill_id / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Skill config not found: {skill_id}")
    return config_path


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def _write_yaml(path: Path, raw: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
