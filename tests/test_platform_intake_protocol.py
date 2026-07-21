from pathlib import Path

import pytest

from app.platform.intake import (
    IntakeAction,
    IntakeMaterialRef,
    IntakeOutcome,
    IntakeTaskSubmission,
)
from app.platform.models import UploadedFile
from app.review.intake import ReviewIntakeDecision
from app.writing.intake import IntakeDecision, WritingIntakeStore


def test_submission_payload_keeps_material_references_without_file_content(tmp_path: Path):
    stored = tmp_path / "material.docx"
    stored.write_bytes(b"private-content")
    submission = IntakeTaskSubmission(
        channel="wecom",
        sender_userid="user-1",
        task_type="writer1",
        instructions=("控制在1000字左右",),
        materials=(
            IntakeMaterialRef.text("正文素材"),
            IntakeMaterialRef.url("https://example.com/a"),
            IntakeMaterialRef.file(
                UploadedFile(
                    filename="material.docx",
                    stored_path=str(stored),
                    delete_after_read=True,
                )
            ),
        ),
        metadata={"source": "writing_bot"},
    )

    payload = submission.to_payload()

    assert payload["task_type"] == "writer1"
    assert payload["instructions"] == ["控制在1000字左右"]
    assert payload["materials"][0] == {"kind": "text", "text": "正文素材"}
    assert payload["materials"][1] == {"kind": "url", "url": "https://example.com/a"}
    assert payload["materials"][2]["stored_path"] == str(stored)
    assert "content" not in payload["materials"][2]
    assert "private-content" not in str(payload)

    restored = IntakeTaskSubmission.from_payload(payload)
    assert restored.channel == submission.channel
    assert restored.sender_userid == submission.sender_userid
    assert restored.task_type == submission.task_type
    assert restored.instructions == submission.instructions
    assert [item.kind for item in restored.materials] == [item.kind for item in submission.materials]
    assert restored.materials[-1].uploaded_file is not None
    assert restored.materials[-1].uploaded_file.stored_path == str(stored)


def test_file_material_requires_a_durable_reference(tmp_path: Path):
    with pytest.raises(ValueError, match="持久化"):
        IntakeMaterialRef.file(UploadedFile(filename="memory.docx", content=b"content"))


def test_outcome_requires_submission_only_for_submit_action():
    wait = IntakeOutcome.wait("请继续发送材料")
    cancelled = IntakeOutcome.cancelled("已取消")

    assert wait.action is IntakeAction.WAIT
    assert cancelled.action is IntakeAction.CANCEL
    with pytest.raises(ValueError, match="submission"):
        IntakeOutcome(action=IntakeAction.SUBMIT)


def test_writing_decision_exposes_common_submission_protocol(tmp_path: Path):
    stored = tmp_path / "source.pdf"
    stored.write_bytes(b"pdf")
    decision = IntakeDecision(
        action="run",
        skill_id="writer1",
        text="突出政策背景",
        material_text="第一份文字材料",
        urls=("https://example.com/one",),
        files=(UploadedFile(filename="source.pdf", stored_path=str(stored)),),
    )

    outcome = decision.to_platform_outcome(channel="wecom", sender_userid="user-1")

    assert outcome.action is IntakeAction.SUBMIT
    assert outcome.submission is not None
    assert outcome.submission.task_type == "writer1"
    assert [item.kind.value for item in outcome.submission.materials] == ["text", "url", "file"]


def test_review_decision_exposes_common_submission_and_cancel_protocol(tmp_path: Path):
    stored = tmp_path / "review.docx"
    stored.write_bytes(b"docx")
    run = ReviewIntakeDecision(
        action="run_single",
        files=(UploadedFile(filename="review.docx", stored_path=str(stored)),),
        instructions=("重点看数字口径",),
        revision=3,
    )
    cancelled = ReviewIntakeDecision(action="cancel", reply="已取消")

    run_outcome = run.to_platform_outcome(channel="wecom", sender_userid="user-1")
    cancel_outcome = cancelled.to_platform_outcome(channel="wecom", sender_userid="user-1")

    assert run_outcome.action is IntakeAction.SUBMIT
    assert run_outcome.submission is not None
    assert run_outcome.submission.task_type == "review_single"
    assert run_outcome.submission.metadata["revision"] == 3
    assert cancel_outcome.action is IntakeAction.CANCEL


def test_unknown_legacy_actions_are_rejected_instead_of_silently_submitted():
    decision = IntakeDecision(action="mystery")

    with pytest.raises(ValueError, match="未知"):
        decision.to_platform_outcome(channel="wecom", sender_userid="user-1")


def test_writing_store_cancel_action_clears_durable_materials(tmp_path: Path):
    store = WritingIntakeStore(storage_dir=tmp_path / "intake")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=UploadedFile(filename="material.docx", content=b"content"),
    )
    stored_files = list((tmp_path / "intake").glob("**/files/*"))

    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="取消写作",
    )
    outcome = decision.to_platform_outcome(channel="wecom", sender_userid="user-1")

    assert decision.action == "cancel"
    assert outcome.action is IntakeAction.CANCEL
    assert stored_files and not any(path.exists() for path in stored_files)
