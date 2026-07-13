"""审核 Bot 的持久化文件暂存与指令衔接测试。"""

from __future__ import annotations

import json
from pathlib import Path

from app.platform.models import UploadedFile
from app.review.intake import ReviewIntakeStore, infer_primary_file_index


def _file(name: str, content: bytes) -> UploadedFile:
    return UploadedFile(filename=name, content=content)


def test_format_review_can_reuse_file_sent_before_instruction(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)

    received = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("公文.docx", b"docx-content"),
    )
    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="帮我审核一下格式",
    )

    assert received.action == "wait_auto"
    assert decision.action == "run_format"
    assert decision.files[0].filename == "公文.docx"
    assert decision.files[0].read_bytes() == b"docx-content"


def test_format_review_can_reuse_single_file_after_auto_content_review_started(
    tmp_path: Path,
):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    queued = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("公文.docx", b"docx-content"),
    )
    content_review = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=queued.revision,
        file_texts=("公文正文",),
    )

    format_review = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="帮我审核一下格式",
    )

    assert content_review.action == "run_single"
    assert format_review.action == "run_format"
    assert format_review.files[0].read_bytes() == b"docx-content"


def test_next_default_file_does_not_join_previously_reviewed_recent_file(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    first = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("第一份.docx", b"first"),
    )
    store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=first.revision,
        file_texts=("第一份正文",),
    )

    second = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("第二份.docx", b"second"),
    )
    decision = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=second.revision,
        file_texts=("第二份正文",),
    )

    assert second.action == "wait_auto"
    assert [item.filename for item in second.files] == ["第二份.docx"]
    assert decision.action == "run_single"
    assert decision.files[0].read_bytes() == b"second"


def test_format_review_still_supports_instruction_before_file(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)

    waiting = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="按公文格式检查",
    )
    decision = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("公文.docx", b"docx-content"),
    )

    assert waiting.action == "wait"
    assert "发送" in waiting.reply
    assert decision.action == "run_format"


def test_multi_file_review_can_start_after_first_file(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("正文.docx", b"main"),
    )

    waiting = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="这几个文件联合审核",
    )
    second = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件1.docx", b"attachment"),
    )
    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="开始审核",
    )

    assert waiting.action == "wait"
    assert "刚发的文件" in waiting.reply
    assert "作为正文" not in waiting.reply
    assert "系统会自动开始" in waiting.reply
    assert "发完后回复" not in waiting.reply
    assert second.action == "wait_auto"
    assert decision.action == "run_multi"
    assert [item.filename for item in decision.files] == ["正文.docx", "附件1.docx"]
    assert [item.read_bytes() for item in decision.files] == [b"main", b"attachment"]
    assert decision.primary_file_index == 0


def test_default_single_file_auto_finalizes_without_user_instruction(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)

    queued = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("普通材料.docx", b"single"),
    )
    decision = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=queued.revision,
        file_texts=("普通材料正文",),
    )

    assert queued.action == "wait_auto"
    assert decision.action == "run_single"
    assert [item.filename for item in decision.files] == ["普通材料.docx"]
    assert decision.files[0].read_bytes() == b"single"


def test_start_signal_during_default_single_file_wait_does_not_request_second_file(
    tmp_path: Path,
):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("普通材料.docx", b"single"),
    )

    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="开始审核",
    )

    assert decision.action == "wait"
    assert "正在审核" in decision.reply
    assert "至少" not in decision.reply


def test_default_multiple_files_auto_finalize_as_joint_review_without_text(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件1-名单.docx", b"attachment"),
    )
    queued = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("会议通知.docx", b"main"),
    )

    decision = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=queued.revision,
        file_texts=("附件1：参会名单", "请参阅附件1。"),
    )

    assert decision.action == "run_multi"
    assert [item.filename for item in decision.files] == ["附件1-名单.docx", "会议通知.docx"]
    assert decision.primary_file_index == 1


def test_new_file_invalidates_older_auto_finalize_timer(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    first = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("正文.docx", b"main"),
    )
    second = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件1.docx", b"attachment"),
    )

    stale = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=first.revision,
        file_texts=("正文",),
    )
    decision = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=second.revision,
        file_texts=("详见附件1。", "附件1：名单"),
    )

    assert stale.action == "stale"
    assert decision.action == "run_multi"
    assert len(decision.files) == 2


def test_default_auto_batch_can_finalize_from_persisted_state_after_restart(tmp_path: Path):
    first_store = ReviewIntakeStore(storage_dir=tmp_path)
    first_store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件1-名单.docx", b"attachment"),
    )
    queued = first_store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("会议通知.docx", b"main"),
    )

    restarted_store = ReviewIntakeStore(storage_dir=tmp_path)
    decision = restarted_store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=queued.revision,
        file_texts=("附件1：参会名单", "请参阅附件1。"),
    )

    assert decision.action == "run_multi"
    assert decision.primary_file_index == 1
    assert [item.read_bytes() for item in decision.files] == [b"attachment", b"main"]


def test_primary_inference_uses_content_when_filenames_are_ambiguous():
    files = [_file("材料甲.docx", b"a"), _file("材料乙.docx", b"b")]

    primary = infer_primary_file_index(
        files,
        file_texts=("请填写附件1并反馈。", "附件1：议案意见反馈表"),
    )

    assert primary == 0


