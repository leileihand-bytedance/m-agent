"""审核 Bot 的持久化文件暂存与指令衔接测试。"""

from __future__ import annotations

import json
from pathlib import Path

from app.platform.models import UploadedFile
from app.review.intake import ReviewIntakeStore


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

    assert received.action == "bypass"
    assert decision.action == "run_format"
    assert decision.files[0].filename == "公文.docx"
    assert decision.files[0].read_bytes() == b"docx-content"


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
    assert second.action == "wait"
    assert decision.action == "run_multi"
    assert [item.filename for item in decision.files] == ["正文.docx", "附件1.docx"]
    assert [item.read_bytes() for item in decision.files] == [b"main", b"attachment"]
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
