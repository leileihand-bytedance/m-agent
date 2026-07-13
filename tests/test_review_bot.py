"""存档机制 + 主流程逻辑的单元测试.

注:企微 Bot 联调(WSClient)需要真实凭证和网络,这里只测可测的逻辑:
  - .docx 后缀判断
  - 拒接非 .docx 文件
  - 存档到 data/reviews/<日期-序号>/
  - 大文件拒绝
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

# 让测试能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.review.main import (  # noqa: E402
    is_docx_filename,
    save_review,
    ReviewConfig,
    _next_review_index,
    _prepare_review_reply_file,
    build_user_review_reply,
    _split_text_into_paragraphs,
    extract_text_content,
    _review_text,
    _start_neican_review,
    _resolve_text_registration_reply,
    _build_enter_welcome_text,
    _is_followup_review_request_text,
    _resolve_instruction_only_text_reply,
    _resolve_smalltalk_text_reply,
    _configure_ws_client_timeouts,
    _build_delivery_failure_user_reply,
    _build_processing_failure_user_reply,
    _summarize_delivery_error,
    RecentSubmissionTracker,
)
from app.review.document_type import DocumentType  # noqa: E402
from app.review.reviewer import ReviewResult, Finding  # noqa: E402
from app.platform.user_registry import RegistrationFlow, UserRegistry  # noqa: E402


# ============================================================
# 测试 1: .docx 后缀判断
# ============================================================

def test_is_docx_filename_accept():
    assert is_docx_filename("report.docx") is True
    assert is_docx_filename("Report.DOCX") is True
    assert is_docx_filename("汇报.docx") is True
    print("✅ test_is_docx_filename_accept: .docx 全部接受")


def test_is_docx_filename_reject():
    assert is_docx_filename("report.pdf") is False
    assert is_docx_filename("report.txt") is False
    assert is_docx_filename("report.md") is False
    assert is_docx_filename("report") is False
    assert is_docx_filename(None) is False
    assert is_docx_filename("") is False
    print("✅ test_is_docx_filename_reject: 非 .docx 全部拒")


# ============================================================
# 测试 2: 存档到 data/reviews/
# ============================================================

def _make_fake_docx_bytes() -> bytes:
    """构造一个最小的 .docx 文件,返回字节."""
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>测试内容</w:t></w:r></w:p>
  </w:body>
</w:document>''')
    return buf.getvalue()


def test_save_review_creates_directory():
    """存档应创建标准目录结构."""
    import tempfile
    import shutil

    with tempfile.TemporaryDirectory() as tmpdir:
        reviews_dir = Path(tmpdir) / "reviews"
        reviews_dir.mkdir()  # 提前创建
        file_bytes = _make_fake_docx_bytes()

        # 构造一个假审核结果
        result = ReviewResult(
            findings=[
                Finding(
                    rule_id="dupe-char-check",
                    paragraph_index=0,
                    line_number=1,
                    original_text="我们要的的",
                    description="重复字",
                ),
            ],
            total_rules=10,
            passed_rules=9,
            filename="test.docx",
        )

        review_dir = save_review(
            reviews_dir=reviews_dir,
            file_bytes=file_bytes,
            original_filename="测试报告.docx",
            sender="user123",
            msgid="msg456",
            result=result,
            parsed_paragraphs=["测试内容", "第二段"],
        )

        # 验证目录结构
        assert review_dir.exists()
        assert (review_dir / "input").exists()
        assert (review_dir / "output").exists()
        assert (review_dir / "input" / "测试报告.docx").exists()
        assert (review_dir / "output" / "report.md").exists()
        assert (review_dir / "meta.json").exists()

        # 验证 report.md 内容
        report = (review_dir / "output" / "report.md").read_text(encoding="utf-8")
        assert "测试报告.docx" in report
        assert "dupe-char-check" in report

        # 验证 meta.md 内容
        meta = (review_dir / "meta.json").read_text(encoding="utf-8")
        assert "user123" in meta
        assert "msg456" in meta

        print(f"✅ test_save_review_creates_directory: 存档目录 {review_dir.name} 结构正确")


def test_save_review_increments_index():
    """同一日多次审核,序号应递增."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        reviews_dir = Path(tmpdir) / "reviews"
        reviews_dir.mkdir()

        # 存档 3 次
        dirs = []
        for i in range(3):
            result = ReviewResult(
                findings=[], total_rules=10, passed_rules=10, filename=f"f{i}.docx"
            )
            d = save_review(
                reviews_dir=reviews_dir,
                file_bytes=b"x",
                original_filename=f"f{i}.docx",
                sender="u",
                msgid=f"m{i}",
                result=result,
                parsed_paragraphs=[],
            )
            dirs.append(d)

        # 验证序号递增
        names = [d.name for d in dirs]
        # 名字形如 "20260613-001" "20260613-002" "20260613-003"
        suffixes = [n.split("-")[-1] for n in names]
        assert suffixes == ["001", "002", "003"], f"序号应为 001/002/003,实际 {suffixes}"
        print(f"✅ test_save_review_increments_index: 3 次存档序号 {suffixes}")


