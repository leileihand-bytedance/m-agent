from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.gateway.wecom import (  # noqa: E402
    extract_message_id,
    extract_text_message,
    format_text_reply,
    handle_text_frame_with_app,
    handle_text_frame,
)
from app.platform.models import PlatformResult  # noqa: E402


def test_extract_text_message_from_wecom_frame():
    frame = {
        "body": {
            "text": {"content": "帮我写直报：https://example.com"},
            "from": {"userid": "user-001"},
        }
    }

    message = extract_text_message(frame)

    assert message.sender_userid == "user-001"
    assert message.content == "帮我写直报：https://example.com"


def test_extract_message_id_uses_stable_wecom_precedence():
    assert extract_message_id({"msgid": " top-level "}) == "top-level"
    assert extract_message_id({"body": {"msgid": " body-id "}}) == "body-id"
    assert extract_message_id({"headers": {"req_id": " request-id "}}) == "request-id"


def test_extract_message_id_returns_empty_for_invalid_values():
    assert extract_message_id({"body": {"msgid": 123}}) == ""
    assert extract_message_id({"body": [], "headers": "invalid"}) == ""


def test_format_text_reply_for_clarification():
    result = PlatformResult(
        skill_id=None,
        output={},
        needs_clarification=True,
        message="你是想写直报还是审核文档？",
    )

    assert format_text_reply(result) == "你是想写直报还是审核文档？"


def test_format_text_reply_for_direct_report_output():
    result = PlatformResult(
        skill_id="direct_report",
        output={
            "title": "标题",
            "body": "正文",
            "sources": ["https://example.com"],
        },
        needs_clarification=False,
        message="",
    )

    assert format_text_reply(result) == "标题\n\n正文"


def test_format_text_reply_includes_revision_note_when_present():
    result = PlatformResult(
        skill_id="rewrite",
        output={
            "title": "",
            "body": "润色后的正文。",
            "revision_note": "调整了语气，并顺了一下句子。",
            "sources": [],
        },
        needs_clarification=False,
        message="",
    )

    assert format_text_reply(result) == "润色后的正文。\n\n修改说明：调整了语气，并顺了一下句子。"


def test_handle_text_frame_uses_runner_and_returns_reply():
    calls = []

    def runner(message: str) -> PlatformResult:
        calls.append(message)
        return PlatformResult(
            skill_id="direct_report",
            output={"title": "标题", "body": "正文", "sources": []},
            needs_clarification=False,
            message="",
        )

    frame = {
        "body": {
            "text": {"content": " 写直报：https://example.com "},
            "from": {"userid": "user-001"},
        }
    }

    reply = handle_text_frame(frame, runner)

    assert calls == ["写直报：https://example.com"]
    assert reply == "标题\n\n正文"


def test_handle_text_frame_rejects_empty_text():
    frame = {
        "body": {
            "text": {"content": "  "},
            "from": {"userid": "user-001"},
        }
    }

    reply = handle_text_frame(frame, lambda message: None)

    assert reply == "请发送要处理的文字、链接或文件。"


def test_handle_text_frame_with_app_passes_sender_to_platform_app():
    calls = []

    class FakeApp:
        def handle_text_message(self, *, channel: str, sender_userid: str, text: str) -> PlatformResult:
            calls.append((channel, sender_userid, text))
            return PlatformResult(
                skill_id="direct_report",
                output={"title": "标题", "body": "正文", "sources": []},
                needs_clarification=False,
                message="",
            )

    frame = {
        "body": {
            "text": {"content": "写直报：https://example.com"},
            "from": {"userid": "user-001"},
        }
    }

    reply = handle_text_frame_with_app(frame, FakeApp())

    assert calls == [("wecom", "user-001", "写直报：https://example.com")]
    assert reply == "标题\n\n正文"
