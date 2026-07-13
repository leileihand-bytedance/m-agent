"""通用审核代码规则检测器.

这部分规则只接入通用审核,优先处理误报低、可稳定定位的问题:
- general-placeholder: 占位内容未清理
- general-heading-seq-skip: 标题编号跳号
- general-heading-empty: 标题后无正文
- general-reference-missing: 附件/附表引用悬空
- general-attachment-name-mismatch: 附件编号和附件名称对不上
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING

from .general_term_checker import (
    GENERAL_TERM_VARIANT_RULE_ID,
    check_term_variants,
)

if TYPE_CHECKING:
    from .reviewer import Finding


GENERAL_DETERMINISTIC_RULE_IDS = (
    "general-placeholder",
    "general-heading-seq-skip",
    "general-heading-empty",
    "general-reference-missing",
    "general-attachment-name-mismatch",
    "general-invalid-date",
    "general-date-range-logic",
    GENERAL_TERM_VARIANT_RULE_ID,
)

_CN_NUM_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_LEVEL1_RE = re.compile(r"^([一二三四五六七八九十]+)[、.．]\s*(\S.*)$")
_LEVEL2_RE = re.compile(r"^（([一二三四五六七八九十]+)）\s*(\S.*)$")
_LEVEL3_DECIMAL_RE = re.compile(r"^([1-9]\d?)[.．](\d{1,2})(?!\d)\s*(\S.*)$")
_LEVEL3_RE = re.compile(r"^([1-9]\d?)[.．](?!\d)\s*(\S.*)$")
_LEVEL4_RE = re.compile(r"^（([1-9]\d?)）\s*(\S.*)$")

_EXPLICIT_PLACEHOLDER_RE = re.compile(
    r"【待(?:补充|确认|完善|定)】|"
    r"（待(?:补充|确认|完善|定)）|"
    r"\b(?:TBD|TODO|FIXME|XXX+)\b|"
    r"点击添加(?:标题|文本)|"
    r"此处输入"
)
_STANDALONE_PLACEHOLDERS = {"待补充", "待确认", "待完善", "待定"}
_NUMERIC_VALUE_RE = re.compile(r"^[+-]?\d+(?:[.．,]\d+)?%?$")
_QUESTIONNAIRE_PROMPT_RE = re.compile(
    r"贵行|请(?:填写|说明|提供|描述)|可补充|是否|有哪些|如何"
)
_QUESTIONNAIRE_OPTIONAL_ITEM_RE = re.compile(
    r"所面临的(?:困难|问题)|对策建议|待(?:填写|补充)|可选答"
)

_ATTACHMENT_REF_RE = re.compile(
    r"(?:见|详见|参见|请见)(附件|附表)\s*([一二三四五六七八九十0-9]+)?"
)
_ATTACHMENT_DECL_RE = re.compile(
    r"^(附件|附表)\s*([一二三四五六七八九十0-9]+)?(?:[:：\s]|$)"
)
_ATTACHMENT_DECL_WITH_TITLE_RE = re.compile(
    r"^(附件|附表)\s*([一二三四五六七八九十0-9]+)\s*[:：]\s*(.+?)\s*$"
)
_ATTACHMENT_INLINE_TITLE_RE = re.compile(
    r"(附件|附表)\s*([一二三四五六七八九十0-9]+)\s*[:：]?\s*[《“\"]([^》”\"\n]{2,80})[》”\"]"
)
_ATTACHMENT_INLINE_BARE_TITLE_RE = re.compile(
    r"(附件|附表)\s*([一二三四五六七八九十0-9]+)\s*[:：]?\s*"
    r"([^\s，。；;:：、]{2,80}?(?:通知|方案|办法|清单|名单|说明|报告|函|公告|章程|条例|制度|细则|合同|协议|表))"
    r"(?=[，。；;、]|$)"
)
_TITLE_THEN_ATTACHMENT_REF_RE = re.compile(
    r"[《“\"]([^》”\"\n]{2,80})[》”\"]\s*[（(]?\s*(附件|附表)\s*([一二三四五六七八九十0-9]+)\s*[)）]?"
)
_FULL_CN_DATE_RE = re.compile(r"(?<!\d)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日?")
_FULL_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_SHORT_CN_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})月\s*(\d{1,2})日")
_TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)")
_DATE_ONLY_TOKEN_PATTERN = (
    r"(?:\d{4}年\s*\d{1,2}月\s*\d{1,2}日?|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}月\s*\d{1,2}日)"
    r"(?:\s*[（(][^）)]{0,20}[）)])?"
)
_DATE_TOKEN_PATTERN = (
    _DATE_ONLY_TOKEN_PATTERN
    +
    r"(?:\s*(?:[01]?\d|2[0-3])[:：][0-5]\d)?"
)
_DATE_RANGE_RE = re.compile(
    rf"(?P<start>{_DATE_TOKEN_PATTERN})\s*(?:至|到|-|—|－|~|～)\s*(?P<end>{_DATE_TOKEN_PATTERN})"
)
_SAME_DAY_TIME_RANGE_RE = re.compile(
    rf"(?P<date>{_DATE_ONLY_TOKEN_PATTERN})\s*(?P<start_time>(?:[01]?\d|2[0-3])[:：][0-5]\d)"
    r"\s*(?:至|到|-|—|－|~|～)\s*(?P<end_text>[^，。；;\n]{0,20})"
)
_DATE_CLEANUP_PARENS_RE = re.compile(r"[（(](?:星期|周)[一二三四五六日天末]?[^）)]*[）)]")
_CROSS_DAY_MARKER_RE = re.compile(r"次日|翌日|第二天|隔日")


def _chinese_to_int(text: str) -> int:
    if text == "十":
        return 10
    if len(text) == 2 and text[0] == "十":
        return 10 + _CN_NUM_MAP.get(text[1], 0)
    if len(text) == 2 and text[1] == "十":
        return _CN_NUM_MAP.get(text[0], 0) * 10
    return _CN_NUM_MAP.get(text, 0)


def _parse_heading(text: str) -> tuple[int, int, str] | None:
    stripped = text.strip()
    if _NUMERIC_VALUE_RE.fullmatch(stripped):
        return None
    match = _LEVEL1_RE.match(stripped)
    if match:
        return 1, _chinese_to_int(match.group(1)), match.group(2).strip()

    match = _LEVEL2_RE.match(stripped)
    if match:
        return 2, _chinese_to_int(match.group(1)), match.group(2).strip()

    match = _LEVEL3_DECIMAL_RE.match(stripped)
    if match:
        return 4, int(match.group(2)), match.group(3).strip()

    match = _LEVEL3_RE.match(stripped)
    if match:
        return 3, int(match.group(1)), match.group(2).strip()

    match = _LEVEL4_RE.match(stripped)
    if match:
        return 4, int(match.group(1)), match.group(2).strip()

    return None


def _is_heading_only_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if _QUESTIONNAIRE_PROMPT_RE.search(stripped):
        return False
    for sep in ("：", ":"):
        if sep not in stripped:
            continue
        _, value = stripped.split(sep, 1)
        if value.strip():
            return False
    if any(mark in stripped for mark in "。！？；"):
        return False
    return len(stripped) <= 80


def _normalize_attachment_title(text: str) -> str:
    cleaned = text.strip().strip("。；;，,")
    cleaned = cleaned.strip("《》“”\"'（）()[]【】")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def _attachment_titles_look_consistent(referenced: str, declared: str) -> bool:
    if referenced == declared:
        return True
    shorter, longer = sorted((referenced, declared), key=len)
    return len(shorter) >= 4 and shorter in longer


def _iter_nonempty_lines(paragraph: str) -> list[str]:
    """兼容 Word 段内软换行和历史文字消息合并段落."""
    return [line.strip() for line in paragraph.splitlines() if line.strip()]


def _iter_attachment_named_references(paragraph: str) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for match in _ATTACHMENT_INLINE_TITLE_RE.finditer(paragraph):
        refs.append((f"{match.group(1)}{match.group(2)}", match.group(3)))
    for match in _ATTACHMENT_INLINE_BARE_TITLE_RE.finditer(paragraph):
        refs.append((f"{match.group(1)}{match.group(2)}", match.group(3)))
    for match in _TITLE_THEN_ATTACHMENT_REF_RE.finditer(paragraph):
        refs.append((f"{match.group(2)}{match.group(3)}", match.group(1)))
    return refs


def _build_valid_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _is_always_impossible_month_day(month: int, day: int) -> bool:
    if month < 1 or month > 12 or day < 1:
        return True
    if month == 2:
        return day > 29
    if month in {4, 6, 9, 11}:
        return day > 30
    return day > 31


def _parse_date_token(token: str, *, fallback_year: int | None = None) -> tuple[date | None, bool]:
    cleaned = _DATE_CLEANUP_PARENS_RE.sub("", token).strip()

    match = _FULL_CN_DATE_RE.search(cleaned)
    if match:
        return _build_valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3))), True

    match = _FULL_ISO_DATE_RE.search(cleaned)
    if match:
        return _build_valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3))), True

    match = _SHORT_CN_DATE_RE.search(cleaned)
    if not match or fallback_year is None:
        return None, False
    return _build_valid_date(fallback_year, int(match.group(1)), int(match.group(2))), False


def _parse_time_token(token: str) -> tuple[int, int] | None:
    match = _TIME_RE.search(token)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def check_placeholder_content(paragraphs: list[str]) -> list["Finding"]:
    """检测明显未清理的占位内容."""
    from .reviewer import Finding

    findings: list[Finding] = []
    for idx, paragraph in enumerate(paragraphs):
        match = _EXPLICIT_PLACEHOLDER_RE.search(paragraph)
        target = match.group(0) if match else ""
        normalized = paragraph.strip().strip("：:，,。；; ")
        if not target and normalized in _STANDALONE_PLACEHOLDERS:
            target = normalized

        if not target:
            continue

        findings.append(
            Finding(
                rule_id="general-placeholder",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=paragraph,
                description="存在未清理的占位内容",
                target_text=target,
            )
        )

    return findings


def check_heading_sequence(paragraphs: list[str]) -> list["Finding"]:
    """检测同层级标题/列表编号跳号."""
    from .reviewer import Finding

    findings: list[Finding] = []
    current_path: dict[int, int | None] = {1: None, 2: None, 3: None}
    prev_by_key: dict[tuple[int, tuple[int | None, ...]], int] = {}

    for idx, paragraph in enumerate(paragraphs):
        parsed = _parse_heading(paragraph)
        if not parsed:
            continue

        level, number, rest_text = parsed
        should_report_gap = _is_heading_only_text(rest_text)
        if level == 1:
            parent_key: tuple[int | None, ...] = ()
        elif level == 2:
            parent_key = (current_path[1],)
        elif level == 3:
            parent_key = (current_path[1], current_path[2])
        else:
            parent_key = (current_path[1], current_path[2], current_path[3])

        key = (level, parent_key)
        previous = prev_by_key.get(key)
        if should_report_gap and previous is not None and number > previous + 1:
            findings.append(
                Finding(
                    rule_id="general-heading-seq-skip",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=paragraph,
                    description=f"同层级标题编号跳号：前一项是 {previous}，当前是 {number}",
                    target_text=paragraph.strip(),
                )
            )

        prev_by_key[key] = number
        if level == 1:
            current_path[1] = number
            current_path[2] = None
            current_path[3] = None
        elif level == 2:
            current_path[2] = number
            current_path[3] = None
        elif level == 3:
            current_path[3] = number

    return findings


def check_empty_headings(paragraphs: list[str]) -> list["Finding"]:
    """检测标题后没有正文."""
    from .reviewer import Finding

    findings: list[Finding] = []
    is_questionnaire = any(
        "问卷" in paragraph for paragraph in paragraphs[:30]
    )
    for idx, paragraph in enumerate(paragraphs):
        parsed = _parse_heading(paragraph)
        if not parsed:
            continue

        current_level, _, rest_text = parsed
        if is_questionnaire and _QUESTIONNAIRE_OPTIONAL_ITEM_RE.search(rest_text):
            continue
        if not _is_heading_only_text(rest_text):
            continue
        next_text = ""
        for later in paragraphs[idx + 1:]:
            if later.strip():
                next_text = later
                break

        if not next_text:
            findings.append(
                Finding(
                    rule_id="general-heading-empty",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=paragraph,
                    description="该标题后没有正文内容",
                    target_text=paragraph.strip(),
                )
            )
            continue

        next_heading = _parse_heading(next_text)
        if next_heading and next_heading[0] <= current_level:
            findings.append(
                Finding(
                    rule_id="general-heading-empty",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=paragraph,
                    description="该标题后直接进入同级或更高一级标题，缺少正文内容",
                    target_text=paragraph.strip(),
                )
            )

    return findings


def check_missing_references(paragraphs: list[str]) -> list["Finding"]:
    """检测附件/附表引用悬空."""
    from .reviewer import Finding

    declared_exact: set[str] = set()
    declared_kinds: set[str] = set()
    for paragraph in paragraphs:
        for line in _iter_nonempty_lines(paragraph):
            match = _ATTACHMENT_DECL_RE.match(line)
            if not match:
                continue
            kind = match.group(1)
            number = match.group(2) or ""
            declared_kinds.add(kind)
            if number:
                declared_exact.add(f"{kind}{number}")

    findings: list[Finding] = []
    for idx, paragraph in enumerate(paragraphs):
        for match in _ATTACHMENT_REF_RE.finditer(paragraph):
            kind = match.group(1)
            number = match.group(2) or ""
            target = f"{kind}{number}" if number else kind

            if number and target in declared_exact:
                continue
            if not number and kind in declared_kinds:
                continue

            findings.append(
                Finding(
                    rule_id="general-reference-missing",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=paragraph,
                    description=f"文中提到{target}，但正文里未找到对应附件标题",
                    target_text=target,
                )
            )

    return findings


def check_attachment_name_mismatches(paragraphs: list[str]) -> list["Finding"]:
    """检测正文里引用的附件名称与附件标题不一致."""
    from .reviewer import Finding

    declared_titles: dict[str, str] = {}
    for paragraph in paragraphs:
        for line in _iter_nonempty_lines(paragraph):
            match = _ATTACHMENT_DECL_WITH_TITLE_RE.match(line)
            if not match:
                continue
            target = f"{match.group(1)}{match.group(2)}"
            normalized_title = _normalize_attachment_title(match.group(3))
            if normalized_title:
                declared_titles[target] = normalized_title

    findings: list[Finding] = []
    for idx, paragraph in enumerate(paragraphs):
        stripped = paragraph.strip()
        for target, referenced_title in _iter_attachment_named_references(stripped):
            declared_title = declared_titles.get(target)
            normalized_ref = _normalize_attachment_title(referenced_title)
            if not declared_title or not normalized_ref:
                continue
            if _attachment_titles_look_consistent(normalized_ref, declared_title):
                continue

            actual_target = next(
                (
                    declared_target
                    for declared_target, title in declared_titles.items()
                    if declared_target != target
                    and _attachment_titles_look_consistent(normalized_ref, title)
                ),
                None,
            )
            if actual_target:
                description = (
                    f"正文写{target}“{normalized_ref}”，但附件清单中"
                    f"“{normalized_ref}”实际为{actual_target}"
                )
            else:
                description = (
                    f"正文提到的附件名称“{normalized_ref}”与"
                    f"{target}标题“{declared_title}”不一致"
                )

            findings.append(
                Finding(
                    rule_id="general-attachment-name-mismatch",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=paragraph,
                    description=description,
                    target_text=target,
                )
            )

    return findings


def check_invalid_dates(paragraphs: list[str]) -> list["Finding"]:
    """检测明显不成立的日期表达."""
    from .reviewer import Finding

    findings: list[Finding] = []
    for idx, paragraph in enumerate(paragraphs):
        occupied_spans: list[tuple[int, int]] = []

        for pattern in (_FULL_CN_DATE_RE, _FULL_ISO_DATE_RE):
            for match in pattern.finditer(paragraph):
                occupied_spans.append(match.span())
                parsed = _build_valid_date(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                )
                if parsed is not None:
                    continue
                findings.append(
                    Finding(
                        rule_id="general-invalid-date",
                        paragraph_index=idx,
                        line_number=idx + 1,
                        original_text=paragraph,
                        description="日期不存在或不符合日历常识",
                        target_text=match.group(0),
                    )
                )

        for match in _SHORT_CN_DATE_RE.finditer(paragraph):
            start, end = match.span()
            if any(start < occupied_end and end > occupied_start for occupied_start, occupied_end in occupied_spans):
                continue
            if not _is_always_impossible_month_day(int(match.group(1)), int(match.group(2))):
                continue
            findings.append(
                Finding(
                    rule_id="general-invalid-date",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=paragraph,
                    description="日期不存在或不符合日历常识",
                    target_text=match.group(0),
                )
            )

    return findings


def check_date_range_logic(paragraphs: list[str]) -> list["Finding"]:
    """检测同一句里显式起止日期的前后逻辑错误."""
    from .reviewer import Finding

    findings: list[Finding] = []
    for idx, paragraph in enumerate(paragraphs):
        for match in _DATE_RANGE_RE.finditer(paragraph):
            start_token = match.group("start")
            end_token = match.group("end")

            start_date, _ = _parse_date_token(start_token, fallback_year=2024)
            fallback_year = start_date.year if start_date is not None else None
            end_date, end_has_year = _parse_date_token(end_token, fallback_year=fallback_year)
            if start_date is None or end_date is None:
                continue

            if end_date < start_date:
                if not end_has_year and end_date.month < start_date.month:
                    continue
                findings.append(
                    Finding(
                        rule_id="general-date-range-logic",
                        paragraph_index=idx,
                        line_number=idx + 1,
                        original_text=paragraph,
                        description="起始时间晚于结束时间，前后逻辑不一致",
                        target_text=match.group(0),
                    )
                )
                continue

            if end_date != start_date:
                continue

            start_time = _parse_time_token(start_token)
            end_time = _parse_time_token(end_token)
            if start_time and end_time and end_time < start_time:
                findings.append(
                    Finding(
                        rule_id="general-date-range-logic",
                        paragraph_index=idx,
                        line_number=idx + 1,
                        original_text=paragraph,
                        description="同一日期下结束时刻早于开始时刻，前后逻辑不一致",
                        target_text=match.group(0),
                    )
                )

        for match in _SAME_DAY_TIME_RANGE_RE.finditer(paragraph):
            end_text = match.group("end_text").strip()
            if _CROSS_DAY_MARKER_RE.search(end_text):
                continue
            if _FULL_CN_DATE_RE.search(end_text) or _FULL_ISO_DATE_RE.search(end_text) or _SHORT_CN_DATE_RE.search(end_text):
                continue

            start_time = _parse_time_token(match.group("start_time"))
            end_time = _parse_time_token(end_text)
            if not start_time or not end_time or end_time >= start_time:
                continue

            findings.append(
                Finding(
                    rule_id="general-date-range-logic",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=paragraph,
                    description="同一日期下结束时刻早于开始时刻，前后逻辑不一致",
                    target_text=match.group(0).strip(),
                )
            )

    return findings


def check_general_document_rules(paragraphs: list[str]) -> list["Finding"]:
    """运行通用审核新增的代码规则."""
    all_findings = []
    all_findings.extend(check_placeholder_content(paragraphs))
    all_findings.extend(check_heading_sequence(paragraphs))
    all_findings.extend(check_empty_headings(paragraphs))
    all_findings.extend(check_missing_references(paragraphs))
    all_findings.extend(check_attachment_name_mismatches(paragraphs))
    all_findings.extend(check_invalid_dates(paragraphs))
    all_findings.extend(check_date_range_logic(paragraphs))
    all_findings.extend(check_term_variants(paragraphs))
    all_findings.sort(key=lambda finding: finding.paragraph_index)
    return all_findings