def test_save_review_sanitizes_filename():
    """特殊字符文件名应被清洗."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        reviews_dir = Path(tmpdir) / "reviews"
        reviews_dir.mkdir()
        result = ReviewResult(
            findings=[], total_rules=10, passed_rules=10, filename="x"
        )

        # 含特殊字符的文件名
        review_dir = save_review(
            reviews_dir=reviews_dir,
            file_bytes=b"x",
            original_filename="../../etc/passwd.docx",  # 路径穿越
            sender="u",
            msgid="m",
            result=result,
            parsed_paragraphs=[],
        )

        # 验证文件确实在 source 下,没有逃逸
        source_files = list((review_dir / "input").iterdir())
        assert len(source_files) == 1
        saved = source_files[0]
        assert "passwd" in saved.name
        # 不应包含 .. 或 /
        assert ".." not in saved.name
        assert "/" not in saved.name
        print(f"✅ test_save_review_sanitizes_filename: 危险文件名被清洗为 {saved.name}")


def test_save_review_supports_text_message_archive(tmp_path: Path):
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir()

    result = ReviewResult(
        findings=[],
        total_rules=10,
        passed_rules=10,
        filename="文字消息",
    )

    review_dir = save_review(
        reviews_dir=reviews_dir,
        file_bytes=None,
        original_filename="文字消息.txt",
        sender="user123",
        msgid="msg-text-001",
        result=result,
        parsed_paragraphs=["第一段文字。", "第二段文字。"],
        text_content="第一段文字。\n\n第二段文字。",
        doc_type=DocumentType.GENERAL,
    )

    text_source = review_dir / "input" / "文字消息.txt"
    assert text_source.exists()
    assert text_source.read_text(encoding="utf-8") == "第一段文字。\n\n第二段文字。"


def test_prepare_review_reply_file_builds_marked_doc_for_neican_findings(tmp_path: Path):
    from docx import Document

    source_doc = tmp_path / "source.docx"
    doc = Document()
    doc.add_paragraph("国务院政策例行吹风会介绍推进城市更新工作有关情况")
    doc.save(str(source_doc))

    review_dir = tmp_path / "review"
    source_dir = review_dir / "input"
    source_dir.mkdir(parents=True)
    saved_source = source_dir / "内参周报.docx"
    saved_source.write_bytes(source_doc.read_bytes())

    findings = [
        Finding(
            rule_id="content-mismatch",
            paragraph_index=0,
            line_number=1,
            original_text="国务院政策例行吹风会介绍推进城市更新工作有关情况",
            description="标题和正文讲的不是同一件事",
            target_text="国务院政策例行吹风会介绍推进城市更新工作有关情况",
        )
    ]

    reply_file = _prepare_review_reply_file(review_dir, "内参周报.docx", findings)

    assert reply_file is not None
    assert reply_file.exists()
    assert reply_file.name == "marked_内参周报.docx"


def test_prepare_review_reply_file_returns_source_when_no_findings(tmp_path: Path):
    source_doc = tmp_path / "source.docx"
    source_doc.write_bytes(b"fake-docx")

    review_dir = tmp_path / "review"
    source_dir = review_dir / "input"
    source_dir.mkdir(parents=True)
    saved_source = source_dir / "内参周报.docx"
    saved_source.write_bytes(source_doc.read_bytes())

    reply_file = _prepare_review_reply_file(review_dir, "内参周报.docx", [])

    assert reply_file is None


# ============================================================
# 测试 3: 配置加载
# ============================================================

def test_load_config_from_env_file():
    """从 .env 加载配置."""
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        env_path = Path(tmpdir) / ".env"
        env_path.write_text(
            "\n".join([
                "WECOM_REVIEW_BOT_ID=test_bot_id_12345",
                "WECOM_REVIEW_BOT_SECRET=test_secret_abcde",
                "M_AGENT_REVIEW_RULES=app/data/rules.md",
                "M_AGENT_REVIEWS_DIR=data/reviews",
                f"M_AGENT_DATA_DIR={Path(tmpdir) / 'M-Agent-Files'}",
                "M_AGENT_LOG_MAX_MB=8",
                "REVIEW_REPLY_ACK_TIMEOUT_SECONDS=45",
            ]),
            encoding="utf-8",
        )

        # 临时切到 tmpdir,因为 load_config 用的是 _ROOT
        # 这里直接调 load_config 传 env_path
        from app.review.main import load_config
        config = load_config(env_path)

        assert config.wecom_bot_id == "test_bot_id_12345"
        assert config.wecom_bot_secret == "test_secret_abcde"
        assert config.rules_path.name == "rules.md"
        assert config.reviews_dir.name == "reviews"
        assert config.user_registry_path == (
            Path(tmpdir) / "M-Agent-Files" / "runtime" / "users" / "review_users.yaml"
        ).resolve()
        assert config.max_file_size_mb == 10  # 默认值
        assert config.reply_ack_timeout_seconds == 45.0
        assert config.log_max_bytes == 8 * 1024 * 1024
        assert config.direct_admin_notifications is False
        print("✅ test_load_config_from_env_file: 配置加载正确")


def test_review_load_config_uses_single_external_data_root(tmp_path: Path):
    data_root = tmp_path / "M-Agent-Files"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "WECOM_REVIEW_BOT_ID=bot-id",
                "WECOM_REVIEW_BOT_SECRET=bot-secret",
                f"M_AGENT_DATA_DIR={data_root}",
            ]
        ),
        encoding="utf-8",
    )

    from app.review.main import load_config

    config = load_config(env_path)

    assert config.reviews_dir == data_root / "tasks" / "review"
    assert config.logs_dir == data_root / "runtime" / "logs"
    assert config.ops_events_dir == data_root / "runtime" / "ops" / "events"
    assert config.ops_heartbeat_dir == data_root / "runtime" / "ops" / "heartbeats"
    assert config.user_registry_path == data_root / "runtime" / "users" / "review_users.yaml"


def test_configure_ws_client_timeouts_overrides_sdk_reply_ack_timeout():
    class FakeManager:
        def __init__(self) -> None:
            self._reply_ack_timeout = 5.0

    class FakeClient:
        def __init__(self) -> None:
            self._ws_manager = FakeManager()

    client = FakeClient()

    _configure_ws_client_timeouts(client, reply_ack_timeout_seconds=30.0)

    assert client._ws_manager._reply_ack_timeout == 30.0


def test_build_delivery_failure_user_reply_hides_req_id_detail():
    exc = RuntimeError("Reply ack timeout (5.0s) for reqId: secret-req-id")

    reply = _build_delivery_failure_user_reply("标注文档", exc)

    assert "审核已经完成" in reply
    assert "标注文档发送失败" in reply
    assert "企业微信上传回执超时" in reply
    assert "管理员" in reply
    assert "secret-req-id" not in reply


def test_summarize_delivery_error_maps_upload_failure():
    exc = RuntimeError("Upload failed: 3 chunk(s) failed")

    assert _summarize_delivery_error(exc) == "企业微信文件上传失败"


def test_load_config_missing_required():
    """缺少必填字段应报错."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        env_path = Path(tmpdir) / ".env"
        env_path.write_text("# 空配置", encoding="utf-8")

        from app.review.main import load_config
        try:
            load_config(env_path)
            assert False, "应该抛 ValueError"
        except ValueError as e:
            assert "WECOM_REVIEW_BOT_ID" in str(e)
            print("✅ test_load_config_missing_required: 缺字段时正确报错")


