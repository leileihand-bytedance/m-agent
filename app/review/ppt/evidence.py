from __future__ import annotations

from collections.abc import Iterable

from .models import (
    PptCrossCandidate,
    PptElement,
    PptFinding,
    PptLocalCandidate,
    PptReviewDocument,
)


_CATEGORY_PRIORITY = {
    "data_inconsistency": 0,
    "content_inconsistency": 1,
    "name": 2,
    "typo": 3,
    "sequence": 4,
    "placeholder": 5,
    "grammar": 6,
    "punctuation": 7,
}


def validate_local_candidate(
    document: PptReviewDocument,
    candidate: PptLocalCandidate,
) -> PptFinding | None:
    """只接受能在指定页、指定对象逐字找到的单点候选。"""
    element = _element_index(document).get(
        (candidate.slide_number, candidate.element_id)
    )
    if element is None or not _has_exact_source(element, candidate.target_text):
        return None
    return PptFinding(
        rule_id=f"ppt-{candidate.category.replace('_', '-')}",
        category=candidate.category,
        slide_number=candidate.slide_number,
        element_id=candidate.element_id,
        target_text=candidate.target_text,
        description=candidate.description,
    )


def validate_cross_candidate(
    document: PptReviewDocument,
    candidate: PptCrossCandidate,
) -> PptFinding | None:
    """只接受有双边原文且主体、时间、口径均相同的跨页候选。"""
    if not (
        candidate.same_subject
        and candidate.same_time_scope
        and candidate.same_metric_scope
    ):
        return None

    index = _element_index(document)
    first = index.get((candidate.slide_number, candidate.element_id))
    second = index.get(
        (candidate.related_slide_number, candidate.related_element_id)
    )
    if first is None or second is None:
        return None
    if not _has_exact_source(first, candidate.target_text):
        return None
    if not _has_exact_source(second, candidate.related_text):
        return None
    if (
        candidate.slide_number == candidate.related_slide_number
        and candidate.element_id == candidate.related_element_id
        and candidate.target_text == candidate.related_text
    ):
        return None

    return PptFinding(
        rule_id=f"ppt-{candidate.category.replace('_', '-')}",
        category=candidate.category,
        slide_number=candidate.slide_number,
        element_id=candidate.element_id,
        target_text=candidate.target_text,
        description=candidate.description,
        related_slide_number=candidate.related_slide_number,
        related_element_id=candidate.related_element_id,
        related_text=candidate.related_text,
    )


def dedupe_findings(findings: Iterable[PptFinding]) -> tuple[PptFinding, ...]:
    """同一原文只保留更具体的问题类型，并维持首次出现顺序。"""
    selected: dict[tuple[int, str, str], PptFinding] = {}
    order: list[tuple[int, str, str]] = []
    for finding in findings:
        key = (
            finding.slide_number,
            finding.element_id,
            finding.target_text,
        )
        previous = selected.get(key)
        if previous is None:
            selected[key] = finding
            order.append(key)
            continue
        if _CATEGORY_PRIORITY[finding.category] < _CATEGORY_PRIORITY[previous.category]:
            selected[key] = finding
    return tuple(selected[key] for key in order)


def _element_index(
    document: PptReviewDocument,
) -> dict[tuple[int, str], PptElement]:
    return {
        (element.slide_number, element.element_id): element
        for slide in document.slides
        for element in slide.elements
    }


def _has_exact_source(element: PptElement, target_text: str) -> bool:
    return bool(target_text.strip()) and target_text in element.text
