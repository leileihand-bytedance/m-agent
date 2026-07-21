from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.identity import AccessPolicy  # noqa: E402


def test_access_policy_allows_explicit_user_skill():
    policy = AccessPolicy.from_dict(
        {
            "allow_unknown_users": False,
            "users": {
                "user-001": {
                    "allowed_skills": ["direct_report"],
                }
            },
        }
    )

    assert policy.can_use_skill("user-001", "direct_report") is True
    assert policy.can_use_skill("user-001", "review") is False


def test_access_policy_blocks_unknown_user_when_not_allowed():
    policy = AccessPolicy.from_dict(
        {
            "allow_unknown_users": False,
            "default_allowed_skills": ["direct_report"],
            "users": {},
        }
    )

    assert policy.can_use_skill("unknown-user", "direct_report") is False


def test_access_policy_can_allow_unknown_users_for_local_development():
    policy = AccessPolicy.from_dict(
        {
            "allow_unknown_users": True,
            "default_allowed_skills": ["direct_report"],
            "users": {},
        }
    )

    assert policy.can_use_skill("unknown-user", "direct_report") is True
    assert policy.can_use_skill("unknown-user", "review") is False


def test_access_policy_normalizes_legacy_brief_permission():
    policy = AccessPolicy.from_dict(
        {
            "allow_unknown_users": True,
            "default_allowed_skills": ["writer2"],
            "users": {"user-001": {"allowed_skills": ["writer2", "writer1"]}},
        }
    )

    assert policy.default_allowed_skills == ("writer1",)
    assert policy.user_allowed_skills["user-001"] == ("writer1",)
    assert policy.can_use_skill("user-001", "writer1") is True
    assert policy.can_use_skill("unknown-user", "writer1") is True
