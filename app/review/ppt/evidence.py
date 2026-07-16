from __future__ import annotations

from collections.abc import Iterable
import re

from .models import (
    PptCrossCandidate,
    PptElement,
    PptFinding,
    PptLocalCandidate,
    PptReviewDocument,
)
from .text_policy import factual_description


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
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})年?")
_PERIOD_RE = re.compile(
    r"上半年|下半年|全年|年初|年末|第一季度|第二季度|第三季度|第四季度|"
    r"一季度|二季度|三季度|四季度|Q[1-4]|(?:[1-9]|1[0-2])月",
    re.IGNORECASE,
)
_UNIT_RE = re.compile(
    r"(?<=\d)(?:个百分点|％|%|万户|户|万人|人|万元|亿元|元|万吨|吨|"
    r"万公里|公里|万件|件|万台|台)"
)
_TARGET_SCOPE_RE = re.compile(r"目标|计划|预计|预测|预算|拟|力争")
_ACTUAL_SCOPE_RE = re.compile(r"实际|已完成|实现|截至|达到")


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
        description=factual_description(candidate.category, candidate.description),
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
    if _has_explicit_scope_conflict(candidate.target_text, candidate.related_text):
        return None

    return PptFinding(
        rule_id=f"ppt-{candidate.category.replace('_', '-')}",
        category=candidate.category,
        slide_number=candidate.slide_number,
        element_id=candidate.element_id,
        target_text=candidate.target_text,
        description=factual_description(candidate.category, candidate.description),
        related_slide_number=candidate.related_slide_number,
        related_element_id=candidate.related_element_id,
        related_text=candidate.related_text,
    )


def dedupe_findings(findings: Iterable[PptFinding]) -> tuple[PptFinding, ...]:
    """本地问题按单边证据去重，跨处问题按完整双边证据去重。"""
    selected: dict[tuple[object, ...], PptFinding] = {}
    order: list[tuple[object, ...]] = []
    for finding in findings:
        primary_key = (
            finding.slide_number,
            finding.element_id,
            finding.target_text,
        )
        is_cross = finding.related_slide_number is not None
        key: tuple[object, ...]
        if is_cross:
            key = (
                "cross",
                *primary_key,
                finding.related_slide_number,
                finding.related_element_id,
                finding.related_text,
            )
            local_key = ("local", *primary_key)
            local = selected.get(local_key)
            if (
                local is not None
                and _CATEGORY_PRIORITY[finding.category]
                < _CATEGORY_PRIORITY[local.category]
            ):
                del selected[local_key]
                order.remove(local_key)
        else:
            key = ("local", *primary_key)
            stronger_cross = next(
                (
                    item
                    for item in selected.values()
                    if item.related_slide_number is not None
                    and (
                        item.slide_number,
                        item.element_id,
                        item.target_text,
                    )
                    == primary_key
                    and _CATEGORY_PRIORITY[item.category]
                    < _CATEGORY_PRIORITY[finding.category]
                ),
                None,
            )
            if stronger_cross is not None:
                continue
        previous = selected.get(key)
        if previous is None:
            selected[key] = finding
            order.append(key)
            continue
        if _CATEGORY_PRIORITY[finding.category] < _CATEGORY_PRIORITY[previous.category]:
            selected[key] = finding
    return tuple(selected[key] for key in order)


def _has_explicit_scope_conflict(first: str, second: str) -> bool:
    if _different_explicit_tokens(_YEAR_RE, first, second):
        return True
    if _different_explicit_tokens(_PERIOD_RE, first, second):
        return True
    if _different_explicit_tokens(_UNIT_RE, first, second):
        return True

    first_target = bool(_TARGET_SCOPE_RE.search(first))
    second_target = bool(_TARGET_SCOPE_RE.search(second))
    first_actual = bool(_ACTUAL_SCOPE_RE.search(first))
    second_actual = bool(_ACTUAL_SCOPE_RE.search(second))
    return (
        first_target
        and not first_actual
        and second_actual
        and not second_target
    ) or (
        second_target
        and not second_actual
        and first_actual
        and not first_target
    )


def _different_explicit_tokens(
    pattern: re.Pattern[str],
    first: str,
    second: str,
) -> bool:
    first_tokens = {
        (match.group(1) if match.lastindex else match.group(0)).lower()
        for match in pattern.finditer(first)
    }
    second_tokens = {
        (match.group(1) if match.lastindex else match.group(0)).lower()
        for match in pattern.finditer(second)
    }
    return bool(first_tokens and second_tokens and first_tokens != second_tokens)


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
