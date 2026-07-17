from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
import hashlib
import json
from pathlib import Path
import re
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from app.review.model_config import build_anthropic_client
from app.review.core.metrics import ReviewRunMetrics
from app.review.core.model_runtime import create_model_message

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


class PptModelOutputTruncatedError(ValueError):
    """模型因输出上限提前停止，返回的结构化结果不完整。"""


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
_MAX_LANGUAGE_BATCH_CHARS = 3000


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
    progress_path: Path | None = None,
    input_digest: str = "",
    metrics: ReviewRunMetrics | None = None,
) -> PptReviewResult:
    """独立审核 PPT 文档；模型候选必须经过原文证据校验。"""
    if model_runner is None:
        runner = _build_default_model_runner(metrics=metrics)
    elif metrics is None:
        runner = model_runner
    else:
        runner = _tracked_model_runner(model_runner, metrics)
    findings = list(check_ppt_rules(document))
    progress = _load_progress(progress_path, input_digest=input_digest)
    stored_batches = cast(dict[str, object], progress["language_batches"])

    language_template = _load_prompt("language.md")
    for batch_number, elements in enumerate(_language_batch_groups(document), 1):
        batch = "\n\n".join(elements)
        batch_key = _stage_key(f"language:{batch_number}", batch)
        stored_payload = stored_batches.get(batch_key)
        if stored_payload is not None:
            payload = _checked_payload(stored_payload)
        else:
            payload = await _run_language_batch(
                runner,
                language_template,
                elements,
            )
            stored_batches[batch_key] = payload
            _write_progress(progress_path, progress)
        for candidate in _local_candidates(payload):
            finding = validate_local_candidate(document, candidate)
            if finding is not None:
                findings.append(finding)

    consistency_complete = True
    try:
        document_text = _document_text(document)
        consistency_key = _stage_key("consistency", document_text)
        stored_consistency = progress.get("consistency")
        if (
            isinstance(stored_consistency, dict)
            and stored_consistency.get("key") == consistency_key
        ):
            consistency_payload = _checked_payload(stored_consistency.get("payload"))
        else:
            consistency_payload = _checked_payload(
                await runner(
                    "consistency",
                    (
                        f"{_load_prompt('consistency.md')}\n\n"
                        f"以下是同一份 PPT 的全部可审核材料：\n{document_text}"
                    ),
                )
            )
            progress["consistency"] = {
                "key": consistency_key,
                "payload": consistency_payload,
            }
            _write_progress(progress_path, progress)
        for candidate in _cross_candidates(consistency_payload):
            finding = validate_cross_candidate(document, candidate)
            if finding is not None:
                findings.append(finding)
    except Exception:
        consistency_complete = False
        if metrics is not None:
            metrics.record_degraded_stage("ppt_consistency")

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
    metrics: ReviewRunMetrics | None = None,
) -> PptReviewResult:
    document = await asyncio.to_thread(extract_ppt_document, path, task_dir=task_dir)
    input_digest = await asyncio.to_thread(_file_sha256, path)
    return await review_ppt_document(
        document,
        model_runner=model_runner,
        progress_path=task_dir / "work" / "ppt_review_progress.json",
        input_digest=input_digest,
        metrics=metrics,
    )


def _build_default_model_runner(
    metrics: ReviewRunMetrics | None = None,
) -> PptModelRunner:
    client, model_name = build_anthropic_client()

    async def run(_stage: str, prompt: str) -> dict[str, object]:
        stage_name = "ppt_language" if _stage == "language" else "ppt_consistency"
        response = await asyncio.to_thread(
            create_model_message,
            client,
            metrics=metrics,
            stage=stage_name,
            model=model_name,
            max_tokens=4096,
            temperature=0,
            thinking={"type": "disabled"},
            timeout=180.0,
            messages=[{"role": "user", "content": prompt}],
        )
        if getattr(response, "stop_reason", "") == "max_tokens":
            raise PptModelOutputTruncatedError("模型输出达到上限")
        text = "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )
        return parse_model_payload(text)

    return run