# ============================================================
# 测试 4: 文字消息审核
# ============================================================

def test_split_text_into_paragraphs():
    """文本按空行/换行拆分段落,过滤空段."""
    assert _split_text_into_paragraphs("第一段。\n\n第二段。") == ["第一段。", "第二段。"]
    assert _split_text_into_paragraphs("A\nB\nC") == ["A", "B", "C"]
    assert _split_text_into_paragraphs("  \n\n  ") == []


def test_split_text_into_paragraphs_keeps_attachment_lines_after_blank_line():
    text = (
        "请填写附件1议案意见反馈表。\n\n"
        "附件 1：换届工作领导小组名单\n"
        "附件 7：议案意见反馈表"
    )

    assert _split_text_into_paragraphs(text) == [
        "请填写附件1议案意见反馈表。",
        "附件 1：换届工作领导小组名单",
        "附件 7：议案意见反馈表",
    ]


def test_extract_text_content():
    """从企微文本消息 frame 中提取 content."""
    frame = {
        "body": {
            "text": {"content": "  测试文字  "},
        }
    }
    assert extract_text_content(frame) == "测试文字"

    assert extract_text_content({}) is None
    assert extract_text_content({"body": {}}) is None
    assert extract_text_content({"body": {"text": {}}}) is None


def test_resolve_text_registration_reply_registers_valid_name(tmp_path: Path):
    registry = UserRegistry(tmp_path / "users.yaml")
    flow = RegistrationFlow(registry, require_registration=True)

    handled, reply = _resolve_text_registration_reply(flow, "new_user", "Tom")

    assert handled is True
    assert reply == (
        "你好，Tom：\n"
        "我可以帮你审内参、半月报，或者其他文字材料，直接发文字或docx给我就可以。"
        "另外请注意，涉及行内数据请务必脱敏哦。"
    )
    assert registry.get_name("new_user") == "Tom"


