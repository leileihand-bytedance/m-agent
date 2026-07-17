from __future__ import annotations

import json
from pathlib import Path

from app.review.capabilities import (
    REVIEW_CAPABILITIES,
    get_review_capability,
    infer_review_capability,
    review_capability_for_task_type,
)
from app.review.core.metrics import ReviewRunMetrics
from app.review.observability import write_review_run_observability
from app.review.task_execution import REVIEW_TASK_TYPES


def test_review_capability_registry_covers_eight_independent_modules() -> None:
    capability_ids = [capability.id for capability in REVIEW_CAPABILITIES]

    assert capability_ids == [
        "general_text_review",
        "general_word_review",
        "html_review",
        "neican_review",
        "halfmonthly_review",
        "official_format_review",
        "ppt_review",
        "multi_file_review",
    ]
    assert len(set(capability_ids)) == 8
    assert {
        capability.task_type
        for capability in REVIEW_CAPABILITIES
        if capability.task_type is not None
    } == set(REVIEW_TASK_TYPES)
    assert get_review_capability("official_format_review").uses_model is False
    assert get_review_capability("multi_file_review").task_type is None


def test_review_capability_can_be_resolved_from_persistent_task_type() -> None:
    capability = review_capability_for_task_type("review_general_docx")

    assert capability.id == "general_word_review"
    assert capability.name == "通用 Word 审核"


def test_legacy_general_tasks_are_only_inferred_from_explicit_input_kind() -> None:
    assert infer_review_capability(
        {"document_type": "general", "original_filename": "材料.docx"}
    ).id == "general_word_review"
    assert infer_review_capability(
        {"document_type": "general", "original_filename": "文字消息.txt"}
    ).id == "general_text_review"
    assert infer_review_capability({"document_type": "general"}) is None


def test_review_run_observability_updates_meta_without_material_content(tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "meta.json").write_text(
        json.dumps({"task_id": "task-1", "original_filename": "材料.docx"}),
        encoding="utf-8",
    )
    metrics = ReviewRunMetrics()
    metrics.record_model_call("local_scan")
    metrics.record_model_call("whole_document_logic")
    metrics.record_model_failure("whole_document_logic")
    metrics.record_model_elapsed("local_scan", 125.5)
    metrics.record_degraded_stage("whole_document_logic")

    write_review_run_observability(
        task_dir,
        capability=get_review_capability("general_word_review"),
        metrics=metrics,
        elapsed_ms=2300.25,
        finding_count=3,
    )

    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["capability_id"] == "general_word_review"
    assert meta["capability_name"] == "通用 Word 审核"
    assert meta["observability"] == {
        "schema_version": 1,
        "elapsed_ms": 2300.25,
        "model_calls": 2,
        "model_failures": 1,
        "model_calls_by_stage": {
            "local_scan": 1,
            "whole_document_logic": 1,
        },
        "model_failures_by_stage": {"whole_document_logic": 1},
        "model_elapsed_ms_by_stage": {"local_scan": 125.5},
        "degraded_stages": ["whole_document_logic"],
        "finding_count": 3,
    }
    assert "paragraph" not in json.dumps(meta, ensure_ascii=False).lower()
