from __future__ import annotations

from .models import PptFindingCategory


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
_RULE_FACTS = {
    "ppt-sequence-duplicate": "同一组序号重复出现",
    "ppt-sequence-reverse": "同一组序号出现倒序",
    "ppt-sequence-skip": "同一组序号存在跳号",
    "ppt-placeholder": "该处存在未清理占位内容",
    "ppt-quote-pair": "引号或成对标点未配对",
    "ppt-consecutive-punctuation": "该处连续重复使用相同标点",
}


def factual_description(
    category: PptFindingCategory,
    *,
    rule_id: str = "",
) -> str:
    """只返回代码维护的事实说明，不透传模型自由文本。"""
    return _RULE_FACTS.get(rule_id, _FACT_FALLBACKS[category])
