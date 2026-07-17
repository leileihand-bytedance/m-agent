"""通用审核术语检查器.

确定性规则:
- general-term-variant: 命中术语库 forbidden_variants 时,直接产出 Finding。

另外提供 prompt 用的受保护术语选择能力,帮助模型降低对专业术语的误判。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core.models import Finding


# 受保护术语规则 ID
GENERAL_TERM_VARIANT_RULE_ID = "general-term-variant"

_ASCII_TOKEN_CHARS = "A-Za-z0-9"
_CJK_ALLOWED_SUFFIXES = (
    "产品",
    "业务",
    "服务",
    "模式",
    "平台",
    "项目",
    "体系",
    "贷款",
    "客户",
    "余额",
    "规模",
    "特色",
    "方案",
    "品牌",
    "功能",
    "系统",
    "场景",
    "案例",
    "流程",
    "战略",
    "能力",
    "应用",
    "实践",
    "累计",
    "覆盖",
    "支持",
    "推动",
    "推进",
    "打造",
    "上线",
    "落地",
    "助力",
    "赋能",
    "的",
    "是",
    "在",
    "与",
    "及",
    "和",
    "等",
    "将",
    "已",
    "可",
    "会",
    "需",
    "应",
    "被",
    "把",
    "由",
    "为",
    "有",
    "无",
)


def _is_cjk_char(char: str) -> bool:
    """判断单个字符是否为常见中文汉字."""
    return "\u3400" <= char <= "\u4dbf" or "\u4e00" <= char <= "\u9fff"


def _is_ascii_variant(text: str) -> bool:
    """英文/数字术语按整词匹配,避免大小写漏检."""
    return any(char.isascii() and char.isalpha() for char in text)


def _get_allowed_cjk_suffixes(term: dict[str, Any]) -> tuple[str, ...]:
    custom_suffixes = tuple(
        suffix
        for suffix in term.get("allowed_suffixes", [])
        if isinstance(suffix, str) and suffix
    )
    return custom_suffixes + _CJK_ALLOWED_SUFFIXES


def _iter_ascii_variant_matches(paragraph: str, variant: str) -> list[str]:
    pattern = re.compile(
        rf"(?<![{_ASCII_TOKEN_CHARS}]){re.escape(variant)}(?![{_ASCII_TOKEN_CHARS}])",
        re.IGNORECASE,
    )
    return [match.group(0) for match in pattern.finditer(paragraph)]


def _iter_cjk_variant_matches(
    paragraph: str,
    variant: str,
    term: dict[str, Any],
) -> list[str]:
    allowed_suffixes = _get_allowed_cjk_suffixes(term)
    matches: list[str] = []
    search_start = 0

    while True:
        start = paragraph.find(variant, search_start)
        if start == -1:
            return matches

        end = start + len(variant)
        if end >= len(paragraph):
            matches.append(paragraph[start:end])
        else:
            trailing = paragraph[end:]
            next_char = trailing[0]
            if not _is_cjk_char(next_char) or any(
                trailing.startswith(suffix) for suffix in allowed_suffixes
            ):
                matches.append(paragraph[start:end])

        search_start = start + 1


def _iter_variant_matches(
    paragraph: str,
    variant: str,
    term: dict[str, Any],
) -> list[str]:
    if _is_ascii_variant(variant):
        return _iter_ascii_variant_matches(paragraph, variant)
    return _iter_cjk_variant_matches(paragraph, variant, term)


def _is_term_relevant(term: dict[str, Any], text: str) -> bool:
    """判断某术语是否与当前文本相关(用于 prompt 保护段)."""
    lowered = text.lower()

    standard = term.get("standard", "")
    if standard and standard.lower() in lowered:
        return True

    for alias in term.get("allowed_aliases", []):
        if isinstance(alias, str) and alias.lower() in lowered:
            return True

    # 如果文本里已经出现明确错写,也让模型知道这是受监控术语
    for variant in term.get("forbidden_variants", []):
        if isinstance(variant, str) and variant.lower() in lowered:
            return True

    return False


def select_relevant_terms(
    paragraphs: list[str],
    term_library: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """从术语库中选出与当前段落集合相关的术语.

    只返回 doc_types 包含 general 的术语,控制 prompt 体积。
    """
    from .term_loader import load_term_library

    if term_library is None:
        term_library = load_term_library()

    text = "\n".join(paragraphs)
    return [
        term
        for term in term_library
        if isinstance(term, dict)
        and "general" in term.get("doc_types", [])
        and _is_term_relevant(term, text)
    ]


def build_protected_terms_prompt_section(
    paragraphs: list[str],
    term_library: list[dict[str, Any]] | None = None,
) -> str:
    """构造 prompt 中的受保护术语段.

    如果当前段落没有命中任何术语,返回空字符串,避免无意义膨胀 prompt。
    """
    relevant = select_relevant_terms(paragraphs, term_library)
    if not relevant:
        return ""

    lines = [
        "",
        "# 受保护术语",
        "",
        "以下专业术语/专有名词在本 chunk 中出现。不要因为它们生僻、中英混排、缩写或包含特殊字符,就直接判为错别字或名称错误。",
        "只有当文本中确实命中明确错写(如术语库中列出的 forbidden variant)时,才报名称错误/错别字。",
        "",
    ]

    for term in relevant:
        standard = term.get("standard", "")
        aliases = term.get("allowed_aliases", [])
        notes = term.get("notes", "")

        alias_part = f"（允许别名: {', '.join(aliases)}）" if aliases else ""
        notes_part = f" — {notes}" if notes else ""
        lines.append(f"- {standard}{alias_part}{notes_part}")

    return "\n".join(lines)


def check_term_variants(
    paragraphs: list[str],
    term_library: list[dict[str, Any]] | None = None,
) -> list["Finding"]:
    """检测术语库中明确的错写变体.

    当段落中命中 forbidden_variants 时,产出 general-term-variant Finding。
    """
    from .core.models import Finding
    from .term_loader import load_term_library

    if term_library is None:
        term_library = load_term_library()

    findings: list[Finding] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        for term in term_library:
            if not isinstance(term, dict):
                continue
            if "general" not in term.get("doc_types", []):
                continue

            standard = term.get("standard", "")
            variants = term.get("forbidden_variants", [])
            if not standard or not variants:
                continue

            for variant in variants:
                if not isinstance(variant, str) or not variant:
                    continue
                for matched_text in _iter_variant_matches(paragraph, variant, term):
                    findings.append(
                        Finding(
                            rule_id=GENERAL_TERM_VARIANT_RULE_ID,
                            paragraph_index=paragraph_index,
                            line_number=paragraph_index + 1,
                            original_text=paragraph,
                            description=f"术语写法不规范：'{matched_text}'应为'{standard}'",
                            target_text=matched_text,
                        )
                    )

    return findings
