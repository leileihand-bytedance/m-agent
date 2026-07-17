"""审核引擎.

两层架构:
  - 格式类规则(引号/数字单位/标点/目录序号) → format_checker.py 正则检测
  - 语义类规则(截断/错配/完整性/内容质量) → LLM CoT + 结构化输出

rules.md 是给 LLM 读的"审核清单",LLM 只处理语义类规则。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path

from docx.oxml.ns import qn

from .core.dedupe import dedupe_prefer_longer_description
from .core.evidence import canonicalize_paragraph_finding
from .core.metrics import ReviewRunMetrics
from .core.model_output import (
    collect_message_text,
    looks_like_valid_issue_json,
    parse_paragraph_findings,
)
from .core.model_runtime import create_model_message, run_with_retries
from .core.models import Finding, ReviewResult
from .format_checker import check_all_format_rules, check_format_rules
from .model_config import build_anthropic_client
from .parser import iter_reviewable_paragraphs, open_docx_sanitized
from .rules.profiles import NEICAN_PROFILE, ReviewProfile
from .toc_utils import (
    find_toc_range,
    normalize_toc_entry_text,
    strip_pageref,
)


@dataclass(frozen=True)
class ReviewEntry:
    """结构化的正文条目（标题 + 若干正文段落）。"""

    section: str
    title_index: int
    title_text: str
    body_indexes: tuple[int, ...]
    body_paragraphs: tuple[str, ...]


@dataclass(frozen=True)
class ReviewDocument:
    """供不同审核阶段复用的轻量文档结构。"""

    toc_entries: tuple[tuple[int, str], ...]
    entries: tuple[ReviewEntry, ...]


SECTION_ALIASES = {
    "党政要闻": "党政要闻",
    "监管动态": "监管动态",
    "同业动向": "同业动向",
    "同业动态": "同业动向",
    "市场观察": "市场观察",
    "前沿观点": "前沿观点",
}


SECTION_TITLES = (
    "党政要闻",
    "监管动态",
    "同业动向",
    "同业动态",
    "市场观察",
    "前沿观点",
)


PHASE_RULE_FALLBACKS = {
    "title-truncated": "\n".join(
        [
            "#### `title-truncated` 标题截断",
            "- 只看标题本身是否被砍断、语句不完整。",
            "- 标题只是比正文简略，不算截断。",
        ]
    ),
    "content-mismatch": "\n".join(
        [
            "#### `content-mismatch` 标题正文错配",
            "- 标题和正文说的必须是同一件事。",
            "- 会议名称、文件名称、人物活动如果不是同一件，就报错配。",
        ]
    ),
    "content-incomplete": "\n".join(
        [
            "#### `content-incomplete` 正文不完整",
            "- 正文在句中突然结束、缺宾语或结束语就报。",
            "- 完整句即使较短，也不要误报。",
        ]
    ),
    "toc-mismatch": "\n".join(
        [
            "#### `toc-mismatch` 目录正文不匹配",
            "- 检查目录里的章节名、标题和顺序，是否与正文对应得上。",
            "- 目录没刷新、目录还是旧标题，也算这一类问题。",
        ]
    ),
    "content-out-of-scope": "\n".join(
        [
            "#### `content-out-of-scope` 内容不在收录范围",
            "- 与银行经营管理、宏观经济政策完全无关的内容才报。",
        ]
    ),
    "content-wrong-section": "\n".join(
        [
            "#### `content-wrong-section` 内容放错板块",
            "- 判断内容主体应该归到哪个板块，再看是否放错。",
        ]
    ),
    "content-duplicate": "\n".join(
        [
            "#### `content-duplicate` 重复内容",
            "- 同一件事在周报里出现两次或以上才报。",
        ]
    ),
    "content-outdated": "\n".join(
        [
            "#### `content-outdated` 过时信息",
            "- 整篇内容明显早于周报时间范围，且没有近期动态时才报。",
        ]
    ),
}

_NEICAN_QUOTED_TEXT_RE = re.compile(r"[“\"'《【]([^”\"'》】]{1,30})[”\"'》】]")
_NEICAN_WORD_RE = re.compile(r"[一-龥A-Za-z0-9]{2,20}")
_TITLE_STYLE_NAMES = {"heading 3", "标题 3"}
_NEICAN_MATCH_NOISE = (
    "标题与正文内容不符",
    "标题内容与正文不符",
    "标题正文不匹配",
    "标题和正文讲的不是同一件事",
    "标题和正文不是同一件事",
    "标题和正文不匹配",
    "标题讲",
    "标题说",
    "标题为",
    "正文却是",
    "正文为",
    "正文讲",
    "正文是",
    "内容不符",
    "内容不匹配",
)
_NEICAN_INCOMPLETE_INTRO_SUFFIXES = (
    "原文：",
    "原文如下：",
    "全文如下：",
    "链接如下：",
    "具体如下：",
)
_NEICAN_DANGLING_CLAUSES = {
    "扎实",
    "切实",
    "持续",
    "继续",
    "进一步",
    "全面",
    "有序",
    "稳步",
    "积极",
    "深入",
    "加快",
    "推进",
    "推动",
    "抓好",
    "做好",
    "强化",
    "统筹",
    "深化",
    "促进",
    "维护",
    "保障",
    "实现",
}


def _normalize_section_title(text: str) -> str | None:
    return SECTION_ALIASES.get(text.strip())


def _is_section_title(text: str) -> bool:
    return _normalize_section_title(text) is not None


def _style_name(style) -> str:
    if style is None:
        return ""
    return str(getattr(style, "name", "") or "").strip()


def _normalize_style_name(style_name: str) -> str:
    return re.sub(r"\s+", " ", style_name.strip()).lower()


def _is_title_style(style_name: str) -> bool:
    return _normalize_style_name(style_name) in _TITLE_STYLE_NAMES


def _is_reference_line(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(("原文：", "来源：", "链接：", "原文链接："))


# ============================================================
# LLM 调用
# ============================================================

def _get_anthropic_client():
    """构造审核阶段使用的 anthropic 兼容 client."""
    return build_anthropic_client()


# 语义类规则 ID 列表(由 LLM 处理)
SEMANTIC_RULE_IDS = (
    "title-truncated",
    "content-mismatch",
    "content-incomplete",
    "toc-mismatch",
    "content-out-of-scope",
    "content-wrong-section",
    "content-duplicate",
    "content-outdated",
)

# 第一阶段规则（格式正则 + 基础内容LLM）
PHASE1_RULES = (
    "title-truncated",
    "content-mismatch",
    "content-incomplete",
)

# 第二阶段规则（深度内容LLM）
PHASE2_RULES = (
    "toc-mismatch",
    "content-out-of-scope",
    "content-wrong-section",
    "content-duplicate",
    "content-outdated",
)


def _find_toc_range(paragraphs: list[str]) -> tuple[int, int]:
    """返回目录区正文段落的起止位置。"""
    return find_toc_range(paragraphs)


def _clean_toc_text(text: str) -> str:
    return strip_pageref(text)


def _clip_text(text: str, max_chars: int = 260) -> str:
    normalized = re.sub(r"\s+", " ", text.strip())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3] + "..."


def _find_sentence_end_forward(text: str, start: int, end: int) -> int | None:
    limit = min(len(text), end)
    for idx in range(max(0, start), limit):
        if text[idx] in "。！？；?!":
            return idx + 1
    return None


def _find_sentence_end_backward(text: str, start: int, end: int) -> int | None:
    lower = max(0, start)
    upper = min(len(text), end)
    for idx in range(upper - 1, lower - 1, -1):
        if text[idx] in "。！？；?!":
            return idx + 1
    return None


def _summarize_phase1_head(text: str, max_chars: int) -> str:
    """尽量保留完整句，避免人为截断被误判成正文不完整。"""
    normalized = re.sub(r"\s+", " ", text.strip())
    if len(normalized) <= max_chars:
        return normalized

    suffix = " [后文略]"
    hard_limit = max_chars - len(suffix)
    if hard_limit <= 40:
        return normalized[:max_chars]

    forward_end = _find_sentence_end_forward(normalized, hard_limit, hard_limit + 120)
    if forward_end is not None:
        return normalized[:forward_end].rstrip() + suffix

    backward_end = _find_sentence_end_backward(normalized, 0, hard_limit)
    if backward_end is not None and backward_end >= max(80, hard_limit // 2):
        return normalized[:backward_end].rstrip() + suffix

    return normalized[:hard_limit].rstrip("，、：,:；; ") + suffix


def _summarize_phase1_tail(text: str, max_chars: int) -> str:
    """保留真实结尾，并显式标明前文省略，避免把中间裁剪当成源文错误。"""
    normalized = re.sub(r"\s+", " ", text.strip())
    if len(normalized) <= max_chars:
        return normalized

    prefix = "[前文略] "
    hard_limit = max_chars - len(prefix)
    if hard_limit <= 40:
        return prefix + normalized[-max(1, hard_limit):]

    start = len(normalized) - hard_limit
    sentence_start = _find_sentence_end_backward(normalized, max(0, start - 120), start)
    if sentence_start is not None:
        snippet = normalized[sentence_start:].lstrip()
        if len(snippet) <= hard_limit:
            return prefix + snippet

    return prefix + normalized[-hard_limit:]


def _extract_rule_blocks(rules_text: str) -> dict[str, str]:
    """从 rules.md 文本里抽出单条规则块。"""
    blocks: dict[str, str] = {}
    current_rule_id: str | None = None
    current_lines: list[str] = []

    for line in rules_text.splitlines():
        stripped = line.lstrip()
        heading_match = re.match(r"^#{3,4} .*`([a-z0-9-]+)`", stripped)
        if heading_match:
            if current_rule_id and current_lines:
                blocks[current_rule_id] = "\n".join(current_lines).strip()
            current_rule_id = heading_match.group(1)
            current_lines = [line]
            continue

        if current_rule_id is None:
            continue

        if stripped.startswith("## ") or stripped.startswith("### "):
            blocks[current_rule_id] = "\n".join(current_lines).strip()
            current_rule_id = None
            current_lines = []
            continue

        current_lines.append(line)

    if current_rule_id and current_lines:
        blocks[current_rule_id] = "\n".join(current_lines).strip()

    return blocks


def _build_phase_rule_reference(rules_text: str, rule_ids: tuple[str, ...]) -> str:
    """构造当前阶段需要的紧凑规则说明。"""
    blocks = _extract_rule_blocks(rules_text)
    selected = [
        PHASE_RULE_FALLBACKS.get(rule_id) or blocks.get(rule_id, "")
        for rule_id in rule_ids
    ]
    return "\n\n".join(block for block in selected if block).strip()


def _build_review_document(
    paragraphs: list[str],
    docx_path: Path | None = None,
) -> ReviewDocument:
    """把原始段落整理成目录区 + 正文条目。"""
    toc_start, toc_end = _find_toc_range(paragraphs)
    toc_entries = tuple(
        (idx, _clean_toc_text(paragraphs[idx]))
        for idx in range(toc_start, toc_end)
        if _clean_toc_text(paragraphs[idx])
    )

    style_names: list[str] = []
    if docx_path is not None and docx_path.exists():
        doc = open_docx_sanitized(docx_path)
        doc_paragraphs = list(iter_reviewable_paragraphs(doc))
        if len(doc_paragraphs) == len(paragraphs):
            style_names = [_style_name(paragraph.style) for paragraph in doc_paragraphs]

    entries: list[ReviewEntry] = []
    current_section: str | None = None
    title_index: int | None = None
    title_text: str | None = None
    body_indexes: list[int] = []
    body_paragraphs: list[str] = []

    def flush_current_entry() -> None:
        nonlocal title_index, title_text, body_indexes, body_paragraphs
        if current_section and title_index is not None and title_text is not None:
            entries.append(
                ReviewEntry(
                    section=current_section,
                    title_index=title_index,
                    title_text=title_text,
                    body_indexes=tuple(body_indexes),
                    body_paragraphs=tuple(body_paragraphs),
                )
            )
        title_index = None
        title_text = None
        body_indexes = []
        body_paragraphs = []

    for idx, paragraph in enumerate(paragraphs):
        stripped = paragraph.strip()
        if _is_section_title(stripped):
            flush_current_entry()
            current_section = stripped
            continue

        if current_section is None:
            continue

        style_name = style_names[idx] if idx < len(style_names) else ""
        is_title = _is_title_style(style_name) if style_names else _is_news_title(stripped)
        if is_title:
            flush_current_entry()
            title_index = idx
            title_text = stripped
            continue

        if title_index is None:
            continue

        body_indexes.append(idx)
        body_paragraphs.append(stripped)

    flush_current_entry()

    return ReviewDocument(
        toc_entries=toc_entries,
        entries=tuple(entries),
    )


def _render_phase1_context(document: ReviewDocument) -> str:
    """渲染第一阶段所需的正文上下文，不裁剪原文。"""
    lines = ["# 正文条目（按正文顺序）", ""]

    for entry in document.entries:
        lines.append(
            f"[标题段 {entry.title_index + 1} | 板块:{entry.section}] {entry.title_text}"
        )
        if not entry.body_paragraphs:
            lines.append("  [正文缺失]")
            lines.append("")
            continue

        for body_index, body_text in zip(entry.body_indexes, entry.body_paragraphs):
            lines.append(f"  [正文段 {body_index + 1}] {body_text}")

        lines.append("")

    return "\n".join(lines).strip()


def _render_phase2_context(document: ReviewDocument) -> str:
    """渲染第二阶段所需的目录和正文索引，不裁剪原文。"""
    lines = ["# 目录区", ""]

    for idx, text in document.toc_entries:
        lines.append(f"[目录段 {idx + 1}] {text}")

    lines.extend(["", "# 正文条目索引", ""])

    for entry in document.entries:
        lines.append(
            f"[板块:{entry.section} | 标题段 {entry.title_index + 1}] {entry.title_text}"
        )
        if entry.body_paragraphs:
            for body_index, body_text in zip(entry.body_indexes, entry.body_paragraphs):
                lines.append(f"  [正文段 {body_index + 1}] {body_text}")
        else:
            lines.append("  [正文缺失]")
        lines.append("")

    return "\n".join(lines).strip()


def _expected_toc_items(document: ReviewDocument, mode: str) -> list[str]:
    expected: list[str] = []
    current_section: str | None = None

    for entry in document.entries:
        if entry.section != current_section:
            current_section = entry.section
            if mode in {"section-only", "full"}:
                expected.append(current_section)
        if mode in {"title-only", "full"}:
            expected.append(entry.title_text.strip())

    return expected


def _actual_toc_items(document: ReviewDocument) -> list[tuple[int, str, str]]:
    items: list[tuple[int, str, str]] = []
    for paragraph_index, raw_text in document.toc_entries:
        normalized = normalize_toc_entry_text(raw_text)
        if not normalized:
            continue
        kind = "section" if normalized in SECTION_TITLES else "title"
        items.append((paragraph_index, kind, normalized))
    return items


def check_toc_refresh_mismatch(
    paragraphs: list[str],
    document: ReviewDocument,
) -> list[Finding]:
    """检查目录是否和正文内容对应，重点兜住“目录没刷新”的情况。"""
    actual_items = _actual_toc_items(document)
    if not actual_items or not document.entries:
        return []

    actual_kinds = {kind for _, kind, _ in actual_items}
    if actual_kinds == {"section"}:
        mode = "section-only"
    elif actual_kinds == {"title"}:
        mode = "title-only"
    else:
        mode = "full"

    expected_items = _expected_toc_items(document, mode)
    if not expected_items:
        return []

    findings: list[Finding] = []
    compare_len = min(len(actual_items), len(expected_items))

    for idx in range(compare_len):
        paragraph_index, _, actual_text = actual_items[idx]
        expected_text = expected_items[idx]
        if actual_text == expected_text:
            continue
        findings.append(
            Finding(
                rule_id="toc-mismatch",
                paragraph_index=paragraph_index,
                line_number=paragraph_index + 1,
                original_text=paragraphs[paragraph_index],
                description=f"目录写“{actual_text}”，正文对应为“{expected_text}”，疑似目录未刷新。",
                target_text=actual_text,
            )
        )

    if len(actual_items) > len(expected_items):
        for paragraph_index, _, actual_text in actual_items[len(expected_items):]:
            findings.append(
                Finding(
                    rule_id="toc-mismatch",
                    paragraph_index=paragraph_index,
                    line_number=paragraph_index + 1,
                    original_text=paragraphs[paragraph_index],
                    description=f"目录中的“{actual_text}”在正文中没有对应内容。",
                    target_text=actual_text,
                )
            )
    elif len(expected_items) > len(actual_items):
        paragraph_index = actual_items[-1][0]
        missing = expected_items[len(actual_items)]
        findings.append(
            Finding(
                rule_id="toc-mismatch",
                paragraph_index=paragraph_index,
                line_number=paragraph_index + 1,
                original_text=paragraphs[paragraph_index],
                description=f"目录缺少“{missing}”等正文内容，疑似目录未刷新。",
                target_text=actual_items[-1][2],
            )
        )

    return findings


def _build_prompt(rules_text: str, paragraphs: list[str], filename: str) -> str:
    """构造发给 LLM 的 prompt(仅语义类规则)。"""
    paras_text = "\n\n".join(
        f"[段 {i+1}]\n{p}" for i, p in enumerate(paragraphs)
    )

    prompt = f"""你是一位严谨的中文文档审核员。