def test_resolve_text_registration_reply_rejects_invalid_name(tmp_path: Path):
    registry = UserRegistry(tmp_path / "users.yaml")
    flow = RegistrationFlow(registry, require_registration=True)

    handled, reply = _resolve_text_registration_reply(flow, "new_user", "Tom@123")

    assert handled is True
    assert "请发送一个有效的名字" in reply
    assert not registry.is_registered("new_user")


def test_resolve_text_registration_reply_prompts_again_for_common_greeting(tmp_path: Path):
    registry = UserRegistry(tmp_path / "users.yaml")
    flow = RegistrationFlow(registry, require_registration=True)

    handled, reply = _resolve_text_registration_reply(flow, "new_user", "你好")

    assert handled is True
    assert reply == flow.ask_name_message()
    assert not registry.is_registered("new_user")


def test_resolve_text_registration_reply_prompts_again_for_review_request_text(tmp_path: Path):
    registry = UserRegistry(tmp_path / "users.yaml")
    flow = RegistrationFlow(registry, require_registration=True)

    handled, reply = _resolve_text_registration_reply(flow, "new_user", "我要审个材料")

    assert handled is True
    assert reply == flow.ask_name_message()
    assert not registry.is_registered("new_user")


def test_build_enter_welcome_text_asks_name_for_new_user(tmp_path: Path):
    registry = UserRegistry(tmp_path / "users.yaml")
    flow = RegistrationFlow(registry, require_registration=True)

    reply = _build_enter_welcome_text(flow, "new_user")

    assert reply == (
        "欢迎使用智能审核BOT！\n"
        "这是你第一次使用，先互相认识一下吧，请先告诉我你的英文名（例如：Jack）。"
    )


def test_build_enter_welcome_text_keeps_regular_welcome_for_registered_user(tmp_path: Path):
    registry = UserRegistry(tmp_path / "users.yaml")
    registry.register("old_user", "Alice")
    flow = RegistrationFlow(registry, require_registration=True)

    reply = _build_enter_welcome_text(flow, "old_user")

    assert reply == "你好，需要我帮你审核什么呢？请直接发送 .docx 文档或直接发送文字,我会认真审核。"


