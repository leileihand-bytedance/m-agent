from __future__ import annotations

from .models import PptFinding, PptReviewResult
from .text_policy import factual_description


_CATEGORY_LABELS = {
    "typo": "错别字",
    "grammar": "语病",
    "punctuation": "标点",
    "name": "名称不一致",
    "placeholder": "占位内容",
    "sequence": "序号不连贯",
    "data_inconsistency": "数据不一致",
    "content_inconsistency": "内容不一致",
}
_BOUNDARY = "本次未审核图片文字和演讲者备注。"


def format_ppt_review_messages(
    result: PptReviewResult,
    *,
    max_chars: int = 3500,
) -> tuple[str, ...]:
    """把 PPT 审核结果转成只含事实、可分段发送的文字。"""
    if max_chars < 200:
        raise ValueError("单段文字长度上限不能小于 200")

    if not result.findings:
        status = "未发现低级文字或内部一致性问题。"
        if not result.consistency_complete:
            status = "已完成文字检查，但全篇一致性检查未完成。"
        elif result.warnings:
            status = "未发现已成功读取范围内的低级文字或内部一致性问题。"
        warning_blocks = _warning_blocks(result.warnings)
        return _pack_blocks(
            f"PPT审核完成：{status}",
            warning_blocks,
            _BOUNDARY,
            max_chars=max_chars,
        )

    header = f"PPT审核完成：共发现{len(result.findings)}项问题。"
    if not result.consistency_complete:
        header += "\n注意：全篇一致性检查未完成。"
    blocks = tuple(
        _format_finding(number, finding)
        for number, finding in enumerate(result.findings, 1)
    ) + _warning_blocks(result.warnings)
    return _pack_blocks(header, blocks, _BOUNDARY, max_chars=max_chars)


def _format_finding(number: int, finding: PptFinding) -> str:
    label = _CATEGORY_LABELS[finding.category]
    description = factual_description(finding.category, finding.description)
    if finding.related_slide_number is not None:
        return (
            f"{number}.【第{finding.slide_number}页 ↔ "
            f"第{finding.related_slide_number}页｜{label}】\n"
            f"原文一：{finding.target_text}\n"
            f"原文二：{finding.related_text}\n"
            f"问题：{description}"
        )
    return (
        f"{number}.【第{finding.slide_number}页｜{label}】\n"
        f"原文：{finding.target_text}\n"
        f"问题：{description}"
    )


def _warning_blocks(warnings: tuple[str, ...]) -> tuple[str, ...]:
    unique = tuple(dict.fromkeys(item.strip() for item in warnings if item.strip()))
    if not unique:
        return ()
    return ("读取提示：\n" + "\n".join(f"- {item}" for item in unique),)


def _pack_blocks(
    header: str,
    blocks: tuple[str, ...],
    boundary: str,
    *,
    max_chars: int,
) -> tuple[str, ...]:
    content_limit = max_chars - len(boundary) - 2
    if len(header) > content_limit:
        raise ValueError("单段文字长度上限过小")

    messages: list[str] = []
    current = header
    for block in blocks:
        pieces = _split_block(block, content_limit)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= content_limit:
                current = candidate
                continue
            messages.append(current)
            current = piece
    if current:
        messages.append(current)
    messages[-1] = f"{messages[-1]}\n\n{boundary}"
    if any(len(message) > max_chars for message in messages):
        raise ValueError("审核结果存在无法分段的超长内容")
    return tuple(messages)


def _split_block(block: str, max_chars: int) -> tuple[str, ...]:
    if len(block) <= max_chars:
        return (block,)
    return tuple(
        block[start : start + max_chars]
        for start in range(0, len(block), max_chars)
    )
