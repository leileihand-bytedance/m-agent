from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.policy_research.models import PolicyResearchResult
from app.policy_research.profiles import get_policy_research_profile


def test_get_policy_research_profile_returns_known_profile():
    profile = get_policy_research_profile("brief")

    assert profile.id == "brief"
    assert profile.max_primary == 1
    assert profile.max_alternatives == 2


def test_policy_research_result_defaults_are_stable():
    result = PolicyResearchResult(
        should_attach_policy=False,
        decision_reason="unsupported_theme",
        matched_themes=[],
        retrieval_query="",
        confidence=0.0,
    )

    assert result.primary_policy is None
    assert result.alternative_policies == []