def _tracked_model_runner(
    runner: PptModelRunner,
    metrics: ReviewRunMetrics,
) -> PptModelRunner:
    async def run(stage: str, prompt: str) -> dict[str, object]:
        stage_name = "ppt_language" if stage == "language" else "ppt_consistency"
        metrics.record_model_call(stage_name)
        started_at = perf_counter()
        try:
            return await runner(stage, prompt)
        except Exception:
            metrics.record_model_failure(stage_name)
            raise
        finally:
            metrics.record_model_elapsed(stage_name, (perf_counter() - started_at) * 1000)

    return run


def _load_progress(
    path: Path | None,
    *,
    input_digest: str,
) -> dict[str, object]:
    if path is None:
        return {
            "schema_version": 1,
            "input_digest": "",
            "language_batches": {},
            "consistency": None,
        }
    if not input_digest.strip():
        raise ValueError("PPT审核断点缺少输入摘要")
    if not path.is_file():
        return {
            "schema_version": 1,
            "input_digest": input_digest,
            "language_batches": {},
            "consistency": None,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("PPT审核断点损坏") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("PPT审核断点版本无效")
    if payload.get("input_digest") != input_digest:
        raise ValueError("PPT审核断点与当前输入不一致")
    batches = payload.get("language_batches")
    if not isinstance(batches, dict):
        raise ValueError("PPT审核分批断点无效")
    for value in batches.values():
        _checked_payload(value)
    consistency = payload.get("consistency")
    if consistency is not None:
        if not isinstance(consistency, dict) or not isinstance(
            consistency.get("key"), str
        ):
            raise ValueError("PPT审核一致性断点无效")
        _checked_payload(consistency.get("payload"))
    return cast(dict[str, object], payload)


def _write_progress(path: Path | None, payload: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError) as exc:
        raise ValueError("PPT审核断点包含不可保存数据") from exc
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def _checked_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        raise ValueError("模型输出格式无效：issues 必须是数组")
    return cast(dict[str, object], payload)


def _stage_key(stage: str, content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"{stage}:{digest}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


def _language_batches(document: PptReviewDocument) -> tuple[str, ...]:
    return tuple("\n\n".join(batch) for batch in _language_batch_groups(document))


def _language_batch_groups(
    document: PptReviewDocument,
) -> tuple[tuple[str, ...], ...]:
    batches: list[tuple[str, ...]] = []
    current: list[str] = []
    current_size = 0
    for tagged in _tagged_elements(document):
        separator_size = 2 if current else 0
        if (
            current
            and current_size + separator_size + len(tagged)
            > _MAX_LANGUAGE_BATCH_CHARS
        ):
            batches.append(tuple(current))
            current = []
            current_size = 0
            separator_size = 0
        current.append(tagged)
        current_size += separator_size + len(tagged)
    if current:
        batches.append(tuple(current))
    return tuple(batches)


async def _run_language_batch(
    runner: PptModelRunner,
    language_template: str,
    elements: tuple[str, ...],
) -> dict[str, object]:
    batch = "\n\n".join(elements)
    try:
        return _checked_payload(
            await runner(
                "language",
                f"{language_template}\n\n以下是待审核材料：\n{batch}",
            )
        )
    except PptModelOutputTruncatedError:
        if len(elements) <= 1:
            raise
        midpoint = len(elements) // 2
        left_payload = await _run_language_batch(
            runner,
            language_template,
            elements[:midpoint],
        )
        right_payload = await _run_language_batch(
            runner,
            language_template,
            elements[midpoint:],
        )
        left_issues = cast(list[object], left_payload["issues"])
        right_issues = cast(list[object], right_payload["issues"])
        return {"issues": [*left_issues, *right_issues]}


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
        related_slide_number = _strict_int(issue.get("related_slide_number"))
        related_element_id = (
            _non_empty_string(issue.get("related_element_id")) or ""
        )
        related_text = _non_empty_string(issue.get("related_text")) or ""
        candidates.append(
            PptLocalCandidate(
                category=cast(PptFindingCategory, category),
                slide_number=slide_number,
                element_id=element_id,
                target_text=target_text,
                description=description,
                related_slide_number=related_slide_number,
                related_element_id=related_element_id,
                related_text=related_text,
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
