"""目录区识别与归一化工具."""

from __future__ import annotations

import re


SECTION_TITLES = {
    "党政要闻",
    "监管动态",
    "同业动向",
    "同业动态",
    "市场观察",
    "前沿观点",
}

_ORDINAL_PREFIX_RE = re.compile(r"^[一二三四五六七八九十]+[、.．]")
_TRAILING_PAGE_RE = re.compile(r"(?:[.．·•…\s]{0,12})(\d+)\s*$")


def strip_pageref(text: str) -> str:
    return re.sub(r"PAGEREF[^\s]+", "", text).strip()


def strip_trailing_page_number(text: str) -> str:
    stripped = strip_pageref(text)
    return _TRAILING_PAGE_RE.sub("", stripped).strip()


def normalize_toc_entry_text(text: str) -> str:
    stripped = strip_trailing_page_number(text)
    stripped = _ORDINAL_PREFIX_RE.sub("", stripped).strip()
    return re.sub(r"\s+", " ", stripped)


def _looks_like_toc_line(text: str) -> bool:
    stripped = strip_pageref(text)
    if not stripped:
        return False
    if _ORDINAL_PREFIX_RE.match(stripped):
        return True
    if _TRAILING_PAGE_RE.search(stripped):
        return True
    return False


def _next_non_empty(paragraphs: list[str], start: int) -> str | None:
    for idx in range(start, len(paragraphs)):
        stripped = paragraphs[idx].strip()
        if stripped:
            return stripped
    return None


def find_toc_range(paragraphs: list[str]) -> tuple[int, int]:
    """返回目录正文段落的起止位置，end 为排他边界。"""
    toc_start = -1

    for idx, paragraph in enumerate(paragraphs):
        if paragraph.strip() == "目录":
            toc_start = idx + 1
            break

    if toc_start < 0:
        return 0, 0

    seen_candidate = False
    toc_end = len(paragraphs)

    for idx in range(toc_start, len(paragraphs)):
        stripped = paragraphs[idx].strip()
        if not stripped:
            continue

        if "主编" in stripped or "责编" in stripped:
            toc_end = idx
            break

        next_non_empty = _next_non_empty(paragraphs, idx + 1)
        plain_text = strip_trailing_page_number(stripped)

        if (
            seen_candidate
            and plain_text in SECTION_TITLES
            and not _looks_like_toc_line(stripped)
            and next_non_empty is not None
            and not _looks_like_toc_line(next_non_empty)
        ):
            toc_end = idx
            break

        if _looks_like_toc_line(stripped):
            seen_candidate = True
            continue

        if seen_candidate:
            toc_end = idx
            break

    return toc_start, toc_end
