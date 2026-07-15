"""通用审核错误标记器.

给 .docx 文档中的错误位置添加红色高亮和批注.
仅用于通用审核模块.

标注策略:
  - 错别字 / 名称错误 / 语病 / 内容不完整 / 重复内容 → 标红错误所在的整句话
  - 标点符号 / 连续标点 / 引号不成对 / 中英文标点混用 / 数字单位空格 →
    标红"错误标点及前面半句话"(从上一个较大停顿到错误位置后少量字符)
"""

from __future__ import annotations

import re
from copy import deepcopy
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from .reviewer import Finding
from .parser import iter_reviewable_paragraphs, paragraph_text, open_docx_sanitized


_ERROR_COLOR = RGBColor(255, 0, 0)

# 需要按"半句话"标注的规则(标点/格式类)
_HALF_SENTENCE_RULE_IDS = {
    "punctuation-check",
}
_EXACT_TARGET_RULE_IDS = {
    "general-typo",
    "general-name-error",
    "general-grammar",
    "general-punctuation",
    "general-incomplete",
    "general-duplicate",
    "general-logic-inconsistency",
    "general-term-variant",
    "consecutive-punct",
    "mixed-punct",
    "num-unit",
}

_SENTENCE_END_PUNCT = "。！？；\n"
_PAUSE_PUNCT = "，、；"

_MAX_QUOTED_LEN = 30
_MAX_DESC_WORD_LEN = 12
_MAX_ORIGINAL_PREFIX_LEN = 20
_MAX_SPLIT_ITERATIONS = 3
_SIGNIFICANT_SPACE_TARGET_RE = re.compile(
    r"^[，。；：、！？,.;:!?][ \t\u00a0\u3000]+$"
)


def _target_text_for_search(finding: Finding) -> str:
    raw_target = finding.target_text or ""
    if _SIGNIFICANT_SPACE_TARGET_RE.fullmatch(raw_target):
        return raw_target
    return raw_target.strip()


