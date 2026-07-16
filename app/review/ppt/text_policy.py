from __future__ import annotations

import re

from .models import PptFindingCategory


_ADVICE_RE = re.compile(
    r"[，,；;。\s]*(?:建议(?:修改|改为)?|请修改|修改为|改为|应改为|"
    r"正确写法|可改为|应为|可调整为|请调整).*",
    re.DOTALL,
)
_TRAILING_PUNCTUATION_RE = re.compile(r"[，,；;。\s]+$")
_FACT_FALLBACKS: dict[PptFindingCategory, str] = {
    "typo": "该处存在文字错误",
    "grammar": "该处存在明显语病",
    "punctuation": "该处存在标点问题",
    "name": "该处名称写法前后不一致",
    "placeholder": "该处存在未清理占位内容",
    "sequence": "该处序号不连贯",
    "data_inconsistency": "两处原文数据不一致",
    "content_inconsistency": "两处原文内容不一致",
}


def factual_description(
    category: PptFindingCategory,
    description: str,
) -> str:
    """移除模型可能夹带的修改指令，只保留事实描述。"""
    factual = _ADVICE_RE.sub("", description.strip(), count=1)
    factual = _TRAILING_PUNCTUATION_RE.sub("", factual).strip()
    return factual or _FACT_FALLBACKS[category]
