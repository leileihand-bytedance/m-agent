from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.review.main import (
    ReviewConfig,
    _process_queued_single_review,
    _review_file_rejection_message,
    is_pptx_filename,
    is_supported_review_filename,
)
from app.review.core.metrics import ReviewRunMetrics
from app.review.ppt.models import PptFinding, PptReviewResult
from app.review.task_execution import (
    PPT_REVIEW_TASK_TYPE,
    GeneralReviewWorkspace,
)


def test_review_bot_accepts_pptx_without_treating_other_files_as_supported():
    assert is_pptx_filename("经营汇报.pptx") is True
    assert is_pptx_filename("经营汇报.PPTX") is True
    assert is_pptx_filename("经营汇报.ppt") is False
    assert is_supported_review_filename("经营汇报.pptx") is True
    assert is_supported_review_filename("材料.docx") is True
    assert is_supported_review_filename("扫描件.pdf") is False
    assert "另存为 .pptx" in _review_file_rejection_message("旧版汇报.ppt")


def test_queued_ppt_review_uses_independent_processor_and_returns_text_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import app.review.ppt as ppt_review

    calls: list[tuple[Path, Path]] = []

    async def fake_review_pptx(
        path: Path,
        *,
        task_dir: Path,
        metrics: ReviewRunMetrics | None = None,
    ):
        calls.append((path, task_dir))
        assert isinstance(metrics, ReviewRunMetrics)
        metrics.record_model_call("ppt_language")
        return PptReviewResult(
            filename=path.name,
            page_count=3,
            findings=(
                PptFinding(
                    rule_id="ppt-grammar",
                    category="grammar",
                    slide_number=2,
                    element_id="slide:2/shape:1",
                    target_text="持续不断提升",
                    description="‘持续’与‘不断’语义重复",
                ),
            ),
        )

    monkeypatch.setattr(ppt_review, "review_pptx", fake_review_pptx)
    task_dir = tmp_path / "reviews" / "queued-ppt"
    input_dir = task_dir / "input"
    (task_dir / "output").mkdir(parents=True)
    input_dir.mkdir(parents=True)
    input_file = input_dir / "经营汇报.pptx"
    input_file.write_bytes(b"fake-pptx")
    workspace = GeneralReviewWorkspace(
        task_id="task-ppt",
        task_dir=task_dir,
        input_file=input_file,
        filename=input_file.name,
        sender_userid="user-1",
        sender_name="User One",
        task_type=PPT_REVIEW_TASK_TYPE,
        input_kind="pptx",
    )
    config = ReviewConfig(
        wecom_bot_id="bot",
        wecom_bot_secret="secret",
        rules_path=tmp_path / "rules.md",
        reviews_dir=tmp_path / "reviews",
        logs_dir=tmp_path / "logs",
        admin_user_id="",
        admin_name="",
        notification_cooldown=300,
        direct_admin_notifications=False,
        require_registration=False,
    )

    delivery = asyncio.run(
        _process_queued_single_review(
            workspace,
            config=config,
            neican_rules_text="不应读取的 Word 审核规则",
        )
    )

    assert calls == [(input_file, task_dir)]
    assert delivery.kind == "text_parts"
    assert delivery.file_path is None
    assert delivery.text_parts
    assert all("建议" not in part and "修改为" not in part for part in delivery.text_parts)
    saved = json.loads((task_dir / "output" / "result.json").read_text(encoding="utf-8"))
    assert saved["filename"] == "经营汇报.pptx"
    assert saved["findings"][0]["target_text"] == "持续不断提升"
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["capability_id"] == "ppt_review"
    assert meta["observability"]["model_calls"] == 1
    assert meta["observability"]["finding_count"] == 1