def test_is_followup_review_request_text_matches_short_prompt():
    assert _is_followup_review_request_text("帮我审一下") is True
    assert _is_followup_review_request_text("帮我看看有无问题") is True
    assert _is_followup_review_request_text("请审核一下。") is True
    assert _is_followup_review_request_text("帮我审一下这个材料") is True


def test_is_followup_review_request_text_does_not_match_real_content():
    assert _is_followup_review_request_text("这是需要审核的正文第一段内容") is False
    assert _is_followup_review_request_text("今天召开专题会议，研究下半年工作安排。") is False


def test_recent_submission_tracker_ignores_followup_prompt_after_file():
    tracker = RecentSubmissionTracker(ttl_seconds=90)
    tracker.remember("u1", "file", now=100.0)

    assert tracker.should_ignore_text_review("u1", "帮我审一下", now=120.0) is True


def test_recent_submission_tracker_does_not_ignore_followup_without_recent_submission():
    tracker = RecentSubmissionTracker(ttl_seconds=90)

    assert tracker.should_ignore_text_review("u1", "帮我审一下", now=120.0) is False


def test_recent_submission_tracker_does_not_ignore_real_text_after_file():
    tracker = RecentSubmissionTracker(ttl_seconds=90)
    tracker.remember("u1", "file", now=100.0)

    assert tracker.should_ignore_text_review("u1", "第一段：今天召开专题会议。", now=120.0) is False


def test_resolve_instruction_only_text_reply_prompts_user_to_send_material_first():
    tracker = RecentSubmissionTracker(ttl_seconds=90)

    reply = _resolve_instruction_only_text_reply(tracker, "u1", "帮我审一下这个材料", now=100.0)

    assert reply == "收到，请把需要审核的文字或.docx发给我，我来帮你看。"


def test_resolve_instruction_only_text_reply_treats_followup_as_continue_when_recent_submission_exists():
    tracker = RecentSubmissionTracker(ttl_seconds=90)
    tracker.remember("u1", "file", now=100.0)

    reply = _resolve_instruction_only_text_reply(tracker, "u1", "帮我审一下这个材料", now=120.0)

    assert reply == "收到，我会按你刚发的内容继续审核，请稍等……"


def test_resolve_smalltalk_text_reply_handles_ack_text():
    assert _resolve_smalltalk_text_reply("好的") == "收到。"
    assert _resolve_smalltalk_text_reply("ok") == "收到。"


def test_resolve_smalltalk_text_reply_handles_thanks_text():
    assert _resolve_smalltalk_text_reply("谢谢") == "不客气。"
    assert _resolve_smalltalk_text_reply("谢谢啦") == "不客气。"


def test_resolve_smalltalk_text_reply_ignores_real_content():
    assert _resolve_smalltalk_text_reply("这是需要审核的正文第一段内容") is None


def test_review_text_mock_llm(monkeypatch):
    """文字审核 mock LLM 流程."""
    import asyncio

    class _FakeBlock:
        def __init__(self, text: str):
            self.text = text

    class _FakeMessage:
        def __init__(self, text: str):
            self.content = [_FakeBlock(text)]

    class _FakeMessages:
        def __init__(self, text: str):
            self._text = text

        def create(self, **_: object) -> _FakeMessage:
            return _FakeMessage(self._text)

    class _FakeClient:
        def __init__(self, text: str):
            self.messages = _FakeMessages(text)

    monkeypatch.setattr(
        "app.review.general_reviewer.build_anthropic_client",
        lambda: (_FakeClient('{"issues": []}'), "fake-model"),
    )

    config = ReviewConfig(
        wecom_bot_id="x",
        wecom_bot_secret="x",
        rules_path=Path("app/data/rules.md"),
        reviews_dir=Path("data/reviews"),
        logs_dir=Path("data/logs"),
        admin_user_id="",
        admin_name="",
        notification_cooldown=300,
        direct_admin_notifications=False,
        require_registration=False,
    )

    result = asyncio.run(_review_text("第一段。\n\n第二段。", config))
    assert "文字消息" in result
    assert "未发现低级错误" in result


