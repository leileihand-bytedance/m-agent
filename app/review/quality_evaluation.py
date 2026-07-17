"""通用审核真实文件质量评测工具。"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import time
from collections.abc import Awaitable
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .document_type import DocumentType, detect_document_type
from .error_marker import mark_errors_in_docx
from .parser import open_docx_sanitized, parse_docx
from .review_metrics import ReviewRunMetrics
from .core.models import ReviewResult


_ATTACHMENT_REFERENCE_RE = re.compile(
    r"(?:详见|参见|见|填写|按照|依据)?附件\s*[一二三四五六七八九十百0-9]+"
    r"|附件\s*[:：]"
    r"|^\s*附件\s*$",
    re.MULTILINE,
)
_QUESTIONNAIRE_RE = re.compile(r"问卷|调查表|调研题目|请填写")
_LONG_DOCUMENT_MIN_CHARS = 20_000
_NEAR_DUPLICATE_MIN_CHARS = 1_000
_NEAR_DUPLICATE_MIN_LENGTH_RATIO = 0.95
_NEAR_DUPLICATE_MIN_OVERLAP = 0.80
_CONTENT_SHINGLE_SIZE = 32
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SCORING_COLUMNS = (
    "样本编号",
    "原文件名",
    "问题编号",
    "规则编号",
    "错误原文",
    "错误片段",
    "系统说明",
    "有效性",
    "标注位置",
    "建议质量",
    "重要程度",
    "人工备注",
)


@dataclass(frozen=True)
class ReviewSampleCandidate:
    """一个去重后的真实通用审核候选文件。"""

    source_path: Path
    source_filename: str
    sha256: str
    total_chars: int
    total_paragraphs: int
    table_count: int
    has_attachment_reference: bool
    is_questionnaire: bool
    modified_at: float
    content_sha256: str = ""
    content_signature: frozenset[int] = frozenset()

    @property
    def length_bucket(self) -> str:
        if self.total_chars < 2_000:
            return "短文（少于2千字）"
        if self.total_chars < 10_000:
            return "常规（2千至1万字）"
        if self.total_chars < _LONG_DOCUMENT_MIN_CHARS:
            return "较长（1万至2万字）"
        return "长文（2万字以上）"


@dataclass(frozen=True)
class SelectedReviewCase:
    """一次基线运行中冻结的样本编号和主分类。"""

    case_id: str
    primary_category: str
    candidate: ReviewSampleCandidate


@dataclass(frozen=True)
class BaselineRunSummary:
    """一批真实文件评测的运行汇总。"""

    run_id: str
    run_dir: Path
    total_cases: int
    completed_cases: int
    failed_cases: int
    total_findings: int
    total_model_calls: int
    total_model_failures: int
    elapsed_seconds: float


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _content_identity(paragraphs: Sequence[str]) -> tuple[str, frozenset[int]]:
    normalized = re.sub(r"\s+", "", "\n".join(paragraphs))
    content_sha256 = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if len(normalized) <= _CONTENT_SHINGLE_SIZE:
        return content_sha256, frozenset({hash(normalized)})

    signature = frozenset(
        hash(normalized[index:index + _CONTENT_SHINGLE_SIZE])
        for index in range(len(normalized) - _CONTENT_SHINGLE_SIZE + 1)
    )
    return content_sha256, signature


def _are_near_duplicates(
    left: ReviewSampleCandidate,
    right: ReviewSampleCandidate,
) -> bool:
    if left.content_sha256 and left.content_sha256 == right.content_sha256:
        return True
    shorter = min(left.total_chars, right.total_chars)
    longer = max(left.total_chars, right.total_chars)
    if shorter < _NEAR_DUPLICATE_MIN_CHARS or not longer:
        return False
    if shorter / longer < _NEAR_DUPLICATE_MIN_LENGTH_RATIO:
        return False
    if not left.content_signature or not right.content_signature:
        return False

    intersection = len(left.content_signature & right.content_signature)
    smaller_signature = min(len(left.content_signature), len(right.content_signature))
    return (
        bool(smaller_signature)
        and intersection / smaller_signature >= _NEAR_DUPLICATE_MIN_OVERLAP
    )


def _inspect_candidate(path: Path, digest: str) -> ReviewSampleCandidate | None:
    parsed = parse_docx(path)
    if detect_document_type(path.name, parsed.paragraphs) is not DocumentType.GENERAL:
        return None

    document = open_docx_sanitized(path)
    searchable_text = "\n".join(parsed.paragraphs)
    content_sha256, content_signature = _content_identity(parsed.paragraphs)
    return ReviewSampleCandidate(
        source_path=path,
        source_filename=path.name,
        sha256=digest,
        total_chars=parsed.total_chars,
        total_paragraphs=parsed.total_paragraphs,
        table_count=len(document.tables),
        has_attachment_reference=bool(_ATTACHMENT_REFERENCE_RE.search(searchable_text)),
        is_questionnaire=bool(
            _QUESTIONNAIRE_RE.search(f"{path.stem}\n{searchable_text[:5000]}")
        ),
        modified_at=path.stat().st_mtime,
        content_sha256=content_sha256,
        content_signature=content_signature,
    )


def discover_general_candidates(review_tasks_root: Path) -> list[ReviewSampleCandidate]:
    """发现历史输入目录中的去重通用 Word 文件。"""
    latest_by_digest: dict[str, Path] = {}
    for path in sorted(review_tasks_root.glob("**/input/*.docx")):
        if not path.is_file():
            continue
        digest = _sha256(path)
        current = latest_by_digest.get(digest)
        if current is None or path.stat().st_mtime > current.stat().st_mtime:
            latest_by_digest[digest] = path

    candidates: list[ReviewSampleCandidate] = []
    for digest, path in latest_by_digest.items():
        try:
            candidate = _inspect_candidate(path, digest)
        except Exception:
            continue
        if candidate is not None:
            candidates.append(candidate)

    ordered = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.modified_at,
            candidate.sha256,
            str(candidate.source_path),
        ),
    )
    content_unique: list[ReviewSampleCandidate] = []
    for candidate in ordered:
        if any(
            _are_near_duplicates(candidate, existing)
            for existing in content_unique
        ):
            continue
        content_unique.append(candidate)
    return content_unique


def select_baseline_cases(
    candidates: Sequence[ReviewSampleCandidate],
    limit: int = 5,
) -> list[SelectedReviewCase]:
    """按问卷、附件、长文、表格和常规材料顺序选取不重复样本。"""
    if limit < 1:
        return []

    ordered = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.modified_at,
            candidate.sha256,
            str(candidate.source_path),
        ),
    )
    slots: tuple[tuple[str, Callable[[ReviewSampleCandidate], bool]], ...] = (
        ("问卷", lambda candidate: candidate.is_questionnaire),
        ("附件引用", lambda candidate: candidate.has_attachment_reference),
        ("长文", lambda candidate: candidate.total_chars >= _LONG_DOCUMENT_MIN_CHARS),
        ("表格", lambda candidate: candidate.table_count > 0),
        ("常规材料", lambda candidate: candidate.total_chars < _LONG_DOCUMENT_MIN_CHARS),
    )

    selected_pairs: list[tuple[str, ReviewSampleCandidate]] = []
    used_digests: set[str] = set()
    for category, predicate in slots:
        if len(selected_pairs) >= limit:
            break
        match = next(
            (
                candidate
                for candidate in ordered
                if candidate.sha256 not in used_digests
                and not any(
                    _are_near_duplicates(candidate, selected_candidate)
                    for _, selected_candidate in selected_pairs
                )
                and predicate(candidate)
            ),
            None,
        )
        if match is None:
            continue
        selected_pairs.append((category, match))
        used_digests.add(match.sha256)

    for candidate in ordered:
        if len(selected_pairs) >= limit:
            break
        if candidate.sha256 in used_digests:
            continue
        if any(
            _are_near_duplicates(candidate, selected_candidate)
            for _, selected_candidate in selected_pairs
        ):
            continue
        selected_pairs.append(("补充样本", candidate))
        used_digests.add(candidate.sha256)

    return [
        SelectedReviewCase(
            case_id=f"G-{index:03d}",
            primary_category=category,
            candidate=candidate,
        )
        for index, (category, candidate) in enumerate(selected_pairs, 1)
    ]


def _resolve_inside(path: Path, parent: Path, *, label: str) -> Path:
    resolved_parent = parent.expanduser().resolve(strict=False)
    resolved_path = path.expanduser().resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_parent)
    except ValueError as exc:
        raise ValueError(f"{label}必须位于 {resolved_parent}") from exc
    return resolved_path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _candidate_payload(
    selected_case: SelectedReviewCase,
    *,
    data_root: Path,
) -> dict[str, Any]:
    candidate = selected_case.candidate
    return {
        "case_id": selected_case.case_id,
        "primary_category": selected_case.primary_category,
        "source_filename": candidate.source_filename,
        "source_path": str(candidate.source_path.resolve().relative_to(data_root)),
        "sha256": candidate.sha256,
        "content_sha256": candidate.content_sha256,
        "total_chars": candidate.total_chars,
        "total_paragraphs": candidate.total_paragraphs,
        "length_bucket": candidate.length_bucket,
        "table_count": candidate.table_count,
        "has_attachment_reference": candidate.has_attachment_reference,
        "is_questionnaire": candidate.is_questionnaire,
    }


def _finding_payload(case_id: str, index: int, finding: Any) -> dict[str, Any]:
    return {
        "finding_id": f"{case_id}-F{index:03d}",
        "rule_id": finding.rule_id,
        "paragraph_index_internal": finding.paragraph_index,
        "original_text": finding.original_text,
        "target_text": finding.target_text,
        "description": finding.description,
    }


def _spreadsheet_safe(value: Any) -> str:
    text = str(value or "")
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{text}"
    return text


def _scoring_row(
    selected_case: SelectedReviewCase,
    finding_payload: dict[str, Any],
) -> dict[str, str]:
    return {
        "样本编号": _spreadsheet_safe(selected_case.case_id),
        "原文件名": _spreadsheet_safe(selected_case.candidate.source_filename),
        "问题编号": _spreadsheet_safe(finding_payload["finding_id"]),
        "规则编号": _spreadsheet_safe(finding_payload["rule_id"]),
        "错误原文": _spreadsheet_safe(finding_payload["original_text"]),
        "错误片段": _spreadsheet_safe(finding_payload["target_text"]),
        "系统说明": _spreadsheet_safe(finding_payload["description"]),
        "有效性": "",
        "标注位置": "",
        "建议质量": "",
        "重要程度": "",
        "人工备注": "",
    }


async def run_baseline(
    cases: Sequence[SelectedReviewCase],
    *,
    run_id: str,
    data_root: Path,
    output_root: Path,
    rules_text: str,
    reviewer: Callable[..., Awaitable[ReviewResult]] | None = None,
    resume: bool = False,
) -> BaselineRunSummary:
    """顺序运行一批真实文件，并生成机器结果和人工评分 CSV。"""
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id 只能包含字母、数字、点、下划线和短横线")

    resolved_data_root = data_root.expanduser().resolve(strict=False)
    resolved_output_root = _resolve_inside(
        output_root,
        resolved_data_root,
        label="评测输出",
    )
    resolved_output_root.mkdir(parents=True, exist_ok=True)
    run_dir = resolved_output_root / run_id

    for selected_case in cases:
        _resolve_inside(
            selected_case.candidate.source_path,
            resolved_data_root,
            label="评测源文件",
        )

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "case_count": len(cases),
        "cases": [
            _candidate_payload(selected_case, data_root=resolved_data_root)
            for selected_case in cases
        ],
    }
    manifest_path = run_dir / "manifest.json"
    if resume:
        if not manifest_path.is_file():
            raise FileNotFoundError(f"找不到可恢复的评测清单：{manifest_path}")
        existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_identity = [
            (item["case_id"], item["sha256"])
            for item in manifest["cases"]
        ]
        existing_identity = [
            (item.get("case_id"), item.get("sha256"))
            for item in existing_manifest.get("cases", [])
        ]
        if existing_identity != expected_identity:
            raise ValueError("恢复评测失败：当前样本清单与已有清单不一致")
    else:
        run_dir.mkdir(parents=False, exist_ok=False)
        _write_json(manifest_path, manifest)

    cases_dir = run_dir / "cases"
    cases_dir.mkdir(exist_ok=resume)

    if reviewer is None:
        from .general_reviewer import review_general

        reviewer = review_general

    scoring_rows: list[dict[str, str]] = []
    completed_cases = 0
    failed_cases = 0
    total_findings = 0
    total_model_calls = 0
    total_model_failures = 0
    total_elapsed_seconds = 0.0

    for selected_case in cases:
        candidate = selected_case.candidate
        case_dir = cases_dir / selected_case.case_id
        case_dir.mkdir(exist_ok=resume)
        source_copy = case_dir / "source.docx"
        marked_path = case_dir / "marked.docx"
        result_path = case_dir / "result.json"

        if resume and result_path.is_file():
            previous_result = json.loads(result_path.read_text(encoding="utf-8"))
            if previous_result.get("status") == "completed":
                previous_result.setdefault("model_failures", 0)
                previous_result.setdefault("degraded_stages", [])
                _write_json(result_path, previous_result)
                previous_findings = previous_result.get("findings", [])
                scoring_rows.extend(
                    _scoring_row(selected_case, finding_payload)
                    for finding_payload in previous_findings
                )
                completed_cases += 1
                total_findings += int(previous_result.get("finding_count", 0) or 0)
                total_model_calls += int(previous_result.get("model_calls", 0) or 0)
                total_model_failures += int(
                    previous_result.get("model_failures", 0) or 0
                )
                total_elapsed_seconds += float(
                    previous_result.get("elapsed_seconds", 0) or 0
                )
                continue

        shutil.copy2(candidate.source_path, source_copy)

        metrics = ReviewRunMetrics()
        case_started = time.perf_counter()
        case_payload: dict[str, Any]
        try:
            parsed = parse_docx(source_copy)
            result = await reviewer(
                parsed.paragraphs,
                rules_text,
                candidate.source_filename,
                metrics=metrics,
            )
            llm_errors = [
                finding
                for finding in result.findings
                if finding.rule_id == "__llm_error__"
            ]
            review_findings = [
                finding
                for finding in result.findings
                if finding.rule_id != "__llm_error__"
            ]
            finding_payloads = [
                _finding_payload(selected_case.case_id, index, finding)
                for index, finding in enumerate(review_findings, 1)
            ]
            mark_errors_in_docx(source_copy, marked_path, review_findings)
            scoring_rows.extend(
                _scoring_row(selected_case, finding_payload)
                for finding_payload in finding_payloads
            )
            total_findings += len(finding_payloads)

            degradation_errors: list[str] = []
            if llm_errors:
                degradation_errors.append(
                    "; ".join(finding.description for finding in llm_errors)
                )
            if metrics.degraded_stages:
                degradation_errors.append(
                    "未完成审核阶段：" + "、".join(metrics.degraded_stages)
                )

            if degradation_errors:
                status = "partial_failed"
                failed_cases += 1
                error = "; ".join(degradation_errors)
            else:
                status = "completed"
                completed_cases += 1
                error = None

            case_payload = {
                "schema_version": 1,
                "case_id": selected_case.case_id,
                "status": status,
                "error": error,
                "elapsed_seconds": round(time.perf_counter() - case_started, 3),
                "model_calls": metrics.model_calls,
                "model_failures": metrics.model_failures,
                "degraded_stages": list(metrics.degraded_stages),
                "finding_count": len(finding_payloads),
                "findings": finding_payloads,
            }
        except Exception as exc:
            failed_cases += 1
            case_payload = {
                "schema_version": 1,
                "case_id": selected_case.case_id,
                "status": "failed",
                "error": str(exc),
                "elapsed_seconds": round(time.perf_counter() - case_started, 3),
                "model_calls": metrics.model_calls,
                "model_failures": metrics.model_failures,
                "degraded_stages": list(metrics.degraded_stages),
                "finding_count": 0,
                "findings": [],
            }

        total_model_calls += metrics.model_calls
        total_model_failures += metrics.model_failures
        total_elapsed_seconds += float(case_payload["elapsed_seconds"])
        _write_json(result_path, case_payload)

    with (run_dir / "scoring.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as destination:
        writer = csv.DictWriter(destination, fieldnames=_SCORING_COLUMNS)
        writer.writeheader()
        writer.writerows(scoring_rows)

    summary = BaselineRunSummary(
        run_id=run_id,
        run_dir=run_dir,
        total_cases=len(cases),
        completed_cases=completed_cases,
        failed_cases=failed_cases,
        total_findings=total_findings,
        total_model_calls=total_model_calls,
        total_model_failures=total_model_failures,
        elapsed_seconds=round(total_elapsed_seconds, 3),
    )
    _write_json(
        run_dir / "summary.json",
        {
            "schema_version": 1,
            "run_id": summary.run_id,
            "total_cases": summary.total_cases,
            "completed_cases": summary.completed_cases,
            "failed_cases": summary.failed_cases,
            "total_findings": summary.total_findings,
            "total_model_calls": summary.total_model_calls,
            "total_model_failures": summary.total_model_failures,
            "elapsed_seconds": summary.elapsed_seconds,
        },
    )
    return summary