# 审核规则清单(仅语义类规则,格式类已由代码检测)

{rules_text}

# 待审文档

文件名:{filename}

{paras_text}

# 你的任务

按以下步骤思考并输出:

## 步骤 1:识别文档结构

用几行文字描述:
- 文档头:哪几段
- 目录区:从哪段到哪段
- 页脚:哪段
- 正文区:其余部分

## 步骤 2:逐段落分析

对正文区的每个段落,判断其类型(章节分类/新闻标题/正文/原文引用),然后执行语义类检查:

**仅标题段需要检查:**
- title-truncated: 标题说的 ≠ 正文说的同一件事 → 标题截断
- content-mismatch: 标题和正文完全不同 → 错配

**仅正文段需要检查:**
- content-incomplete: 段末语义截断,缺宾语或结束语

**目录区域检查:**
- toc-mismatch: 目录与正文在章节名/标题/顺序上对不上

**内容质量检查:**
- content-out-of-scope: 跟银行经营/宏观经济完全无关
- content-wrong-section: 内容明显属于某板块但放到了别处
- content-duplicate: 同一件事出现两次以上
- content-outdated: 信息明显早于周报时间范围

## 步骤 3:输出 JSON

**严格按以下格式输出,只输出 JSON,不要任何其他文字:**

```json
{{
  "reasoning": "简要分析思路(段落分类、每条规则的检查结论,100字以内)",
  "issues": [
    {{"paragraph_index": 0, "rule_id": "xxx", "target_text": "错误片段", "original_text": "该段完整原文", "description": "问题描述"}}
  ]
}}
```

