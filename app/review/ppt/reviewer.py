from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
import json
from pathlib import Path
import re
from typing import Any, cast

from app.review.model_config import build_anthropic_client

from .evidence import (
    dedupe_findings,
    validate_cross_candidate,
    validate_local_candidate,
)
from .extractor import extract_ppt_document
from .models import (
    PptCrossCandidate,
    PptCrossFindingCategory,
    PptFindingCategory,
    PptLocalCandidate,
    PptReviewDocument,
    PptReviewResult,
)
from .rules import check_ppt_rules


PptModelRunner = Callable[[str, str], Awaitable[dict[str, object]]]

_PROMPT_DIR = Path(__file__).with_name("prompts")
_JSON_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(\{.*\})\s*```\s*$", re.DOTALL)
_LOCAL_CATEGORIES = {
    "typo",
    "grammar",
    "punctuation",
    "name",
    "placeholder",
    "sequence",
}
_CROSS_CATEGORIES = {"data_inconsistency", "content_inconsistency"}
_MAX_LANGUAGE_BATCH_CHARS = 6000


def parse_model_payload(text: str) -> dict[str, object]:
    """解析模型 JSON，并拒绝不含 issues 数组的输出。"""
    match = _JSON_FENCE_RE.match(text)
    raw = match.group(1) if match else text.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("模型输出格式无效：不是合法 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise ValueError("模型输出格式无效：issues 必须是数组")
    return cast(dict[str, object], payload)


async def review_ppt_document(
    document: PptReviewDocument,
    *,
    model_runner: PptModelRunner | None = None,
) -> PptReviewResult:
    """独立审核 PPT 文档；模型候选必须经过原文证据校验。"""
    runner = model_runner or _build_default_model_runner()
    findings = list(check_ppt_rules(document))

    language_template = _load_prompt("language.md")
    for batch in _language_batches(document):
        payload = await runner(
            "language",
            f"{language_template}\n\n以下是待审核材料：\n{batch}",
        )
        for candidate in _local_candidates(payload):
            finding = validate_local_candidate(document, candidate)
            if finding is not None:
                findings.append(finding)

    consistency_complete = True
    try:
        consistency_payload = await runner(
            "consistency",
            (
                f"{_load_prompt('consistency.md')}\n\n"
                f"以下是同一份 PPT 的全部可审核材料：\n{_document_text(document)}"
            ),
        )
        for candidate in _cross_candidates(consistency_payload):
            finding = validate_cross_candidate(document, candidate)
            if finding is not None:
                findings.append(finding)
    except Exception:
        consistency_complete = False

    return PptReviewResult(
        filename=document.filename,
        page_count=document.page_count,
        findings=dedupe_findings(findings),
        excluded_image_count=document.excluded_image_count,
        warnings=document.warnings,
        consistency_complete=consistency_complete,
    )


async def review_pptx(
    path: Path,
    *,
    task_dir: Path,
    model_runner: PptModelRunner | None = None,
) -> PptReviewResult:
    document = await asyncio.to_thread(extract_ppt_document, path, task_dir=task_dir)
    return await review_ppt_document(document, model_runner=model_runner)


def _build_default_model_runner() -> PptModelRunner:
    client, model_name = build_anthropic_client()

    async def run(_stage: str, prompt: str) -> dict[str, object]:
        response = await asyncio.to_thread(
            client.messages.create,
            model=model_name,
            max_tokens=4096,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
        return parse_model_payload(text)

    return run


def _load_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def _language_batches(document: PptReviewDocument) -> tuple[str, ...]:
    batches: list[str] = []
    current: list[str] = []
    current_size = 0
    for tagged in _tagged_elements(document):
        if current and current_size + len(tagged) > _MAX_LANGUAGE_BATCH_CHARS:
            batches.append("\n\n".join(current))
            current = []
            current_size = 0
        current.append(tagged)
        current_size += len(tagged)
    if current:
        batches.append("\n\n".join(current))
    return tuple(batches)


def _document_text(document: PptReviewDocument) -> str:
    return "\n\n".join(_tagged_elements(document))


def _tagged_elements(document: PptReviewDocument) -> Iterable[str]:
    for slide in document.slides:
        for element in slide.elements:
            yield (
                f"[slide={element.slide_number} element={element.element_id} "
                f"kind={element.kind}]\n{element.text}"
            )


def _local_candidates(payload: dict[str, object]) -> tuple[PptLocalCandidate, ...]:
    candidates: list[PptLocalCandidate] = []
    for issue in _issue_dicts(payload):
        category = issue.get("category")
        if category not in _LOCAL_CATEGORIES:
            continue
        slide_number = _strict_int(issue.get("slide_number"))
        element_id = _non_empty_string(issue.get("element_id"))
        target_text = _non_empty_string(issue.get("target_text"))
        description = _non_empty_string(issue.get("description"))
        if None in (slide_number, element_id, target_text, description):
            continue
        candidates.append(
            PptLocalCandidate(
                category=cast(PptFindingCategory, category),
                slide_number=slide_number,
                element_id=element_id,
                target_text=target_text,
                description=description,
            )
        )
    return tuple(candidates)


def _cross_candidates(payload: dict[str, object]) -> tuple[PptCrossCandidate, ...]:
    candidates: list[PptCrossCandidate] = []
    for issue in _issue_dicts(payload):
        category = issue.get("category")
        if category not in _CROSS_CATEGORIES:
            continue
        slide_number = _strict_int(issue.get("slide_number"))
        related_slide_number = _strict_int(issue.get("related_slide_number"))
        element_id = _non_empty_string(issue.get("element_id"))
        related_element_id = _non_empty_string(issue.get("related_element_id"))
        target_text = _non_empty_string(issue.get("target_text"))
        related_text = _non_empty_string(issue.get("related_text"))
        description = _non_empty_string(issue.get("description"))
        same_subject = issue.get("same_subject")
        same_time_scope = issue.get("same_time_scope")
        same_metric_scope = issue.get("same_metric_scope")
        if None in (
            slide_number,
            related_slide_number,
            element_id,
            related_element_id,
            target_text,
            related_text,
            description,
        ) or not all(
            isinstance(flag, bool)
            for flag in (same_subject, same_time_scope, same_metric_scope)
        ):
            continue
        candidates.append(
            PptCrossCandidate(
                category=cast(PptCrossFindingCategory, category),
                slide_number=slide_number,
                element_id=element_id,
                target_text=target_text,
                related_slide_number=related_slide_number,
                related_element_id=related_element_id,
                related_text=related_text,
                description=description,
                same_subject=cast(bool, same_subject),
                same_time_scope=cast(bool, same_time_scope),
                same_metric_scope=cast(bool, same_metric_scope),
            )
        )
    return tuple(candidates)


def _issue_dicts(payload: dict[str, object]) -> Iterable[dict[str, Any]]:
    issues = payload.get("issues")
    if not isinstance(issues, list):
        raise ValueError("模型输出格式无效：issues 必须是数组")
    for issue in issues:
        if isinstance(issue, dict):
            yield issue


def _strict_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _non_empty_string(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
