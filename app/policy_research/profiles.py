from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyResearchProfile:
    id: str
    max_primary: int
    max_alternatives: int
    reject_material_types: tuple[str, ...]
    min_relevance: int = 25


PROFILES = {
    "direct_report": PolicyResearchProfile(
        id="direct_report",
        max_primary=1,
        max_alternatives=1,
        reject_material_types=("event_activity", "award_or_recognition", "lawsuit_or_case"),
        min_relevance=28,
    ),
    "brief": PolicyResearchProfile(
        id="brief",
        max_primary=1,
        max_alternatives=2,
        reject_material_types=(),
        min_relevance=22,
    ),
}


def get_policy_research_profile(profile_id: str) -> PolicyResearchProfile:
    return PROFILES.get(profile_id, PROFILES["brief"])