**关键规则:**
- paragraph_index 从 0 开始
- rule_id 必须是以下之一:{", ".join(SEMANTIC_RULE_IDS)}
- target_text 必须是原文里真实出现的短片段,用于精确定位标红位置
- original_text 必须是该段的**完整原文**,不要截断
- **不确定的问题不要写,宁可漏报不要误报**
- 文档完全没问题 → `{{"issues": []}}`
- 每条 issue 的 description 要简洁,不超过50字
"""
    return prompt


def _build_phase_prompt(
    rules_text: str,
    paragraphs: list[str],
    filename: str,
    phase: int,
    file_path: Path | None = None,
) -> str:
    """构造发给 LLM 的分阶段 prompt。

    phase=1: 基础语义检查（title-truncated/content-mismatch/content-incomplete）
    phase=2: 深度内容检查（toc-mismatch/content-out-of-scope/...）

    仅将对应阶段的规则子集传给 LLM，减少单次调用的 token 消耗。
    """
    document = _build_review_document(paragraphs, file_path)

    if phase == 1:
        target_rules = PHASE1_RULES
        phase_desc = "基础语义检查（标题完整性、标题-正文匹配性、正文完整性）"
        rule_reference = _build_phase_rule_reference(rules_text, target_rules)
        review_context = _render_phase1_context(document)
        task_details = "\n".join(
            [
                "只检查以下 3 类问题：",
                "- title-truncated：标题本身是否被截断。",
                "- content-mismatch：标题和紧跟正文是否是同一件事。",
                "- content-incomplete：正文或原文引用是否在句中突然结束。",
            ]
        )
    else:
        target_rules = PHASE2_RULES
        phase_desc = "深度内容检查（目录匹配、内容相关性、内容归位、重复检测、信息时效性）"
        rule_reference = _build_phase_rule_reference(rules_text, target_rules)
        review_context = _render_phase2_context(document)
        task_details = "\n".join(
            [
                "只检查以下 5 类问题：",
                "- toc-mismatch：目录和正文的章节名、标题、顺序是否对应；目录没刷新也算。",
                "- content-out-of-scope：内容是否完全偏离周报收录范围。",
                "- content-wrong-section：内容是否明显放错板块。",
                "- content-duplicate：同一件事是否重复出现。",
                "- content-outdated：是否整篇内容已经明显过时。",
            ]
        )

    prompt = f"""你是一位严谨的中文文档审核员。