def test_build_user_review_reply_returns_none_when_findings_exist():
    result = ReviewResult(
        findings=[
            Finding(
                rule_id="halfmonthly-leader-title",
                paragraph_index=1,
                line_number=2,
                original_text="黄黎明,行长",
                description="当前信息已采用党内职务口径，黄黎明应补充'党委副书记'",
                target_text="黄黎明",
            )
        ],
        total_rules=12,
        passed_rules=11,
        filename="半月报.docx",
    )

    reply = build_user_review_reply(result, "半月报.docx", doc_type=DocumentType.HALF_MONTHLY)

    assert reply is None


def test_build_user_review_reply_returns_pass_message_when_no_findings():
    result = ReviewResult(
        findings=[],
        total_rules=12,
        passed_rules=12,
        filename="内参周报.docx",
    )

    reply = build_user_review_reply(result, "内参周报.docx", doc_type=DocumentType.NEI_CAN)

    assert reply is not None
    assert "没有发现问题" in reply


def test_run_neican_review_starts_phase2_before_phase1_finishes():
    """内参审核应在等待第一阶段结果时提前启动第二阶段。"""
    import asyncio

    phase2_started = asyncio.Event()

    async def fake_phase1(paragraphs, rules_text, filename, file_path=None):
        await asyncio.wait_for(phase2_started.wait(), timeout=0.1)
        return ReviewResult(
            findings=[],
            total_rules=10,
            passed_rules=10,
            filename=filename,
        )

    async def fake_phase2(paragraphs, rules_text, filename):
        phase2_started.set()
        await asyncio.sleep(0)
        return ReviewResult(
            findings=[],
            total_rules=5,
            passed_rules=5,
            filename=filename,
        )

    async def _run():
        phase1_result, phase2_task, timings = await _start_neican_review(
            phase1_runner=fake_phase1,
            phase2_runner=fake_phase2,
            paragraphs=["标题", "正文"],
            rules_text="",
            filename="demo.docx",
            file_path=None,
        )
        phase2_result, phase2_ms = await phase2_task
        return phase1_result, phase2_result, timings, phase2_ms

    phase1_result, phase2_result, timings, phase2_ms = asyncio.run(_run())

    assert phase1_result.total_rules == 10
    assert phase2_result.total_rules == 5
    assert timings["phase1_ms"] >= 0
    assert timings["wall_start"] > 0
    assert phase2_ms >= 0


def test_run_neican_review_cancels_phase2_when_phase1_fails():
    """第一阶段失败时，应取消后台第二阶段任务，避免继续占用模型调用。"""
    import asyncio

    phase2_cancelled = asyncio.Event()

    async def fake_phase1(paragraphs, rules_text, filename, file_path=None):
        raise RuntimeError("phase1 boom")

    async def fake_phase2(paragraphs, rules_text, filename):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            phase2_cancelled.set()
            raise

    try:
        asyncio.run(
            _start_neican_review(
                phase1_runner=fake_phase1,
                phase2_runner=fake_phase2,
                paragraphs=["标题", "正文"],
                rules_text="",
                filename="demo.docx",
                file_path=None,
            )
        )
        assert False, "应抛出第一阶段异常"
    except RuntimeError as exc:
        assert str(exc) == "phase1 boom"

    assert phase2_cancelled.is_set()


def test_processing_failure_reply_hides_internal_exception_details():
    reply = _build_processing_failure_user_reply(
        "文件解析",
        RuntimeError("failed at /private/tmp/job-001 req_id=secret-request"),
    )

    assert "文件解析失败" in reply
    assert "已经提醒管理员" in reply
    assert "/private/tmp" not in reply
    assert "req_id" not in reply


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("智能审核 Bot - 存档与配置测试")
    print("=" * 60)
    print()

    tests = [
        test_is_docx_filename_accept,
        test_is_docx_filename_reject,
        test_save_review_creates_directory,
        test_save_review_increments_index,
        test_save_review_sanitizes_filename,
        test_load_config_from_env_file,
        test_load_config_missing_required,
        test_split_text_into_paragraphs,
        test_extract_text_content,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"❌ {t.__name__}: FAIL")
            print(f"   {e}")
            failed += 1
        except Exception as e:
            print(f"❌ {t.__name__}: ERROR")
            print(f"   {type(e).__name__}: {e}")
            failed += 1

    print()
    print("=" * 60)
    print(f"测试结果: {passed} 通过, {failed} 失败,共 {len(tests)} 个")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
