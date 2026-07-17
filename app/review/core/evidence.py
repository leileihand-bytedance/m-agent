"""Exact-evidence helpers independent from review business rules."""

from __future__ import annotations

from .models import EvidenceRef, Finding, ReviewIssue, SourceKind, SourceLocation


def build_paragraph_evidence(
    paragraphs: list[str] | tuple[str, ...],
    *,
    paragraph_index: int,
    target_text: str,
    source_kind: SourceKind,
) -> EvidenceRef | None:
    """Build evidence only when the claimed text exists in the claimed unit."""
    if paragraph_index < 0 or paragraph_index >= len(paragraphs):
        return None
    exact_text = target_text
    context = paragraphs[paragraph_index]
    if not exact_text.strip() or exact_text not in context:
        return None
    unit_kind = "html_block" if source_kind == "html" else "paragraph"
    return EvidenceRef(
        location=SourceLocation(
            source_kind=source_kind,
            unit_kind=unit_kind,
            unit_id=str(paragraph_index),
        ),
        exact_text=exact_text,
        context=context,
    )


def paragraph_finding_to_issue(
    finding: Finding,
    paragraphs: list[str] | tuple[str, ...],
    *,
    source_kind: SourceKind,
) -> ReviewIssue | None:
    """Adapt a verified paragraph finding to the canonical issue contract."""
    evidence = build_paragraph_evidence(
        paragraphs,
        paragraph_index=finding.paragraph_index,
        target_text=finding.target_text,
        source_kind=source_kind,
    )
    if evidence is None:
        return None
    return ReviewIssue(
        rule_id=finding.rule_id,
        description=finding.description,
        primary_evidence=evidence,
    )