# 当前审核阶段

{phase_desc}

# 本阶段规则说明

{rule_reference}

# 待审文档

文件名:{filename}

# 本阶段文档上下文

{review_context}

# 你的任务

按以下步骤思考并输出:

## 步骤 1：先理解这份紧凑上下文

不要脑补全文，只基于上面的目录和正文条目判断。

## 步骤 2：按本阶段范围检查

{task_details}

## 步骤 3：输出 JSON

**严格按以下格式输出,只输出 JSON,不要任何其他文字:**

```json
{{
  "reasoning": "简要分析思路(段落分类、每条规则的检查结论,100字以内)",
  "issues": [
    {{"paragraph_index": 0, "rule_id": "xxx", "target_text": "错误片段", "original_text": "该段完整原文", "description": "问题描述"}}
  ]
}}
```

**关键规则:**
- paragraph_index 从 0 开始
- rule_id 必须是以下之一:{", ".join(target_rules)}
- target_text 必须是原文里真实出现的短片段,用于精确定位标红位置
- original_text 必须是该段的**完整原文**,不要截断
- **不确定的问题不要写,宁可漏报不要误报**
- 文档完全没问题 → `{{"issues": []}}`
- 每条 issue 的 description 要简洁,不超过50字
"""
    return prompt


def _parse_llm_output(
    output: str,
    paragraphs: list[str],
    allowed_rules: tuple[str, ...] = SEMANTIC_RULE_IDS,
) -> tuple[list[Finding], str]:
    """兼容旧调用方，实际解析由共享审核核心完成。"""
    return parse_paragraph_findings(output, paragraphs, allowed_rules)


def _normalize_neican_target_text(finding: Finding, paragraph: str) -> str:
    target = (finding.target_text or "").strip()
    if target and target in paragraph:
        return target

    description = finding.description or ""

    for quoted in _NEICAN_QUOTED_TEXT_RE.findall(description):
        quoted = quoted.strip()
        if quoted and quoted in paragraph:
            return quoted

    word_candidates = sorted(
        {candidate.strip() for candidate in _NEICAN_WORD_RE.findall(description) if candidate.strip()},
        key=len,
        reverse=True,
    )
    for candidate in word_candidates:
        if candidate in paragraph:
            return candidate

    if finding.rule_id == "content-incomplete":
        return paragraph[-min(len(paragraph), 12):].strip()

    if finding.rule_id in {
        "title-truncated",
        "content-mismatch",
        "content-wrong-section",
        "toc-mismatch",
        "content-out-of-scope",
        "content-duplicate",
        "content-outdated",
    }:
        if len(paragraph) <= 30:
            return paragraph.strip()
        return paragraph[:20].strip()

    return ""


def _clean_description_for_entry_match(description: str) -> str:
    cleaned = description
    for noise in _NEICAN_MATCH_NOISE:
        cleaned = cleaned.replace(noise, " ")
    cleaned = re.sub(r"[，。；：、（）《》“”‘’\"'【】\(\)\[\]]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _build_match_ngrams(text: str) -> set[str]:
    normalized = re.sub(r"[^一-龥A-Za-z0-9]", "", text)
    grams: set[str] = set()
    for n in (2, 3, 4):
        if len(normalized) < n:
            continue
        for idx in range(len(normalized) - n + 1):
            grams.add(normalized[idx: idx + n])
    return grams


def _score_review_entry_against_description(
    entry: ReviewEntry,
    description: str,
) -> int:
    cleaned = _clean_description_for_entry_match(description)
    if not cleaned:
        return 0

    grams = _build_match_ngrams(cleaned)
    if not grams:
        return 0

    title_text = re.sub(r"\s+", "", entry.title_text)
    body_text = re.sub(r"\s+", "", "".join(entry.body_paragraphs))
    score = 0
    for gram in grams:
        if gram in title_text:
            score += len(gram) * 2
        if gram in body_text:
            score += len(gram)
    return score


def _find_entry_for_paragraph(
    document: ReviewDocument,
    paragraph_index: int,
) -> ReviewEntry | None:
    for entry in document.entries:
        if paragraph_index == entry.title_index:
            return entry
        if paragraph_index in entry.body_indexes:
            return entry
    return None


def _is_entry_body_paragraph(
    document: ReviewDocument,
    paragraph_index: int,
) -> bool:
    if not document.entries:
        return True
    entry = _find_entry_for_paragraph(document, paragraph_index)
    if entry is None:
        return False
    return paragraph_index in entry.body_indexes


def _relocate_content_mismatch_finding(
    finding: Finding,
    paragraphs: list[str],
    document: ReviewDocument,
) -> Finding:
    if not document.entries:
        return finding

    current_entry = _find_entry_for_paragraph(document, finding.paragraph_index)
    current_score = (
        _score_review_entry_against_description(current_entry, finding.description)
        if current_entry is not None
        else 0
    )

    best_entry = max(
        document.entries,
        key=lambda entry: _score_review_entry_against_description(entry, finding.description),
        default=None,
    )
    if best_entry is None:
        return finding

    best_score = _score_review_entry_against_description(best_entry, finding.description)
    if best_score < 6 or best_score <= current_score + 3:
        return finding

    return Finding(
        rule_id=finding.rule_id,
        paragraph_index=best_entry.title_index,
        line_number=best_entry.title_index + 1,
        original_text=paragraphs[best_entry.title_index],
        description=finding.description,
        target_text=best_entry.title_text,
    )


def _is_reference_intro_only(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return any(stripped.endswith(suffix) for suffix in _NEICAN_INCOMPLETE_INTRO_SUFFIXES)


def _last_clause(text: str) -> str:
    stripped = text.strip()
    stripped = stripped.rstrip("。！？?!；;”’》】）)]」』")
    parts = re.split(r"[，、：:；;]", stripped)
    return parts[-1].strip() if parts else ""


def _classify_neican_content_incomplete(paragraph: str) -> str:
    stripped = paragraph.strip()
    if not stripped:
        return "uncertain"

    if _is_reference_intro_only(stripped):
        return "incomplete"

    last_clause = _last_clause(stripped)
    if not last_clause:
        return "uncertain"

    if last_clause in _NEICAN_DANGLING_CLAUSES:
        return "incomplete"

    if last_clause.endswith(("将", "拟", "并", "且")) and len(last_clause) <= 4:
        return "incomplete"

    if not re.search(r"[。！？?!]$", stripped):
        return "incomplete"

    if len(last_clause) >= 4:
        return "complete"

    return "uncertain"


def _normalize_content_incomplete_description(paragraph: str) -> str:
    if _is_reference_intro_only(paragraph):
        return "正文仅写引导语，缺少后续原文内容。"
    return "正文末尾句子突然中断，语义不完整。"


def _content_incomplete_target_text(paragraph: str) -> str:
    clause = _last_clause(paragraph)
    if clause and clause in paragraph:
        return clause
    stripped = paragraph.strip().rstrip("。！？?!；;")
    return stripped[-min(len(stripped), 12):].strip()


def _should_autofill_content_incomplete(paragraph: str) -> bool:
    stripped = paragraph.strip()
    if not stripped:
        return False
    if "原文：" in stripped:
        return False
    if re.match(r"^[一二三四五六七八九十]+、", stripped):
        return False

    last_clause = _last_clause(stripped)
    if last_clause in _NEICAN_DANGLING_CLAUSES:
        return True
    return last_clause.endswith(("将", "拟", "并", "且")) and len(last_clause) <= 4


def _normalize_neican_findings(
    semantic_findings: list[Finding],
    paragraphs: list[str],
    document: ReviewDocument | None = None,
    active_rules: tuple[str, ...] = SEMANTIC_RULE_IDS,
) -> list[Finding]:
    review_document = document or _build_review_document(paragraphs)
    processed: list[Finding] = []

    for finding in semantic_findings:
        if finding.rule_id not in active_rules:
            continue

        if finding.rule_id == "toc-mismatch" and not review_document.toc_entries:
            continue

        current = finding
        paragraph = paragraphs[current.paragraph_index]

        if current.rule_id == "content-mismatch":
            current = _relocate_content_mismatch_finding(
                current,
                paragraphs,
                review_document,
            )
            paragraph = paragraphs[current.paragraph_index]

        if current.rule_id == "content-incomplete":
            if not _is_entry_body_paragraph(review_document, current.paragraph_index):
                continue
            completeness = _classify_neican_content_incomplete(paragraph)
            if completeness == "complete":
                continue
            current = Finding(
                rule_id=current.rule_id,
                paragraph_index=current.paragraph_index,
                line_number=current.paragraph_index + 1,
                original_text=paragraph,
                description=_normalize_content_incomplete_description(paragraph),
                target_text=_content_incomplete_target_text(paragraph),
            )

        processed.append(current)

    if "content-incomplete" in active_rules:
        existing_indexes = {
            finding.paragraph_index
            for finding in processed
            if finding.rule_id == "content-incomplete"
        }
        for entry in review_document.entries:
            for body_index, body_text in zip(entry.body_indexes, entry.body_paragraphs):
                if body_index in existing_indexes:
                    continue
                if not _should_autofill_content_incomplete(body_text):
                    continue
                processed.append(
                    Finding(
                        rule_id="content-incomplete",
                        paragraph_index=body_index,
                        line_number=body_index + 1,
                        original_text=paragraphs[body_index],
                        description=_normalize_content_incomplete_description(body_text),
                        target_text=_content_incomplete_target_text(body_text),
                    )
                )

    normalized: list[Finding] = []

    for finding in dedupe_prefer_longer_description(
        processed,
        key=lambda item: (item.rule_id, item.paragraph_index),
    ):
        paragraph = paragraphs[finding.paragraph_index]
        canonical = canonicalize_paragraph_finding(
            finding,
            paragraphs,
            source_kind="docx",
            target_resolver=lambda item, context: _normalize_neican_target_text(
                item,
                context,
            ),
        )
        if canonical is not None:
            normalized.append(canonical)

    return normalized


def _collect_message_text(message: object) -> str:
    return collect_message_text(message)


def _looks_like_valid_issue_json(output: str) -> bool:
    return looks_like_valid_issue_json(output)


async def _call_phase_llm_once(
    *,
    prompt: str,
    paragraphs: list[str],
    allowed_rules: tuple[str, ...],
    label: str,
    attempt: int,
    metrics: ReviewRunMetrics | None = None,
) -> tuple[list[Finding], str, str | None]:
    """执行一次阶段化 LLM 调用。"""
    try:
        client, model_name = _get_anthropic_client()
        message = await asyncio.to_thread(
            create_model_message,
            client,
            metrics=metrics,
            stage=label,
            model=model_name,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
            timeout=180.0,
        )
        output = _collect_message_text(message)
        findings_part, reasoning = _parse_llm_output(output, paragraphs, allowed_rules)
        if not _looks_like_valid_issue_json(output):
            if metrics is not None:
                metrics.record_model_failure(label)
            print(f"  {label} 第 {attempt + 1} 次失败: invalid JSON", flush=True)
            return [], "", "invalid JSON"
        reason_preview = reasoning[:40] if reasoning else "(无)"
        print(
            f"  {label} 第 {attempt + 1} 次: {len(findings_part)} 条, reasoning: {reason_preview}...",
            flush=True,
        )
        return findings_part, reasoning, None
    except Exception as exc:
        print(f"  {label} 第 {attempt + 1} 次失败: {exc}", flush=True)
        return [], "", str(exc)


async def review_phase1(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
    file_path: Path | None = None,
    *,
    metrics: ReviewRunMetrics | None = None,
    profile: ReviewProfile = NEICAN_PROFILE,
) -> ReviewResult:
    """第一阶段审核：格式正则 + 基础语义（title-truncated/content-mismatch/content-incomplete）。

    Args:
        file_path: 可选的 docx 文件路径，传入后额外执行正文格式检查。

    Returns:
        ReviewResult（含格式类 findings + phase1 语义 findings + 正文格式 findings）
    """
    active_rules = tuple(
        rule_id for rule_id in PHASE1_RULES if rule_id in profile.specialized_rule_ids
    )
    has_body_format_rule = "weekly-body-format" in profile.specialized_rule_ids
    total_phase1_rules = (
        len(active_rules)
        + len(profile.format_rule_ids)
        + int(has_body_format_rule)
    )
    if not paragraphs:
        return ReviewResult(findings=[], total_rules=total_phase1_rules, passed_rules=total_phase1_rules, filename=filename)

    # 格式类规则（正则，秒级）
    format_findings = check_format_rules(paragraphs, profile.format_rule_ids)

    # 内参周报正文格式检查（需要 docx 文件）
    weekly_format_findings: list[Finding] = []
    if has_body_format_rule and file_path is not None and file_path.exists():
        weekly_format_findings = _check_weekly_body_format(paragraphs, file_path)

    # 基础语义 LLM（默认 1 次，失败时重试 1 次）
    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []
    prompt = _build_phase_prompt(rules_text, paragraphs, filename, phase=1, file_path=file_path)
    print(f"  phase1 prompt_chars={len(prompt)}", flush=True)

    async def run_attempt(attempt: int) -> tuple[list[Finding], str | None]:
        findings_part, _, error = await _call_phase_llm_once(
            prompt=prompt,
            paragraphs=paragraphs,
            allowed_rules=active_rules,
            label="neican_phase1",
            attempt=attempt,
            metrics=metrics,
        )
        return findings_part, error

    outcome = await run_with_retries(run_attempt, max_attempts=2)
    if outcome.succeeded:
        semantic_findings.extend(outcome.value or [])
    else:
        llm_errors.extend(outcome.errors)
        if metrics is not None:
            metrics.record_degraded_stage("neican_phase1")

    # 全部失败
    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"LLM phase1 调用失败:{'; '.join(llm_errors)}",
            )],
            total_rules=total_phase1_rules,
            passed_rules=0,
            filename=filename,
        )

    # 语义 findings 去重
    review_document = _build_review_document(paragraphs, file_path)
    semantic_findings = _normalize_neican_findings(
        semantic_findings,
        paragraphs,
        document=review_document,
        active_rules=active_rules,
    )

    # 格式类 + 语义类 + 正文格式
    all_findings = list(semantic_findings)
    all_findings.extend(format_findings)
    all_findings.extend(weekly_format_findings)
    all_findings.sort(key=lambda f: f.paragraph_index)

    # 计算通过规则数
    hit_rule_ids = {f.rule_id for f in all_findings if not f.rule_id.startswith("__")}
    passed_rules = total_phase1_rules - len(hit_rule_ids)

    return ReviewResult(
        findings=all_findings,
        total_rules=total_phase1_rules,
        passed_rules=max(0, passed_rules),
        filename=filename,
    )


async def review_phase2(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
    file_path: Path | None = None,
    *,
    metrics: ReviewRunMetrics | None = None,
    profile: ReviewProfile = NEICAN_PROFILE,
) -> ReviewResult:
    """第二阶段审核：深度内容（toc-mismatch/content-out-of-scope/content-duplicate/content-outdated）。

    Returns:
        ReviewResult（含 phase2 语义 findings）
    """
    active_rules = tuple(
        rule_id for rule_id in PHASE2_RULES if rule_id in profile.specialized_rule_ids
    )
    if not paragraphs:
        return ReviewResult(findings=[], total_rules=len(active_rules), passed_rules=len(active_rules), filename=filename)

    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []
    prompt = _build_phase_prompt(rules_text, paragraphs, filename, phase=2, file_path=file_path)
    print(f"  phase2 prompt_chars={len(prompt)}", flush=True)

    async def run_attempt(attempt: int) -> tuple[list[Finding], str | None]:
        findings_part, _, error = await _call_phase_llm_once(
            prompt=prompt,
            paragraphs=paragraphs,
            allowed_rules=active_rules,
            label="neican_phase2",
            attempt=attempt,
            metrics=metrics,
        )
        return findings_part, error

    outcome = await run_with_retries(run_attempt, max_attempts=2)
    if outcome.succeeded:
        semantic_findings.extend(outcome.value or [])
    else:
        llm_errors.extend(outcome.errors)
        if metrics is not None:
            metrics.record_degraded_stage("neican_phase2")

    # ===== 代码化预检测（确定性高）=====
    from .section_entities import (
        REGULATORY_ENTITIES, PARTY_GOV_ENTITIES, BANKING_ENTITIES,
    )
    review_document = _build_review_document(paragraphs, file_path)
    code_findings = check_section_mismatch(paragraphs)
    code_findings.extend(check_toc_refresh_mismatch(paragraphs, review_document))
    semantic_findings.extend(code_findings)
    # =====================================

    # 全部失败
    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"LLM phase2 调用失败:{'; '.join(llm_errors)}",
            )],
            total_rules=len(active_rules),
            passed_rules=0,
            filename=filename,
        )

    # 去重
    semantic_findings = _normalize_neican_findings(
        semantic_findings,
        paragraphs,
        document=review_document,
        active_rules=active_rules,
    )
    semantic_findings.sort(key=lambda f: f.paragraph_index)

    # 计算通过规则数
    hit_rule_ids = {f.rule_id for f in semantic_findings if not f.rule_id.startswith("__")}
    passed_rules = len(active_rules) - len(hit_rule_ids)

    return ReviewResult(
        findings=semantic_findings,
        total_rules=len(active_rules),
        passed_rules=max(0, passed_rules),
        filename=filename,
    )


def review_text(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
    total_rules: int = 13,
    passed_rules_hint: int | None = None,
) -> ReviewResult:
    """对段落列表做审核.

    两层架构:
    1. 格式类规则 → format_checker.py 正则检测(稳定)
    2. 语义类规则 → LLM CoT 3次调用取并集

    Args:
        paragraphs: 文档段落列表
        rules_text: 规则库文本(给 LLM 看)
        filename: 文件名(用于显示)
        total_rules: 规则总数(显示用),默认13
        passed_rules_hint: 强制指定的"通过规则数"(测试用)
    """
    if not paragraphs:
        return ReviewResult(
            findings=[],
            total_rules=total_rules,
            passed_rules=total_rules,
            filename=filename,
        )

    # ========== 1. 格式类规则:代码检测 ==========
    format_findings = check_all_format_rules(paragraphs)
    print(f"  格式类检测: {len(format_findings)} 条", flush=True)

    # ========== 2. 语义类规则:LLM 3次调用取并集 ==========
    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []

    for attempt in range(3):
        try:
            client, model_name = _get_anthropic_client()
            prompt = _build_prompt(rules_text, paragraphs, filename)
            message = client.messages.create(
                model=model_name,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
                timeout=180.0,
            )
            text_parts = []
            for block in message.content:
                if hasattr(block, "text") and block.text:
                    text_parts.append(block.text)
            output = "\n".join(text_parts)

            findings, reasoning = _parse_llm_output(output, paragraphs)
            semantic_findings.extend(findings)
            reason_preview = reasoning[:40] if reasoning else "(无)"
            print(f"  第 {attempt+1} 次调用: {len(findings)} 条, reasoning: {reason_preview}...", flush=True)
        except Exception as exc:
            llm_errors.append(str(exc))
            print(f"  第 {attempt+1} 次调用失败: {exc}", flush=True)

    # 如果 3 次全部失败
    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"LLM 调用失败:{'; '.join(llm_errors)}",
            )],
            total_rules=total_rules,
            passed_rules=0,
            filename=filename,
        )

    # ========== 3. 合并:去重
    # 同 (rule_id, paragraph_index) 只保留一条,优先保留 description 最长的(信息最丰富)
    merged: dict[tuple[str, int], Finding] = {}
    for f in semantic_findings:
        key = (f.rule_id, f.paragraph_index)
        if key not in merged or len(f.description) > len(merged[key].description):
            merged[key] = f

    semantic_findings = list(merged.values())

    # ========== 4. 合并格式类 + 语义类 ==========
    all_findings = list(semantic_findings)
    all_findings.extend(format_findings)
    all_findings.sort(key=lambda f: f.paragraph_index)

    # ========== 5. 计算通过规则数 ==========
    hit_rule_ids = {f.rule_id for f in all_findings if not f.rule_id.startswith("__")}
    passed_rules = total_rules - len(hit_rule_ids)
    if passed_rules_hint is not None:
        passed_rules = passed_rules_hint

    return ReviewResult(
        findings=all_findings,
        total_rules=total_rules,
        passed_rules=max(0, passed_rules),
        filename=filename,
    )


def _compute_line_number(paragraphs: list[str], paragraph_index: int) -> int:
    """估算段落对应的行号(从1开始).保留以兼容旧调用."""
    return paragraph_index + 1


# ============================================================
# 字体/字号解析辅助（XML 层级）
# ============================================================

def _resolve_font_at_run(run) -> tuple[str | None, int | None]:
    """从 run 层解析中文字体名和字号(EMU)，没有则返回 None. 字号来自 w:sz (half-pt)."""
    rpr = run._r.find(qn('w:rPr'))
    if rpr is None:
        return None, None
    ea = None
    sz_val = None
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is not None:
        ea = rfonts.get(qn('w:eastAsia'))
    sz_el = rpr.find(qn('w:sz'))
    if sz_el is not None:
        sz_val = int(sz_el.get(qn('w:val'))) * 6350  # half-pt → EMU
    return ea, sz_val


def _resolve_font_at_style(style) -> tuple[str | None, int | None]:
    """从样式层解析中文字体名和字号(EMU)."""
    if style is None:
        return None, None
    srpr = style.element.find(qn('w:rPr'))
    if srpr is None:
        return None, None
    ea = None
    sz_val = None
    srfonts = srpr.find(qn('w:rFonts'))
    if srfonts is not None:
        ea = srfonts.get(qn('w:eastAsia'))
    ssz = srpr.find(qn('w:sz'))
    if ssz is not None:
        sz_val = int(ssz.get(qn('w:val'))) * 6350
    return ea, sz_val


def _resolve_font_at_defaults(doc) -> tuple[str | None, int | None]:
    """从文档默认样式解析中文字体名和字号(EMU)."""
    defaults = doc.element.find(qn('w:docDefaults'))
    if defaults is None:
        return None, None
    rprdefault = defaults.find(qn('w:rPrDefault'))
    if rprdefault is None:
        return None, None
    drpr = rprdefault.find(qn('w:rPr'))
    if drpr is None:
        return None, None
    ea = None
    sz_val = None
    drfonts = drpr.find(qn('w:rFonts'))
    if drfonts is not None:
        ea = drfonts.get(qn('w:eastAsia'))
    dsz = drpr.find(qn('w:sz'))
    if dsz is not None:
        sz_val = int(dsz.get(qn('w:val'))) * 6350
    return ea, sz_val


def _iter_visible_runs(paragraph):
    """忽略 Word 自动插入的空 run，避免被虚假字体覆盖。"""
    for run in paragraph.runs:
        if run.text and run.text.strip():
            yield run


def _resolve_paragraph_metric(paragraph, attr: str):
    """从段落本身向样式链回退读取段落格式。"""
    paragraph_format = paragraph.paragraph_format
    value = getattr(paragraph_format, attr)
    if value is not None:
        return value

    style = paragraph.style
    while style is not None:
        style_format = getattr(style, "paragraph_format", None)
        if style_format is not None:
            value = getattr(style_format, attr)
            if value is not None:
                return value
        style = style.base_style

    return None


def _paragraph_has_numbering(dp) -> bool:
    """判断段落是否使用了 Word 自动编号."""
    ppr = dp._p.find(qn('w:pPr'))
    if ppr is None:
        return False
    return ppr.find(qn('w:numPr')) is not None


def _find_weekly_body_start(paragraphs: list[str]) -> int:
    """内参格式审核起点：优先目录后；无目录时退回到首个正式板块。"""
    toc_start, toc_end = _find_toc_range(paragraphs)
    if toc_end > toc_start:
        return min(toc_end, len(paragraphs))

    for idx, paragraph in enumerate(paragraphs):
        if _is_section_title(paragraph):
            return idx
    return 0


def _check_weekly_body_format(
    paragraphs: list[str],
    docx_path: Path,
) -> list[Finding]:
    """检查内参周报正文格式：章节标题/正文字体、字号、行距、首行缩进."""
    body_start = _find_weekly_body_start(paragraphs)

    doc = open_docx_sanitized(docx_path)
    doc_paragraphs = list(iter_reviewable_paragraphs(doc))
    if not doc_paragraphs:
        return []

    findings: list[Finding] = []

    for para_idx, dp in enumerate(doc_paragraphs):
        if para_idx >= len(paragraphs):
            break
        if para_idx < body_start:
            continue

        text = paragraphs[para_idx].strip()
        if not text:
            continue

        style_name = _style_name(dp.style)
        has_style_alignment = para_idx < len(doc_paragraphs)
        is_title = _is_title_style(style_name) if has_style_alignment else _is_news_title(text)

        if _is_section_title(text):
            issues: list[str] = []

            # --- 字体字号（三级降级：run → style → docDefaults） ---
            run_fonts: set[str] = set()
            run_sizes: set[int] = set()
            for run in _iter_visible_runs(dp):
                ea, sz = _resolve_font_at_run(run)
                if ea:
                    run_fonts.add(ea)
                if sz:
                    run_sizes.add(sz)
            if not run_fonts:
                ea, _ = _resolve_font_at_style(dp.style)
                if ea:
                    run_fonts.add(ea)
            if not run_fonts:
                ea, _ = _resolve_font_at_defaults(doc)
                if ea:
                    run_fonts.add(ea)
            if not run_sizes:
                _, sz = _resolve_font_at_style(dp.style)
                if sz:
                    run_sizes.add(sz)
            if not run_sizes:
                _, sz = _resolve_font_at_defaults(doc)
                if sz:
                    run_sizes.add(sz)

            # 章节标题：黑体 18pt
            if run_fonts and "黑体" not in run_fonts:
                issues.append(f"章节标题中文字体应为黑体，当前为{'/'.join(sorted(run_fonts))}")
            expected_size = 18 * 12700  # 18pt in EMU
            if run_sizes and any(s != expected_size for s in run_sizes):
                sizes_detail = "/".join(f"{s/12700:.0f}pt" for s in sorted(run_sizes))
                issues.append(f"章节标题字号应为18pt，当前为{sizes_detail}")

            # 行距
            ls = _resolve_paragraph_metric(dp, "line_spacing")
            ls_rule = _resolve_paragraph_metric(dp, "line_spacing_rule")
            if ls is not None and ls_rule is not None and ls_rule.name == "MULTIPLE":
                if abs(ls - 1.15) > 0.05:
                    issues.append(f"章节标题行距应为1.15倍，当前为{ls:.2f}倍")

            if issues:
                findings.append(Finding(
                    rule_id="weekly-body-format",
                    paragraph_index=para_idx,
                    line_number=para_idx + 1,
                    original_text=text,
                    description="；".join(issues),
                    target_text=issues[0][:30],
                ))

        elif is_title:
            # 新闻标题：黑体（大小从 Normal）
            run_fonts = set()
            for run in _iter_visible_runs(dp):
                ea, _ = _resolve_font_at_run(run)
                if ea:
                    run_fonts.add(ea)
            if not run_fonts:
                ea, _ = _resolve_font_at_style(dp.style)
                if ea:
                    run_fonts.add(ea)
            if not run_fonts:
                ea, _ = _resolve_font_at_defaults(doc)
                if ea:
                    run_fonts.add(ea)

            if run_fonts and "黑体" not in run_fonts:
                findings.append(Finding(
                    rule_id="weekly-body-format",
                    paragraph_index=para_idx,
                    line_number=para_idx + 1,
                    original_text=text,
                    description=f"新闻标题中文字体应为黑体，当前为{'/'.join(sorted(run_fonts))}",
                    target_text=text[:20],
                ))

        else:
            issues = []

            # --- 字体字号 ---
            run_fonts = set()
            run_sizes = set()
            for run in _iter_visible_runs(dp):
                ea, sz = _resolve_font_at_run(run)
                if ea:
                    run_fonts.add(ea)
                if sz:
                    run_sizes.add(sz)
            if not run_fonts:
                ea, _ = _resolve_font_at_style(dp.style)
                if ea:
                    run_fonts.add(ea)
            if not run_fonts:
                ea, _ = _resolve_font_at_defaults(doc)
                if ea:
                    run_fonts.add(ea)
            if not run_sizes:
                _, sz = _resolve_font_at_style(dp.style)
                if sz:
                    run_sizes.add(sz)
            if not run_sizes:
                _, sz = _resolve_font_at_defaults(doc)
                if sz:
                    run_sizes.add(sz)

            # 正文：宋体
            if run_fonts and "宋体" not in run_fonts:
                issues.append(f"正文字体应为宋体，当前为{'/'.join(sorted(run_fonts))}")
            elif not run_fonts:
                issues.append("正文字体未设置，应为宋体")

            # 正文：12pt
            expected_size = 12 * 12700
            if run_sizes and any(s != expected_size for s in run_sizes):
                sizes_detail = "/".join(f"{s/12700:.0f}pt" for s in sorted(run_sizes))
                issues.append(f"正文字号应为12pt，当前为{sizes_detail}")
            elif not run_sizes:
                issues.append("正文字号未设置，应为12pt")

            # --- 行距：1.15倍 ---
            ls = _resolve_paragraph_metric(dp, "line_spacing")
            ls_rule = _resolve_paragraph_metric(dp, "line_spacing_rule")
            if ls is not None and ls_rule is not None and ls_rule.name == "MULTIPLE":
                if abs(ls - 1.15) > 0.05:
                    issues.append(f"正文行距应为1.15倍，当前为{ls:.2f}倍")

            # --- 首行缩进（编号段落跳过） ---
            if not _paragraph_has_numbering(dp):
                fi = _resolve_paragraph_metric(dp, "first_line_indent")
                if fi is not None:
                    fi_cm = fi / 360000  # EMU → cm
                    if fi_cm < 0.7 or fi_cm > 1.0:
                        issues.append(f"正文首行缩进应在0.7-1.0cm之间，当前为{fi_cm:.2f}cm")
                else:
                    issues.append("正文首行缩进缺失，应有约2字符（模板实测约0.85cm）首行缩进")

            if issues:
                findings.append(Finding(
                    rule_id="weekly-body-format",
                    paragraph_index=para_idx,
                    line_number=para_idx + 1,
                    original_text=text,
                    description="；".join(issues),
                    target_text=issues[0][:30],
                ))
    return findings


def check_section_mismatch(paragraphs: list[str]) -> list["Finding"]:
    """检测内容放错板块（content-wrong-section）。

    识别流程：
    1. 遍历段落，识别当前板块标题
    2. 只检测新闻标题段落（5-60字符，无句末标点），正文跟随标题
    3. 标题含实体关键词 → 匹配期望板块
    4. 实体出现在引用语境（"据X数据显示"）→ 不作为主体判断
    5. 期望板块与实际板块不一致 → 报错
    """
    from .core.models import Finding
    from .section_entities import (
        REGULATORY_ENTITIES, PARTY_GOV_ENTITIES, BANKING_ENTITIES,
        MARKET_OBSERVATION_MARKERS,
    )

    findings = []
    current_section_key = None
    current_section_label = None

    for idx, para in enumerate(paragraphs):
        stripped = para.strip()

        # 识别并更新当前板块
        normalized_section = _normalize_section_title(stripped)
        if normalized_section is not None:
            current_section_key = normalized_section
            current_section_label = stripped
            continue

        # 跳过非正文区
        if current_section_key is None:
            continue
        if current_section_key == "前沿观点":
            continue

        # ===== 标题优先：只检测新闻标题 =====
        # 正文段落跟随所属标题，不再单独检测
        if not _is_news_title(para):
            continue

        # 检查文本中是否有实体关键词（排除引用语境）
        text_to_check = stripped[:180]

        matched_entity = None
        expected_section = None

        # 监管动态实体（一行一局一会）
        for kw in REGULATORY_ENTITIES:
            if kw in text_to_check:
                # 排除引用语境（如"据中国人民银行数据"）
                if _is_entity_in_citation_context(text_to_check, kw):
                    continue
                matched_entity = kw
                expected_section = "监管动态"
                break

        # 党政要闻实体（党和国家领导人、国务院各部委）
        if not matched_entity:
            for kw in PARTY_GOV_ENTITIES:
                if kw in text_to_check:
                    if _is_entity_in_citation_context(text_to_check, kw):
                        continue
                    matched_entity = kw
                    expected_section = "党政要闻"
                    break

        # 同业动向实体（民营/数字银行）
        if not matched_entity:
            for kw in BANKING_ENTITIES:
                if kw in text_to_check:
                    if _is_entity_in_citation_context(text_to_check, kw):
                        continue
                    matched_entity = kw
                    expected_section = "同业动向"
                    break

        # 市场观察专属内容类型（A股综述/港股/美股/债市/汇市/大宗商品）
        # 这些内容类型只能放在市场观察
        if not matched_entity:
            for kw in MARKET_OBSERVATION_MARKERS:
                if kw in text_to_check:
                    matched_entity = kw
                    expected_section = "市场观察"
                    break

        if not matched_entity:
            continue

        if current_section_key != expected_section:
            findings.append(Finding(
                rule_id="content-wrong-section",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"内容主体'{matched_entity}'应归入{expected_section}，却放在了{current_section_label}",
                target_text=matched_entity,
            ))

    return findings


def _is_news_title(para: str, *, style_name: str = "") -> bool:
    """判断段落是否为新闻标题。

    优先使用 Word 标题样式；无样式时再退回到文本特征。
    """
    stripped = para.strip()
    if not stripped:
        return False
    if _is_section_title(stripped) or _is_reference_line(stripped):
        return False
    if style_name and _is_title_style(style_name):
        return True
    if len(stripped) < 5 or len(stripped) > 60:
        return False
    return stripped[-1] not in "。；.;：:"


def _is_entity_in_citation_context(text: str, entity: str) -> bool:
    """判断实体是否在引用语境中出现（数据来源而非主体）。

    例如：'据中国人民银行数据显示' 中的'中国人民银行'是数据来源，不是主体。
    """
    # 找到实体在文本中的所有位置
    start = 0
    while True:
        pos = text.find(entity, start)
        if pos == -1:
            break

        # 检查实体前面30字符内是否有引用前缀
        prefix_start = max(0, pos - 30)
        prefix = text[prefix_start:pos]

        for pfx in ("据", "根据", "来自", "依据"):
            if prefix.endswith(pfx):
                return True

        # 检查是否是 "X数据显示" / "X公布的数据" 等模式
        # 必须是 "数据显示" / "公布的数据" / "统计" 等完整后缀
        entity_end = pos + len(entity)
        suffix = text[entity_end:entity_end + 10]

        for kw in ("数据显示", "公布的数据", "统计数据", "发布的数据"):
            if suffix.startswith(kw):
                return True

        start = pos + 1

    return False