def _extract_quoted_text(text: str) -> str | None:
    """从文本中提取引号包围的内容."""
    patterns = [
        rf"《([^》]{{1,{_MAX_QUOTED_LEN}}})》",
        rf"'([^']{{1,{_MAX_QUOTED_LEN}}})'",
        rf'\"([^\"]{{1,{_MAX_QUOTED_LEN}}})\"',
        rf"【([^】]{{1,{_MAX_QUOTED_LEN}}})】",
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            return matches[-1]
    return None


def _get_search_key(finding: Finding) -> str:
    """获取用于在段落中定位错误的搜索关键词.

    优先顺序:
      1. LLM 返回的 target_text
      2. description 中的引号内容
      3. description 中的中文词组
      4. original_text 前缀
    """
    target_text = _target_text_for_search(finding)
    if target_text:
        return target_text

    description = finding.description or ""
    quoted = _extract_quoted_text(description)
    if quoted:
        return quoted

    candidates = re.findall(
        rf"[一-龥\d]{{2,{_MAX_DESC_WORD_LEN}}}", description
    )
    if candidates:
        return max(candidates, key=len)

    original = (finding.original_text or "").strip()
    return original[:_MAX_ORIGINAL_PREFIX_LEN]


def _paragraph_full_text(paragraph: Paragraph) -> str:
    """获取段落完整文本."""
    return paragraph_text(paragraph)


def _build_run_index(paragraph: Paragraph) -> list[tuple[int, int, Run]]:
    """建立 run 在段落中的字符偏移索引."""
    index: list[tuple[int, int, Run]] = []
    offset = 0
    for run in paragraph.runs:
        length = len(run.text)
        index.append((offset, offset + length, run))
        offset += length
    return index


def _find_best_run(paragraph: Paragraph, search_key: str) -> Run | None:
    """在段落中找到包含 search_key 起点的 run，不做模糊猜测."""
    if not search_key:
        return None

    for run in paragraph.runs:
        if search_key in run.text:
            return run

    full_text = _paragraph_full_text(paragraph)
    target_start = full_text.find(search_key)
    if target_start < 0:
        return None
    for run_start, run_end, run in _build_run_index(paragraph):
        if run_start <= target_start < run_end:
            return run
    return None


def _find_sentence_start(text: str, pos: int) -> int:
    """找到 pos 所在句子的起点(搜索范围严格在 pos 之前)."""
    for i in range(pos - 1, -1, -1):
        if text[i] in _SENTENCE_END_PUNCT:
            return i + 1
    return 0


def _find_sentence_end(text: str, pos: int) -> int:
    """找到包含 pos 的句子终点(包含句末标点)."""
    for i in range(pos, len(text)):
        if text[i] in _SENTENCE_END_PUNCT:
            return i + 1
    return len(text)


def _find_clause_start(text: str, pos: int) -> int:
    """找到 pos 之前最近的较大停顿(半句话起点)."""
    for i in range(pos - 1, -1, -1):
        if text[i] in _PAUSE_PUNCT:
            return i + 1
    return _find_sentence_start(text, pos)


def _sentence_range(
    text: str,
    target_text: str,
    rule_id: str,
    description: str = "",
    target_position: int | None = None,
) -> tuple[int, int] | None:
    """计算应标红的文本区间.

    返回 (start, end) 字符偏移. 找不到目标返回 None.
    target_position 由长原文片段解析得到时，可精确区分同段重复短目标。
    """
    if not target_text:
        return None

    pos = target_position if target_position is not None else text.find(target_text)
    if pos < 0:
        return None

    end_pos = pos + len(target_text)

    if (
        rule_id.startswith("general-")
        or rule_id.startswith("official-format-")
        or rule_id.startswith("multi-file-")
        or rule_id in _EXACT_TARGET_RULE_IDS
    ):
        return pos, end_pos

    if rule_id == "quote-pair":
        quote_match = re.search(r"引号'(.{1})'", description)
        quote_char = quote_match.group(1) if quote_match else target_text[:1]
        if target_text.startswith(quote_char):
            return pos, pos + 1
        if target_text.endswith(quote_char):
            quote_pos = end_pos - 1
            return quote_pos, quote_pos + 1
        inner_pos = target_text.find(quote_char)
        if inner_pos >= 0:
            quote_pos = pos + inner_pos
            return quote_pos, quote_pos + 1
        return pos, min(pos + 1, len(text))

    if rule_id in _HALF_SENTENCE_RULE_IDS:
        start = _find_clause_start(text, pos)
        end = end_pos
    else:
        start = _find_sentence_start(text, pos)
        end = _find_sentence_end(text, end_pos)

    start = max(0, start)
    end = min(len(text), end)
    if start >= end:
        return None

    return start, end


def _set_run_text(element, text: str) -> None:
    """设置 run 元素内所有 w:t 节点的文本.

    简单 run 只有一个 w:t;复杂 run 保留第一个,其余清空.
    """
    t_els = element.findall(qn("w:t"))
    if not t_els:
        return
    t_els[0].text = text
    for t_el in t_els[1:]:
        t_el.text = ""


def _split_run(paragraph: Paragraph, run: Run, offset: int) -> Run:
    """在 run 的 offset 位置拆分,返回右侧新 run."""
    text = run.text
    if offset <= 0 or offset >= len(text):
        return run

    left_text = text[:offset]
    right_text = text[offset:]

    run_element = run._element
    new_element = deepcopy(run_element)

    _set_run_text(run_element, left_text)
    _set_run_text(new_element, right_text)

    run_element.addnext(new_element)
    return Run(new_element, paragraph)


def _add_comment(run: Run, text: str) -> None:
    """给 run 添加批注,失败则静默忽略."""
    try:
        run.part.document.add_comment(run, text=text, author="M-Agent")
    except (ValueError, AttributeError):
        pass


def _build_locator_text(finding: Finding) -> str:
    """生成可直接在 Word 中搜索的连续原文片段."""
    original = (finding.original_text or "").strip()
    target = _target_text_for_search(finding)
    if not original:
        return target
    if not target or target not in original:
        return original[:180]

    target_pos = original.find(target)
    sentence_start = _find_sentence_start(original, target_pos)
    sentence_end = _find_sentence_end(original, target_pos + len(target))
    sentence = original[sentence_start:sentence_end].strip()
    if sentence and len(sentence) <= 180:
        return sentence

    start = max(0, target_pos - 60)
    end = min(len(original), target_pos + len(target) + 60)
    return original[start:end].strip()


def _humanize_description(description: str) -> str:
    """移除用户无法使用的内部段号，改为自然语言引用."""
    text = re.sub(r"第\s*\d+\s*段", "文中另一处", description)
    text = re.sub(r"段落\s*\d+", "文中另一处", text)
    return re.sub(r"paragraph\s*\d+", "文中另一处", text, flags=re.IGNORECASE)


def _build_comment_text(index: int, finding: Finding) -> str:
    """生成包含可搜索原文的批注，不向用户展示内部段号."""
    description = _humanize_description(finding.description)
    locator = _build_locator_text(finding)
    if locator:
        return f"错误{index}: {description}\n定位原文：{locator}"
    return f"错误{index}: {description}"


def _mark_text_range(
    paragraph: Paragraph,
    start: int,
    end: int,
    comment_text: str,
) -> bool:
    """把段落内 [start, end) 区间精确标红并加批注."""
    if start >= end:
        return False

    # 每次只拆分一个边界,最多拆分 start 和 end 两个边界
    for _ in range(_MAX_SPLIT_ITERATIONS):
        index = _build_run_index(paragraph)

        split_done = False
        for run_start, run_end, run in index:
            if run_start <= start < run_end and start > run_start:
                _split_run(paragraph, run, start - run_start)
                split_done = True
                break
            if run_start < end <= run_end and end < run_end:
                _split_run(paragraph, run, end - run_start)
                split_done = True
                break

        if not split_done:
            break
    else:
        return False

    index = _build_run_index(paragraph)
    first_marked_run: Run | None = None
    for run_start, run_end, run in index:
        if run_start >= start and run_end <= end:
            run.font.color.rgb = _ERROR_COLOR
            if first_marked_run is None:
                first_marked_run = run

    if first_marked_run is not None:
        _add_comment(first_marked_run, comment_text)

    return first_marked_run is not None


def _fallback_mark_run(
    paragraph: Paragraph,
    finding: Finding,
    comment_text: str,
    target_start: int | None = None,
) -> bool:
    """精确拆分失败时仍落批注，备注提供可搜索原文供人工定位."""
    search_key = _get_search_key(finding)
    run = None
    if target_start is not None:
        for run_start, run_end, candidate in _build_run_index(paragraph):
            if run_start <= target_start < run_end:
                run = candidate
                break
    if run is None and search_key and search_key in _paragraph_full_text(paragraph):
        run = _find_best_run(paragraph, search_key)
    if run is None:
        run = next((candidate for candidate in paragraph.runs if candidate.text), None)
    if run is not None:
        run.font.color.rgb = _ERROR_COLOR
        _add_comment(run, comment_text)
        return True
    return False


def _resolve_finding_paragraph_index(
    finding: Finding,
    paragraph_texts: list[str],
    indexes_by_text: dict[str, list[int]],
) -> int | None:
    """先按长原文内容定位，再用内部段号辅助消歧和保底."""
    claimed_index = finding.paragraph_index
    expected_text = (finding.original_text or "").strip()
    search_key = _get_search_key(finding)
    locator_text = _build_locator_text(finding)

    candidates: list[int] = []
    if expected_text:
        candidates = list(indexes_by_text.get(expected_text, []))
    if expected_text and not candidates:
        candidates = [
            index
            for index, text in enumerate(paragraph_texts)
            if expected_text in text.strip()
        ]
    if locator_text and not candidates:
        candidates = [
            index
            for index, text in enumerate(paragraph_texts)
            if locator_text in text
        ]
    if search_key and not candidates:
        candidates = [
            index
            for index, text in enumerate(paragraph_texts)
            if search_key in text
        ]

    if search_key:
        matching_target = [
            index for index in candidates if search_key in paragraph_texts[index]
        ]
        if matching_target:
            candidates = matching_target

    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        if claimed_index in candidates:
            return claimed_index
        return min(candidates, key=lambda index: abs(index - claimed_index))

    # 内容已发生变化时仍按内部段号落批注，批注里提供原始可搜索文本。
    if 0 <= claimed_index < len(paragraph_texts):
        return claimed_index
    return None


def _resolve_target_start(
    paragraph_text: str,
    finding: Finding,
    target_text: str,
) -> int | None:
    """用长原文片段区分同一段内重复出现的短目标."""
    locator_text = _build_locator_text(finding)
    if locator_text and locator_text in paragraph_text and target_text in locator_text:
        locator_start = paragraph_text.find(locator_text)
        return locator_start + locator_text.find(target_text)

    positions = [
        match.start() for match in re.finditer(re.escape(target_text), paragraph_text)
    ]
    if len(positions) == 1:
        return positions[0]
    if positions:
        return positions[0]
    return None


def mark_errors_in_docx(
    input_path: Path | str,
    output_path: Path | str,
    findings: Iterable[Finding],
) -> Path:
    """在 .docx 中标记审核发现.

    通用审核优先只标红精确错误片段；找不到原文时宁可不标.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    document = open_docx_sanitized(input_path)
    reviewable_paragraphs = list(iter_reviewable_paragraphs(document))
    paragraph_texts = [_paragraph_full_text(paragraph) for paragraph in reviewable_paragraphs]
    indexes_by_text: dict[str, list[int]] = defaultdict(list)
    for paragraph_index, text in enumerate(paragraph_texts):
        indexes_by_text[text.strip()].append(paragraph_index)

    paragraph_text_cache: dict[int, str] = {}

    for idx, finding in enumerate(findings, 1):
        para_index = _resolve_finding_paragraph_index(
            finding,
            paragraph_texts,
            indexes_by_text,
        )
        if para_index is None:
            continue

        paragraph = reviewable_paragraphs[para_index]
        if not paragraph.runs:
            continue

        comment_text = _build_comment_text(idx, finding)

        if para_index not in paragraph_text_cache:
            paragraph_text_cache[para_index] = _paragraph_full_text(paragraph)
        paragraph_text = paragraph_text_cache[para_index]

        target_text = _target_text_for_search(finding)
        if not target_text:
            target_text = _get_search_key(finding)
        target_start = (
            _resolve_target_start(paragraph_text, finding, target_text)
            if target_text
            else None
        )

        marked = False
        try:
            locator_text = _build_locator_text(finding)
            if target_start is None and locator_text in paragraph_text:
                locator_start = paragraph_text.find(locator_text)
                marked = _mark_text_range(
                    paragraph,
                    locator_start,
                    locator_start + len(locator_text),
                    comment_text,
                )
            text_range = _sentence_range(
                paragraph_text,
                target_text,
                finding.rule_id,
                finding.description,
                target_start,
            )
            if not marked and text_range is not None:
                start, end = text_range
                marked = _mark_text_range(paragraph, start, end, comment_text)
        except (ValueError, AttributeError):
            marked = False

        if not marked:
            _fallback_mark_run(
                paragraph,
                finding,
                comment_text,
                target_start=target_start,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    return output_path
