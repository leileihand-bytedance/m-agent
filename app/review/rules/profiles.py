"""Static rule composition for each review material type."""

from __future__ import annotations

from dataclasses import dataclass

from .catalog import RULE_CATALOG


COMMON_FORMAT_RULE_IDS = (
    "quote-pair",
    "num-unit",
    "mixed-punct",
    "consecutive-punct",
    "toc-no-ordinal",
    "toc-seq-skip",
)
GENERAL_DETERMINISTIC_RULE_IDS = (
    "general-placeholder",
    "general-heading-seq-skip",
    "general-heading-empty",
    "general-reference-missing",
    "general-attachment-name-mismatch",
    "general-invalid-date",
    "general-date-range-logic",
    "general-term-variant",
)
GENERAL_LOCAL_SEMANTIC_RULE_IDS = (
    "general-typo",
    "general-name-error",
    "general-grammar",
    "general-punctuation",
    "general-incomplete",
    "general-duplicate",
)
GENERAL_DOCUMENT_SEMANTIC_RULE_IDS = ("general-logic-inconsistency",)


@dataclass(frozen=True)
class ReviewProfile:
    profile_id: str
    material_kind: str
    format_rule_ids: tuple[str, ...] = ()
    deterministic_rule_ids: tuple[str, ...] = ()
    local_semantic_rule_ids: tuple[str, ...] = ()
    document_semantic_rule_ids: tuple[str, ...] = ()
    specialized_rule_ids: tuple[str, ...] = ()

    @property
    def rule_ids(self) -> tuple[str, ...]:
        ordered = (
            *self.format_rule_ids,
            *self.deterministic_rule_ids,
            *self.local_semantic_rule_ids,
            *self.document_semantic_rule_ids,
            *self.specialized_rule_ids,
        )
        return tuple(dict.fromkeys(ordered))


def _general_profile(profile_id: str, material_kind: str) -> ReviewProfile:
    return ReviewProfile(
        profile_id=profile_id,
        material_kind=material_kind,
        format_rule_ids=COMMON_FORMAT_RULE_IDS,
        deterministic_rule_ids=GENERAL_DETERMINISTIC_RULE_IDS,
        local_semantic_rule_ids=GENERAL_LOCAL_SEMANTIC_RULE_IDS,
        document_semantic_rule_ids=GENERAL_DOCUMENT_SEMANTIC_RULE_IDS,
    )


GENERAL_TEXT_PROFILE = _general_profile("general_text", "text")
GENERAL_DOCX_PROFILE = _general_profile("general_docx", "docx")
GENERAL_HTML_PROFILE = _general_profile("general_html", "html")
NEICAN_PROFILE = ReviewProfile(
    profile_id="neican_docx",
    material_kind="docx",
    format_rule_ids=COMMON_FORMAT_RULE_IDS,
    specialized_rule_ids=(
        "title-truncated",
        "content-mismatch",
        "content-incomplete",
        "toc-mismatch",
        "content-out-of-scope",
        "content-wrong-section",
        "content-duplicate",
        "content-outdated",
        "weekly-body-format",
    ),
)
HALFMONTHLY_PROFILE = ReviewProfile(
    profile_id="halfmonthly_docx",
    material_kind="docx",
    format_rule_ids=COMMON_FORMAT_RULE_IDS,
    specialized_rule_ids=(
        "content-incomplete",
        "halfmonthly-date-mismatch",
        "halfmonthly-section-order",
        "halfmonthly-section-mismatch",
        "content-duplicate",
        "halfmonthly-leader-title",
        "halfmonthly-numbering",
        "halfmonthly-body-format",
    ),
)
PPT_PROFILE = ReviewProfile(
    profile_id="pptx",
    material_kind="pptx",
    specialized_rule_ids=(
        "ppt-sequence-duplicate",
        "ppt-sequence-reverse",
        "ppt-sequence-skip",
        "ppt-placeholder",
        "ppt-quote-pair",
        "ppt-consecutive-punctuation",
        "ppt-typo",
        "ppt-grammar",
        "ppt-punctuation",
        "ppt-name",
        "ppt-data-inconsistency",
        "ppt-content-inconsistency",
    ),
)
OFFICIAL_FORMAT_PROFILE = ReviewProfile(
    profile_id="official_format_docx",
    material_kind="docx",
    specialized_rule_ids=(
        "official-format-page",
        "official-format-title",
        "official-format-heading1",
        "official-format-heading2",
        "official-format-heading3",
        "official-format-body",
    ),
)
MULTI_FILE_PROFILE = ReviewProfile(
    profile_id="multi_file_docx",
    material_kind="multi_file",
    specialized_rule_ids=(
        "multi-file-attachment-duplicate",
        "multi-file-reference-missing",
        "multi-file-attachment-unreferenced",
        "multi-file-attachment-name-mismatch",
        "multi-file-logic-inconsistency",
    ),
)


REVIEW_PROFILES = {
    profile.profile_id: profile
    for profile in (
        GENERAL_TEXT_PROFILE,
        GENERAL_DOCX_PROFILE,
        GENERAL_HTML_PROFILE,
        NEICAN_PROFILE,
        HALFMONTHLY_PROFILE,
        PPT_PROFILE,
        OFFICIAL_FORMAT_PROFILE,
        MULTI_FILE_PROFILE,
    )
}

for profile in REVIEW_PROFILES.values():
    unknown = set(profile.rule_ids) - set(RULE_CATALOG)
    if unknown:
        raise RuntimeError(
            f"Review profile {profile.profile_id} has unknown rules: {sorted(unknown)}"
        )
