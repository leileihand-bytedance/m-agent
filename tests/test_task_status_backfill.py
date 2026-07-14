from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backfill_task_status import backfill_task_statuses  # noqa: E402


def test_backfill_task_statuses_classifies_historical_tasks(tmp_path):
    writing_root = tmp_path / "writing"
    review_root = tmp_path / "review"

    completed = writing_root / "2026" / "07" / "20260714-completed"
    (completed / "output").mkdir(parents=True)
    (completed / "meta.json").write_text("{}", encoding="utf-8")
    (completed / "output" / "result.json").write_text(
        json.dumps(
            {
                "skill_id": "writer1",
                "needs_clarification": False,
                "message": "已完成",
                "output": {"title": "标题", "body": "正文"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    needs_input = writing_root / "2026" / "07" / "20260714-needs-input"
    (needs_input / "output").mkdir(parents=True)
    (needs_input / "meta.json").write_text("{}", encoding="utf-8")
    (needs_input / "output" / "result.json").write_text(
        json.dumps(
            {
                "skill_id": None,
                "needs_clarification": True,
                "message": "请说明要写什么",
                "output": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    incomplete = writing_root / "2026" / "07" / "20260714-incomplete"
    incomplete.mkdir(parents=True)
    (incomplete / "meta.json").write_text("{}", encoding="utf-8")

    legacy_review = review_root / "2026" / "07" / "20260714-001"
    (legacy_review / "output").mkdir(parents=True)
    (legacy_review / "meta.md").write_text("历史元信息", encoding="utf-8")
    (legacy_review / "output" / "report.md").write_text("审核报告", encoding="utf-8")

    preview = backfill_task_statuses(
        writing_root=writing_root,
        review_root=review_root,
        apply=False,
    )
    assert preview.planned == 4
    assert not (completed / "status.json").exists()

    applied = backfill_task_statuses(
        writing_root=writing_root,
        review_root=review_root,
        apply=True,
    )
    assert applied.written == 4
    assert json.loads((completed / "status.json").read_text(encoding="utf-8"))[
        "processing_status"
    ] == "completed"
    assert json.loads((needs_input / "status.json").read_text(encoding="utf-8"))[
        "processing_status"
    ] == "needs_input"
    assert json.loads((incomplete / "status.json").read_text(encoding="utf-8"))[
        "processing_status"
    ] == "incomplete"
    review_status = json.loads((legacy_review / "status.json").read_text(encoding="utf-8"))
    assert review_status["processing_status"] == "completed"
    assert review_status["delivery_status"] == "unknown"

    repeated = backfill_task_statuses(
        writing_root=writing_root,
        review_root=review_root,
        apply=True,
    )
    assert repeated.planned == 0
    assert repeated.written == 0
    assert repeated.skipped == 4
