from __future__ import annotations

from collections.abc import Callable
import re

from .models import (
    PptElement,
    PptFinding,
    PptFindingCategory,
    PptReviewDocument,
)


_ARABIC_RE = re.compile(r"^[ \t]*(\d{1,3})([、.．])(?!\d)")
_PAREN_RE = re.compile(r"^[ \t]*[（(](\d{1,3})[）)]")
_CHINESE_RE = re.compile(r"^[ \t]*([一二三四五六七八九十]{1,3})、")
_PLACEHOLDER_RE = re.compile(
    r"(?<![A-Za-z])(?:X{2,}|待补充|待填写|待确认)(?![A-Za-z])",
    re.IGNORECASE,
)
_CONSECUTIVE_PUNCT_RE = re.compile(r"([，。！？；：、,.!?;:])\1+")
_QUOTE_PAIRS = (("“", "”"), ("‘", "’"), ("《", "》"), ("【", "】"))
_CHINESE_DIGITS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def check_ppt_rules(document: PptReviewDocument) -> tuple[PptFinding, ...]:
    """运行 PPT 自己维护的低误报确定性规则。"""
    findings: list[PptFinding] = []
    for slide in document.slides:
        for element in slide.elements:
            findings.extend(_check_element_sequence(element))
            findings.extend(_check_placeholders(element))
            findings.extend(_check_quote_pairs(element))
            findings.extend(_check_consecutive_punctuation(element))
    return tuple(_dedupe_findings(findings))


def _check_element_sequence(element: PptElement) -> list[PptFinding]:
    patterns: tuple[tuple[str, re.Pattern[str], Callable[[str], int]], ...] = (
        ("arabic", _ARABIC_RE, int),
        ("paren", _PAREN_RE, int),
        ("chinese", _CHINESE_RE, _parse_chinese_number),
    )
    previous_by_family_level: dict[tuple[str, int], int] = {}
    findings: list[PptFinding] = []
    for line in element.text.splitlines():
        matched_line = False
        for family, pattern, parser in patterns:
            match = pattern.match(line)
            if match is None:
                continue
            matched_line = True
            current = parser(match.group(1))
            level = _indent_width(line)
            key = (family, level)
            for deeper_key in tuple(previous_by_family_level):
                if deeper_key[1] > level:
                    del previous_by_family_level[deeper_key]
            previous = previous_by_family_level.get(key)
            previous_by_family_level[key] = current
            if previous is None or current == previous + 1:
                break
            target = match.group(0).strip()
            if current == previous:
                rule_id = "ppt-sequence-duplicate"
                description = f"同一组序号重复出现{current}"
            elif current < previous:
                rule_id = "ppt-sequence-reverse"
                description = f"同一组序号由{previous}倒序到{current}"
            else:
                rule_id = "ppt-sequence-skip"
                description = f"同一组序号由{previous}跳到{current}"
            findings.append(
                _finding(
                    element,
                    rule_id=rule_id,
                    category="sequence",
                    target_text=target,
                    description=description,
                )
            )
            break
        if not matched_line:
            previous_by_family_level.clear()
    return findings


def _indent_width(line: str) -> int:
    leading = line[: len(line) - len(line.lstrip(" \t"))]
    return len(leading.expandtabs(2))


def _check_placeholders(element: PptElement) -> list[PptFinding]:
    return [
        _finding(
            element,
            rule_id="ppt-placeholder",
            category="placeholder",
            target_text=match.group(0),
            description="存在未清理的占位内容",
        )
        for match in _PLACEHOLDER_RE.finditer(element.text)
    ]


def _check_quote_pairs(element: PptElement) -> list[PptFinding]:
    findings: list[PptFinding] = []
    for opener, closer in _QUOTE_PAIRS:
        opener_count = element.text.count(opener)
        closer_count = element.text.count(closer)
        if opener_count == closer_count:
            continue
        target = opener if opener_count > closer_count else closer
        findings.append(
            _finding(
                element,
                rule_id="ppt-quote-pair",
                category="punctuation",
                target_text=target,
                description="引号或成对标点未配对",
            )
        )
    return findings


def _check_consecutive_punctuation(element: PptElement) -> list[PptFinding]:
    return [
        _finding(
            element,
            rule_id="ppt-consecutive-punctuation",
            category="punctuation",
            target_text=match.group(0),
            description="连续重复使用相同标点",
        )
        for match in _CONSECUTIVE_PUNCT_RE.finditer(element.text)
    ]


def _parse_chinese_number(value: str) -> int:
    if "十" not in value:
        return _CHINESE_DIGITS[value]
    left, _, right = value.partition("十")
    tens = _CHINESE_DIGITS.get(left, 1) if left else 1
    ones = _CHINESE_DIGITS.get(right, 0) if right else 0
    return tens * 10 + ones


def _finding(
    element: PptElement,
    *,
    rule_id: str,
    category: PptFindingCategory,
    target_text: str,
    description: str,
) -> PptFinding:
    return PptFinding(
        rule_id=rule_id,
        category=category,
        slide_number=element.slide_number,
        element_id=element.element_id,
        target_text=target_text,
        description=description,
    )


def _dedupe_findings(findings: list[PptFinding]) -> list[PptFinding]:
    seen: set[tuple[str, int, str, str]] = set()
    result: list[PptFinding] = []
    for finding in findings:
        key = (
            finding.rule_id,
            finding.slide_number,
            finding.element_id,
            finding.target_text,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result
