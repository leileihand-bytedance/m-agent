"""Canonical rule catalog and static review profiles."""

from .catalog import RULE_CATALOG, RuleDefinition, RuleScope
from .profiles import REVIEW_PROFILES, ReviewProfile

__all__ = [
    "REVIEW_PROFILES",
    "RULE_CATALOG",
    "ReviewProfile",
    "RuleDefinition",
    "RuleScope",
]
