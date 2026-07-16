from pathlib import Path
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.app import PlatformApp
from app.platform.models import PlatformResult, RoutedRequest
from app.rewrite_bot.bot import handle_file_with_platform, handle_text_with_platform
from app.rewrite_bot.config import load_config
from app.rewrite_bot.intake import RewriteIntakeStore


class FakeWsClient:
    def __init__(self):
        self.stream_replies: list[tuple[object, str, str, bool]] = []

    async def reply_stream(self, frame, stream_id, message, finish):
        self.stream_replies.append((frame, stream_id, message, finish))


class FakePlatformApp:
    def __init__(self, *, active_revision: bool = False):
        self.calls: list[dict[str, str]] = []
        self.structured_calls: list[dict[str, str]] = []
        self.active_revision = active_revision

    def resolve_sender_name(self, sender_userid: str) -> str:
        return sender_userid

    def preview_text_route(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
    ) -> RoutedRequest:
        is_rewrite = "润色" in text or self.active_revision
        return RoutedRequest(
            skill_id="rewrite" if is_rewrite else None,
            confidence=1.0 if is_rewrite else 0.0,
            needs_clarification=not is_rewrite,
            message="",
            inputs={"text": text, "revision": self.active_revision},
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
    assert config.intake_dir == data_root / "runtime" / "intake" / "rewrite-bot"


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


def test_rewrite_bot_handles_text_with_rewrite_platform(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()

    asyncio.run(
        handle_text_with_platform(
            frame=_frame(),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=RewriteIntakeStore(storage_dir=tmp_path / "intake"),
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


def test_rewrite_bot_waits_for_direction_when_only_source_is_pasted(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()
    source_text = "县域经济是国民经济的重要组成部分，相关服务仍需持续完善。"
    intake_store = RewriteIntakeStore(storage_dir=tmp_path / "intake")

    asyncio.run(
        handle_text_with_platform(
            frame=_frame(source_text),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )

    assert platform_app.calls == []
    assert platform_app.structured_calls == []
    assert "你希望重点怎么调整" in ws_client.stream_replies[-1][2]


def test_rewrite_bot_runs_pending_source_after_user_provides_direction(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()
    intake_store = RewriteIntakeStore(storage_dir=tmp_path / "intake")
    source_text = "县域经济是国民经济的重要组成部分，相关服务仍需持续完善。"

    asyncio.run(
        handle_text_with_platform(
            frame=_frame(source_text),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )
    asyncio.run(
        handle_text_with_platform(
            frame=_frame("更正式一些，并梳理逻辑"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )

    assert platform_app.calls == []
    assert platform_app.structured_calls == [
        {
            "channel": "wecom-rewrite",
            "sender_userid": "user-001",
            "skill_id": "rewrite",
            "text": "更正式一些，并梳理逻辑",
            "material_text": source_text,
        }
    ]
    assert ws_client.stream_replies[-1][2] == "润色后的正文\n\n修改说明：调整了表达。"


def test_rewrite_bot_uses_default_direction_for_pending_source(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()
    intake_store = RewriteIntakeStore(storage_dir=tmp_path / "intake")

    asyncio.run(
        handle_text_with_platform(
            frame=_frame("需要持续完善工作机制，进一步提升服务质效。"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )
    asyncio.run(
        handle_text_with_platform(
            frame=_frame("按默认方式润色"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )

    assert platform_app.structured_calls[-1]["text"] == "按默认方式润色"


def test_rewrite_bot_keeps_style_words_inside_source_as_source_text(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()
    intake_store = RewriteIntakeStore(storage_dir=tmp_path / "intake")
    source_text = "现有工作机制仍需进一步优化，以持续提升服务质效。"

    asyncio.run(
        handle_text_with_platform(
            frame=_frame(source_text),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )
    asyncio.run(
        handle_text_with_platform(
            frame=_frame("更正式一些"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )

    assert platform_app.structured_calls[-1]["material_text"] == source_text


def test_rewrite_bot_restores_pending_source_after_restart(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()
    storage_dir = tmp_path / "intake"
    source_text = "需要持续完善工作机制，进一步提升服务质效。"

    asyncio.run(
        handle_text_with_platform(
            frame=_frame(source_text),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=RewriteIntakeStore(storage_dir=storage_dir),
        )
    )
    asyncio.run(
        handle_text_with_platform(
            frame=_frame("精简一些"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=RewriteIntakeStore(storage_dir=storage_dir),
        )
    )

    assert platform_app.structured_calls[-1]["material_text"] == source_text
    assert platform_app.structured_calls[-1]["text"] == "精简一些"


def test_rewrite_bot_keeps_active_revision_as_direct_conversation(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(active_revision=True)

    asyncio.run(
        handle_text_with_platform(
            frame=_frame("再正式一点"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=RewriteIntakeStore(storage_dir=tmp_path / "intake"),
        )
    )

    assert platform_app.calls[-1]["text"] == "再正式一点"
    assert platform_app.structured_calls == []


def test_rewrite_bot_cancels_pending_source(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()
    intake_store = RewriteIntakeStore(storage_dir=tmp_path / "intake")

    asyncio.run(
        handle_text_with_platform(
            frame=_frame("需要持续完善工作机制，进一步提升服务质效。"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )
    asyncio.run(
        handle_text_with_platform(
            frame=_frame("取消"),
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=lambda prefix: prefix,
            intake_store=intake_store,
        )
    )

    assert platform_app.calls == []
    assert platform_app.structured_calls == []
    assert "已取消" in ws_client.stream_replies[-1][2]


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
