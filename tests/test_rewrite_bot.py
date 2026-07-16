from pathlib import Path
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.app import PlatformApp
from app.platform.models import PlatformResult, RoutedRequest
from app.rewrite_bot.bot import handle_file_with_platform, handle_text_with_platform
from app.rewrite_bot.config import load_config


class FakeWsClient:
    def __init__(self):
        self.stream_replies: list[tuple[object, str, str, bool]] = []

    async def reply_stream(self, frame, stream_id, message, finish):
        self.stream_replies.append((frame, stream_id, message, finish))


class FakePlatformApp:
    def __init__(self):
        self.calls: list[dict[str, str]] = []
        self.structured_calls: list[dict[str, str]] = []

    def resolve_sender_name(self, sender_userid: str) -> str:
        return sender_userid

    def preview_text_route(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
    ) -> RoutedRequest:
        return RoutedRequest(
            skill_id="rewrite" if "润色" in text else None,
            confidence=1.0 if "润色" in text else 0.0,
            needs_clarification="润色" not in text,
            message="",
            inputs={"text": text},
        )

    def handle_text_message(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
        ack_message: str = "",
    ) -> PlatformResult:
        self.calls.append(
            {
                "channel": channel,
                "sender_userid": sender_userid,
                "text": text,
                "ack_message": ack_message,
            }
        )
        return PlatformResult(
            skill_id="rewrite",
            output={
                "title": "",
                "body": "润色后的正文",
                "revision_note": "调整了表达。",
            },
            needs_clarification=False,
            message="",
        )

    def handle_structured_request(
        self,
        *,
        channel: str,
        sender_userid: str,
        skill_id: str,
        text: str = "",
        material_text: str = "",
        urls=None,
        files=None,
    ) -> PlatformResult:
        self.structured_calls.append(
            {
                "channel": channel,
                "sender_userid": sender_userid,
                "skill_id": skill_id,
                "text": text,
                "material_text": material_text,
            }
        )
        return PlatformResult(
            skill_id="rewrite",
            output={"title": "", "body": "润色后的正文", "revision_note": "调整了表达。"},
            needs_clarification=False,
            message="",
        )


def _frame(content: str = "请润色这段：原文内容需要优化。") -> dict[str, object]:
    return {
        "body": {
            "from": {"userid": "user-001"},
            "text": {"content": content},
        }
    }


def test_rewrite_bot_loads_dedicated_credentials_and_runtime_paths(tmp_path):
    data_root = tmp_path / "M-Agent-Files"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "M_AGENT_REWRITE_BOT_ID=bot-id",
                "M_AGENT_REWRITE_BOT_SECRET=bot-secret",
                f"M_AGENT_DATA_DIR={data_root}",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path)

    assert config.bot_id == "bot-id"
    assert config.bot_secret == "bot-secret"
    assert config.platform_config.skill_allowlist == ("rewrite",)
    assert config.platform_config.jobs_dir == data_root / "tasks" / "writing" / "rewrite"
    assert config.platform_config.conversation_dir == data_root / "runtime" / "conversations" / "rewrite-bot"


def test_rewrite_bot_platform_only_routes_rewrite(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "M_AGENT_REWRITE_BOT_ID=bot-id",
                "M_AGENT_REWRITE_BOT_SECRET=bot-secret",
                f"M_AGENT_DATA_DIR={tmp_path / 'data'}",
                f"M_AGENT_SKILLS_DIR={Path('skills').resolve()}",
            ]
        ),
        encoding="utf-8",
    )
    platform_app = PlatformApp.from_config(load_config(env_path).platform_config)

    rewrite_route = platform_app.preview_text_route(
        channel="wecom-rewrite",
        sender_userid="user-001",
        text="帮我润色这段：原文内容需要优化。",
    )
    direct_report_route = platform_app.preview_text_route(
        channel="wecom-rewrite",
        sender_userid="user-001",
        text="帮我根据这份材料写一篇直报",
    )

    assert rewrite_route.skill_id == "rewrite"
    assert direct_report_route.skill_id is None
    assert direct_report_route.needs_clarification is True


def test_rewrite_bot_handles_text_with_rewrite_platform():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()

    asyncio.run(
        handle_text_with_platform(
            frame=_frame(),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
        )
    )

    assert platform_app.calls == [
        {
            "channel": "wecom-rewrite",
            "sender_userid": "user-001",
            "text": "请润色这段：原文内容需要优化。",
            "ack_message": "收到，正在按材料润色流程处理，请稍后……",
        }
    ]
    assert ws_client.stream_replies[0][2] == "收到，正在按材料润色流程处理，请稍后……"
    assert ws_client.stream_replies[-1][2] == "润色后的正文\n\n修改说明：调整了表达。"


def test_rewrite_bot_treats_directly_pasted_text_as_new_rewrite_task():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()
    source_text = "县域经济是国民经济的重要组成部分，相关服务仍需持续完善。"

    asyncio.run(
        handle_text_with_platform(
            frame=_frame(source_text),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
        )
    )

    assert platform_app.calls == []
    assert platform_app.structured_calls == [
        {
            "channel": "wecom-rewrite",
            "sender_userid": "user-001",
            "skill_id": "rewrite",
            "text": source_text,
            "material_text": "",
        }
    ]


def test_rewrite_bot_rejects_explicit_other_skill_request():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()

    asyncio.run(
        handle_text_with_platform(
            frame=_frame("帮我根据这份材料写一篇直报"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
        )
    )

    assert platform_app.calls == []
    assert platform_app.structured_calls == []
    assert "只提供材料润色" in ws_client.stream_replies[-1][2]


def test_rewrite_bot_rejects_files_without_running_skill():
    ws_client = FakeWsClient()

    asyncio.run(
        handle_file_with_platform(
            frame=_frame(""),
            ws_client=ws_client,
            req_id_factory=lambda prefix: prefix,
        )
    )

    assert "只支持直接粘贴文字" in ws_client.stream_replies[-1][2]


def test_rewrite_bot_rejects_links_without_running_skill():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()

    asyncio.run(
        handle_text_with_platform(
            frame=_frame("请润色这个链接：https://example.com/article"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
        )
    )

    assert platform_app.calls == []
    assert "只支持直接粘贴文字" in ws_client.stream_replies[-1][2]
