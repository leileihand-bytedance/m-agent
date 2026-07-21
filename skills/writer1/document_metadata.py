from __future__ import annotations

from datetime import date
import re
from typing import Mapping


SUPPORTED_METADATA_KEYS = frozenset(
    {"issue_date", "issue_number", "editor", "contact"}
)

_CONNECTOR = r"(?:写成?|改成?|改为|设置?为?|设为|定为|是|为|[:：])?"
_DATE_PATTERN = re.compile(
    rf"(?:报送日期|简报日期|落款日期|日期)\s*{_CONNECTOR}\s*"
    r"(?P<value>(?:20\d{2}\s*[年./-]\s*)?\d{1,2}\s*(?:月|[./-])\s*\d{1,2}\s*日?)"
)
_ISSUE_PATTERN = re.compile(
    rf"(?:简报)?期号\s*{_CONNECTOR}\s*第?\s*"
    r"(?P<value>\d{1,4}|[零〇一二两三四五六七八九十百]+)\s*期?"
)
_EDITOR_PATTERN = re.compile(
    rf"责任编辑\s*{_CONNECTOR}\s*"
    r"(?P<value>[\u3400-\u9fffA-Za-z·]{2,20}?)"
    r"(?=\s*(?:[,，;；。]|联系电话|联系号码|电话|联系方式|签发人|$))"
)
_CONTACT_PATTERN = re.compile(
    rf"(?:联系电话号码|联系电话|联系号码|联系方式|电话)\s*{_CONNECTOR}\s*"
    r"(?P<value>\+?\d[\d\s\-—－()（）]{5,30})"
)
_SIGNER_PATTERN = re.compile(
    rf"签发人\s*{_CONNECTOR}\s*"
    r"(?P<value>[\u3400-\u9fffA-Za-z·]{2,20}?)"
    r"(?=\s*(?:[,，;；。]|责任编辑|联系电话|联系号码|电话|联系方式|$))"
)
_FIELD_PATTERNS = (
    ("issue_date", _DATE_PATTERN),
    ("issue_number", _ISSUE_PATTERN),
    ("editor", _EDITOR_PATTERN),
    ("contact", _CONTACT_PATTERN),
)


def extract_brief_document_metadata(text: str) -> dict[str, str]:
    normalized = _normalize_fullwidth(str(text or ""))
    values: dict[str, str] = {}
    for key, pattern in _FIELD_PATTERNS:
        match = pattern.search(normalized)
        if match is None:
            continue
        value = match.group("value").strip()
        if key == "issue_date":
            value = _normalize_date(value)
        elif key == "issue_number":
            value = value.lstrip("0") or "0"
        elif key == "contact":
            value = re.sub(r"\s+", "", value).replace("—", "-").replace("－", "-")
        if value:
            values[key] = value
    return values


def merge_brief_document_metadata(
    previous: object,
    updates: Mapping[str, str] | None = None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    if isinstance(previous, Mapping):
        for key, value in previous.items():
            clean_key = str(key)
            clean_value = str(value or "").strip()
            if clean_key in SUPPORTED_METADATA_KEYS and clean_value:
                merged[clean_key] = clean_value
    for key, value in (updates or {}).items():
        clean_value = str(value or "").strip()
        if key in SUPPORTED_METADATA_KEYS and clean_value:
            merged[key] = clean_value
    return merged


def requests_brief_signer_change(text: str) -> bool:
    return _SIGNER_PATTERN.search(_normalize_fullwidth(str(text or ""))) is not None


def is_brief_document_metadata_only(text: str) -> bool:
    normalized = _normalize_fullwidth(str(text or "")).lower()
    has_field = False
    remainder = normalized
    for _, pattern in _FIELD_PATTERNS:
        if pattern.search(remainder):
            has_field = True
            remainder = pattern.sub(" ", remainder)
    if _SIGNER_PATTERN.search(remainder):
        has_field = True
        remainder = _SIGNER_PATTERN.sub(" ", remainder)
    if not has_field:
        return False

    for marker in (
        "输出为word",
        "导出为word",
        "输出word",
        "导出word",
        "生成word",
        "word文档",
        "word版",
        "正式版文档",
        "正式文档",
        "输出正式版",
        "导出正式版",
    ):
        remainder = remainder.replace(marker, " ")
    for filler in (
        "请帮我",
        "麻烦",
        "帮我",
        "请",
        "把",
        "将",
        "给我",
        "这篇",
        "这个",
        "当前",
        "上一稿",
        "上一版",
        "简报",
        "文档",
        "重新",
        "一下",
        "的",
    ):
        remainder = remainder.replace(filler, " ")
    remainder = re.sub(r"[\s，。！？、；：,.!?;:'\"（）()【】\[\]]", "", remainder)
    return not remainder


def strip_brief_document_metadata_instructions(text: str) -> str:
    remainder = _normalize_fullwidth(str(text or ""))
    for _, pattern in _FIELD_PATTERNS:
        remainder = pattern.sub(" ", remainder)
    remainder = _SIGNER_PATTERN.sub(" ", remainder)
    remainder = re.sub(r"\s*([，,；;])\s*(?=[，,；;：:。.!！?？]|$)", "", remainder)
    remainder = re.sub(r"([，,；;])(?:\s*[，,；;])+", r"\1", remainder)
    return remainder.strip(" \t\r\n，,；;。")


def _normalize_fullwidth(value: str) -> str:
    return value.translate(
        str.maketrans(
            "０１２３４５６７８９：－（）",
            "0123456789:-()",
        )
    )


def _normalize_date(value: str) -> str:
    compact = re.sub(r"\s+", "", value).replace(".", "-").replace("/", "-")
    match = re.fullmatch(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})日?", compact)
    if match is None:
        match = re.fullmatch(r"(?:(20\d{2})-)?(\d{1,2})-(\d{1,2})", compact)
    if match is None:
        return ""
    year_text, month_text, day_text = match.groups()
    year = int(year_text) if year_text else 2000
    month = int(month_text)
    day = int(day_text)
    try:
        date(year, month, day)
    except ValueError:
        return ""
    if year_text:
        return f"{year}年{month}月{day}日"
    return f"{month}月{day}日"
