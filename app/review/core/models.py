"""Shared review result and evidence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SourceKind = Literal["text", "docx", "html", "pptx", "multi_file"]
UnitKind = Literal[
    "paragraph",
    "html_block",
    "ppt_element",
    "document",
    "file",
]


@dataclass(frozen=True)
class SourceLocation:
    """A format-neutral pointer to one reviewable source unit."""

    source_kind: SourceKind
    unit_kind: UnitKind
    unit_id: str
    page_number: int | None = None
    element_id: str = ""


@dataclass(frozen=True)
class EvidenceRef:
    """Exact source evidence attached to a review issue."""

    location: SourceLocation
    exact_text: str
    context: str = ""


@dataclass(frozen=True)
class ReviewIssue:
    """Canonical issue contract; output adapters keep legacy formats stable."""

    rule_id: str
    description: str
    primary_evidence: EvidenceRef
    related_evidence: tuple[EvidenceRef, ...] = ()
    category: str = ""


@dataclass(frozen=True)
class Finding:
    """Legacy paragraph finding used by Word, text, and HTML outputs."""

    rule_id: str
    paragraph_index: int
    line_number: int
    original_text: str
    description: str
    target_text: str = ""


@dataclass(frozen=True)
class ReviewResult:
    """Legacy paragraph result preserved at existing output boundaries."""

    findings: list[Finding]
    total_rules: int
    passed_rules: int
    filename: str
