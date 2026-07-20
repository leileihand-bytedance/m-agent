"""Stable identities for review sub-capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ReviewCapability:
    id: str
    name: str
    task_type: str | None
    document_type: str
    uses_model: bool = True


REVIEW_CAPABILITIES = (
    ReviewCapability(
        "general_text_review",
        "通用文字审核",
        "review_general_text",
        "general_text",
    ),
    ReviewCapability(
        "general_word_review",
        "通用 Word 审核",
        "review_general_docx",
        "general",
    ),
    ReviewCapability(
        "html_review",
        "静态 HTML 审核",
        "review_general_html",
        "general_html",
    ),
    ReviewCapability(
        "neican_review",
        "内参审核",
        "review_neican_docx",
        "neican",
    ),
    ReviewCapability(
        "halfmonthly_review",
        "半月报审核",
        "review_halfmonthly_docx",
        "half_monthly",
    ),
    ReviewCapability(
        "official_format_review",
        "公文格式审核",
        "review_official_format_docx",
        "official_format",
        uses_model=False,
    ),
    ReviewCapability(
        "ppt_review",
        "PPTX 审核",
        "review_pptx",
        "ppt",
    ),
    ReviewCapability(
        "multi_file_review",
        "多文件联合审核",
        "review_multi_file_docx",
        "multi_file",
    ),
)

_BY_ID: Mapping[str, ReviewCapability] = {
    capability.id: capability for capability in REVIEW_CAPABILITIES
}
_BY_TASK_TYPE: Mapping[str, ReviewCapability] = {
    capability.task_type: capability
    for capability in REVIEW_CAPABILITIES
    if capability.task_type is not None
}
_BY_DOCUMENT_TYPE: Mapping[str, ReviewCapability] = {
    capability.document_type: capability for capability in REVIEW_CAPABILITIES
}


def get_review_capability(capability_id: str) -> ReviewCapability:
    try:
        return _BY_ID[capability_id]
    except KeyError as exc:
        raise ValueError(f"未登记的审核子能力：{capability_id}") from exc


def review_capability_for_task_type(task_type: str) -> ReviewCapability:
    try:
        return _BY_TASK_TYPE[task_type]
    except KeyError as exc:
        raise ValueError(f"未登记的审核任务类型：{task_type}") from exc


def infer_review_capability(meta: Mapping[str, object]) -> ReviewCapability | None:
    capability_id = str(meta.get("capability_id", "") or "").strip()
    if capability_id in _BY_ID:
        return _BY_ID[capability_id]
    task_type = str(meta.get("task_type", "") or "").strip()
    if task_type in _BY_TASK_TYPE:
        return _BY_TASK_TYPE[task_type]
    document_type = str(meta.get("document_type", "") or "").strip()
    if document_type == "general":
        filename = str(meta.get("original_filename", "") or "").strip().lower()
        if filename.endswith(".docx"):
            return _BY_ID["general_word_review"]
        if filename.endswith(".txt") or filename == "文字消息":
            return _BY_ID["general_text_review"]
        return None
    return _BY_DOCUMENT_TYPE.get(document_type)


__all__ = [
    "REVIEW_CAPABILITIES",
    "ReviewCapability",
    "get_review_capability",
    "infer_review_capability",
    "review_capability_for_task_type",
]