def test_default_multiple_files_ask_only_when_filename_and_content_are_ambiguous(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料甲.docx", b"a"),
    )
    queued = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料乙.docx", b"b"),
    )

    asking = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=queued.revision,
        file_texts=("甲材料正文", "乙材料正文"),
    )
    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="第2个是正文",
    )

    assert asking.action == "wait"
    assert "无法确定哪一份是主文件" in asking.reply
    assert decision.action == "run_multi"
    assert decision.primary_file_index == 1


def test_new_file_after_primary_prompt_restarts_automatic_primary_inference(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料甲.docx", b"a"),
    )
    second = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料乙.docx", b"b"),
    )
    asking = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=second.revision,
        file_texts=("甲材料正文", "乙材料正文"),
    )

    third = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件1-名单.docx", b"c"),
    )
    decision = store.finalize_auto_batch(
        channel="wecom",
        sender_userid="user-1",
        expected_revision=third.revision,
        file_texts=("请参阅附件1。", "乙材料正文", "附件1：名单"),
    )

    assert asking.action == "wait"
    assert decision.action == "run_multi"
    assert decision.primary_file_index == 0


def test_multi_file_review_does_not_default_first_file_as_primary(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料甲.docx", b"a"),
    )
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料乙.docx", b"b"),
    )

    asking = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="开始审核",
    )
    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="第2个是正文",
    )

    assert asking.action == "wait"
    assert "无法确定哪一份是主文件" in asking.reply
    assert "1. 材料甲.docx" in asking.reply
    assert "2. 材料乙.docx" in asking.reply
    assert decision.action == "run_multi"
    assert decision.primary_file_index == 1


def test_multi_file_review_accepts_primary_filename_selection(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料甲.docx", b"a"),
    )
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料乙.docx", b"b"),
    )
    store.handle_text(channel="wecom", sender_userid="user-1", text="开始审核")

    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="主文件是材料甲.docx",
    )

    assert decision.action == "run_multi"
    assert decision.primary_file_index == 0


def test_multi_file_review_inferrs_unique_non_attachment_regardless_of_order(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件1-名单.docx", b"a"),
    )
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("会议通知.docx", b"main"),
    )
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件2-反馈表.docx", b"b"),
    )

    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="开始审核",
    )

    assert decision.action == "run_multi"
    assert decision.primary_file_index == 1


def test_multi_file_review_requires_two_files(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.handle_text(channel="wecom", sender_userid="user-1", text="多文件审核")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("正文.docx", b"main"),
    )

    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="开始审核",
    )

    assert decision.action == "wait"
    assert "至少再发送 1 份" in decision.reply


def test_multi_file_review_persists_and_recovers_after_restart(tmp_path: Path):
    first_store = ReviewIntakeStore(storage_dir=tmp_path)
    first_store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    first_store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("正文.docx", b"main"),
    )
    first_store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件1.docx", b"attachment"),
    )

    restarted_store = ReviewIntakeStore(storage_dir=tmp_path)
    decision = restarted_store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="开始审核",
    )

    assert decision.action == "run_multi"
    assert [item.read_bytes() for item in decision.files] == [b"main", b"attachment"]


def test_primary_selection_prompt_recovers_after_restart(tmp_path: Path):
    first_store = ReviewIntakeStore(storage_dir=tmp_path)
    first_store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    first_store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料甲.docx", b"a"),
    )
    first_store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("材料乙.docx", b"b"),
    )
    asking = first_store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="开始审核",
    )

    restarted_store = ReviewIntakeStore(storage_dir=tmp_path)
    decision = restarted_store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="第2个是正文",
    )

    assert "无法确定哪一份是主文件" in asking.reply
    assert decision.action == "run_multi"
    assert decision.primary_file_index == 1
    assert [item.read_bytes() for item in decision.files] == [b"a", b"b"]


def test_review_intake_isolates_users(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("正文.docx", b"user-1"),
    )

    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-2",
        text="开始审核",
    )

    assert decision.action == "wait"
    assert "没有待联合审核" in decision.reply


def test_review_intake_cancel_removes_stored_files(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path)
    store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("正文.docx", b"main"),
    )
    stored_files = list(tmp_path.glob("**/files/*"))

    decision = store.handle_text(
        channel="wecom",
        sender_userid="user-1",
        text="取消审核",
    )

    assert stored_files
    assert decision.action == "wait"
    assert "已取消" in decision.reply
    assert not any(path.exists() for path in stored_files)


def test_review_intake_removes_expired_state_on_restart(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path, ttl_seconds=60)
    store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("正文.docx", b"expired"),
    )
    state_path = next(tmp_path.glob("*/state.json"))
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["updated_at"] = 0
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    ReviewIntakeStore(storage_dir=tmp_path, ttl_seconds=60)

    assert list(tmp_path.iterdir()) == []


def test_review_intake_enforces_file_count_and_total_size(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path, max_files=2, max_total_file_bytes=5)
    store.handle_text(channel="wecom", sender_userid="user-1", text="联合审核")
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("正文.docx", b"1234"),
    )

    decision = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("附件1.docx", b"12"),
    )

    assert decision.action == "wait"
    assert "总大小" in decision.reply


def test_default_file_count_limit_does_not_ask_user_to_start_review(tmp_path: Path):
    store = ReviewIntakeStore(storage_dir=tmp_path, max_files=1)
    store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("第一份.docx", b"first"),
    )

    decision = store.add_file(
        channel="wecom",
        sender_userid="user-1",
        file=_file("第二份.docx", b"second"),
    )

    assert decision.action == "wait"
    assert "未纳入" in decision.reply
    assert "开始审核" not in decision.reply
