from __future__ import annotations


_LEGACY_SKILL_IDS = {"writer2": "writer1"}


def canonical_skill_id(skill_id: str | None) -> str | None:
    """Translate retired persisted IDs without changing historical records."""

    if skill_id is None:
        return None
    normalized = str(skill_id).strip()
    return _LEGACY_SKILL_IDS.get(normalized, normalized)
