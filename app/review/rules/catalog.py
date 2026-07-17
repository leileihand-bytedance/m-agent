"""Canonical metadata for active review rule IDs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RuleScope(str, Enum):
    COMMON = "common"
    CONDITIONAL = "conditional"
    SPECIALIZED = "specialized"


@dataclass(frozen=True)
class RuleDefinition:
    rule_id: str
    family: str
    scope: RuleScope
    execution: str
    evidence_policy: str
    location_policy: str


def _rule(
    rule_id: str,
    family: str,
    scope: RuleScope,
    execution: str = "deterministic",
    evidence_policy: str = "single_exact",
    location_policy: str = "paragraph",
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        family=family,
        scope=scope,
        execution=execution,
        evidence_policy=evidence_policy,
        location_policy=location_policy,
    )


_RULES = (
    _rule("quote-pair", "quote_pair", RuleScope.COMMON),
    _rule("num-unit", "number_unit_spacing", RuleScope.CONDITIONAL),
    _rule("mixed-punct", "mixed_punctuation", RuleScope.CONDITIONAL),
    _rule("consecutive-punct", "consecutive_punctuation", RuleScope.COMMON),
    _rule("toc-no-ordinal", "toc_structure", RuleScope.CONDITIONAL),
    _rule("toc-seq-skip", "toc_structure", RuleScope.CONDITIONAL),
    _rule("general-placeholder", "placeholder", RuleScope.CONDITIONAL),
    _rule("general-heading-seq-skip", "heading_sequence", RuleScope.CONDITIONAL),
    _rule("general-heading-empty", "heading_structure", RuleScope.CONDITIONAL),
    _rule("general-reference-missing", "reference_integrity", RuleScope.CONDITIONAL),
    _rule("general-attachment-name-mismatch", "attachment_reference", RuleScope.CONDITIONAL),
    _rule("general-invalid-date", "calendar_date", RuleScope.CONDITIONAL),
    _rule("general-date-range-logic", "date_logic", RuleScope.CONDITIONAL),
    _rule("general-term-variant", "term_variant", RuleScope.CONDITIONAL),
    _rule("general-typo", "typo", RuleScope.CONDITIONAL, "model"),
    _rule("general-name-error", "name_consistency", RuleScope.CONDITIONAL, "model"),
    _rule("general-grammar", "grammar", RuleScope.CONDITIONAL, "model"),
    _rule("general-punctuation", "punctuation", RuleScope.CONDITIONAL, "model"),
    _rule("general-incomplete", "content_incomplete", RuleScope.CONDITIONAL, "model"),
    _rule("general-duplicate", "content_duplicate", RuleScope.CONDITIONAL, "model", "document_context"),
    _rule("general-logic-inconsistency", "document_logic", RuleScope.CONDITIONAL, "model", "document_context"),
    _rule("title-truncated", "title_integrity", RuleScope.SPECIALIZED, "model"),
    _rule("content-mismatch", "title_body_match", RuleScope.SPECIALIZED, "model"),
    _rule("content-incomplete", "content_incomplete", RuleScope.CONDITIONAL, "model"),
    _rule("toc-mismatch", "toc_structure", RuleScope.SPECIALIZED, "hybrid"),
    _rule("content-out-of-scope", "section_scope", RuleScope.SPECIALIZED, "model"),
    _rule("content-wrong-section", "section_scope", RuleScope.SPECIALIZED, "hybrid"),
    _rule("content-duplicate", "content_duplicate", RuleScope.CONDITIONAL, "model", "document_context"),
    _rule("content-outdated", "timeliness", RuleScope.SPECIALIZED, "model"),
    _rule("weekly-body-format", "inner_report_format", RuleScope.SPECIALIZED, "deterministic", "format_property", "word_format"),
    _rule("content-citation-mismatch", "citation", RuleScope.SPECIALIZED, "hybrid", "double_exact", "paragraph_pair"),
    _rule("halfmonthly-date-mismatch", "date_logic", RuleScope.SPECIALIZED, "hybrid"),
    _rule("halfmonthly-section-order", "section_order", RuleScope.SPECIALIZED, "hybrid"),
    _rule("halfmonthly-section-mismatch", "section_scope", RuleScope.SPECIALIZED, "model"),
    _rule("halfmonthly-leader-title", "leader_title", RuleScope.SPECIALIZED),
    _rule("halfmonthly-numbering", "numbering", RuleScope.SPECIALIZED),
    _rule("halfmonthly-body-format", "halfmonthly_format", RuleScope.SPECIALIZED, "deterministic", "format_property", "word_format"),
    _rule("ppt-sequence-duplicate", "sequence", RuleScope.SPECIALIZED, location_policy="ppt_element"),
    _rule("ppt-sequence-reverse", "sequence", RuleScope.SPECIALIZED, location_policy="ppt_element"),
    _rule("ppt-sequence-skip", "sequence", RuleScope.SPECIALIZED, location_policy="ppt_element"),
    _rule("ppt-placeholder", "placeholder", RuleScope.SPECIALIZED, location_policy="ppt_element"),
    _rule("ppt-quote-pair", "quote_pair", RuleScope.SPECIALIZED, location_policy="ppt_element"),
    _rule("ppt-consecutive-punctuation", "consecutive_punctuation", RuleScope.SPECIALIZED, location_policy="ppt_element"),
    _rule("ppt-typo", "typo", RuleScope.SPECIALIZED, "model", location_policy="ppt_element"),
    _rule("ppt-grammar", "grammar", RuleScope.SPECIALIZED, "model", location_policy="ppt_element"),
    _rule("ppt-punctuation", "punctuation", RuleScope.SPECIALIZED, "model", location_policy="ppt_element"),
    _rule("ppt-name", "name_consistency", RuleScope.SPECIALIZED, "model", "double_exact", "ppt_element_pair"),
    _rule("ppt-data-inconsistency", "data_consistency", RuleScope.SPECIALIZED, "model", "double_exact", "ppt_element_pair"),
    _rule("ppt-content-inconsistency", "content_consistency", RuleScope.SPECIALIZED, "model", "double_exact", "ppt_element_pair"),
    _rule("official-format-page", "official_format", RuleScope.SPECIALIZED, evidence_policy="format_property", location_policy="word_format"),
    _rule("official-format-title", "official_format", RuleScope.SPECIALIZED, evidence_policy="format_property", location_policy="word_format"),
    _rule("official-format-heading1", "official_format", RuleScope.SPECIALIZED, evidence_policy="format_property", location_policy="word_format"),
    _rule("official-format-heading2", "official_format", RuleScope.SPECIALIZED, evidence_policy="format_property", location_policy="word_format"),
    _rule("official-format-heading3", "official_format", RuleScope.SPECIALIZED, evidence_policy="format_property", location_policy="word_format"),
    _rule("official-format-body", "official_format", RuleScope.SPECIALIZED, evidence_policy="format_property", location_policy="word_format"),
    _rule("multi-file-attachment-duplicate", "cross_file_attachment", RuleScope.SPECIALIZED, location_policy="file"),
    _rule("multi-file-reference-missing", "cross_file_attachment", RuleScope.SPECIALIZED, location_policy="file"),
    _rule("multi-file-attachment-unreferenced", "cross_file_attachment", RuleScope.SPECIALIZED, location_policy="file"),
    _rule("multi-file-attachment-name-mismatch", "cross_file_attachment", RuleScope.SPECIALIZED, "deterministic", "double_exact", "file_pair"),
    _rule("multi-file-logic-inconsistency", "cross_file_logic", RuleScope.SPECIALIZED, "model", "double_exact", "file_pair"),
)


RULE_CATALOG = {rule.rule_id: rule for rule in _RULES}
if len(RULE_CATALOG) != len(_RULES):
    raise RuntimeError("Duplicate review rule ID in canonical catalog")
