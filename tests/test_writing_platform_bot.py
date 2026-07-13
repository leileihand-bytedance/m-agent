from pathlib import Path
import asyncio
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import app.writing.config as writing_config  # noqa: E402
import app.writing.bot as writing_bot  # noqa: E402
from app.platform.config import PlatformConfig  # noqa: E402
from app.platform.intent import ConversationIntent  # noqa: E402
from app.platform.models import PlatformResult, RoutedRequest  # noqa: E402
from app.platform.ops.events import OpsEventLogger, read_ops_events  # noqa: E402
from app.writing.bot import build_platform_config, handle_file_with_platform, handle_text_with_platform, mask_config_value  # noqa: E402
from app.writing.config import WritingBotConfig, load_config  # noqa: E402
from app.writing.intake import WritingIntakeStore  # noqa: E402


class FakeWsClient:
    def __init__(self):
        self.stream_replies = []
        self.sent_messages = []

    async def reply_stream(self, frame, stream_id, message, finish):
        self.stream_replies.append((frame, stream_id, message, finish))

    async def send_message(self, sender, payload):
        self.sent_messages.append((sender, payload))

    async def download_file(self, url, aes_key):
        return {"buffer": b"fake docx", "filename": "material.docx"}


class FailingFinalReplyWsClient(FakeWsClient):
    async def reply_stream(self, frame, stream_id, message, finish):
        if stream_id.startswith("writing-result"):
            raise RuntimeError("reply failed")
        await super().reply_stream(frame, stream_id, message, finish)


class FakePlatformApp:
    def __init__(self, result=None, error=None, intent=ConversationIntent.NEW_TASK, skill_id="direct_report"):
        self.calls = []
        self.structured_calls = []
        self.intent_calls = []
        self.preview_calls = []
        self._intent = intent
        self._skill_id = skill_id
        self._result = result or PlatformResult(
            skill_id=skill_id,
            output={"title": "标题", "body": "正文", "sources": ["https://example.com"]},
            needs_clarification=False,
            message="",
        )
        self._error = error

    def handle_text_message(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
        ack_message: str = "",
    ) -> PlatformResult:
        self.calls.append((channel, sender_userid, text))
        if self._error:
            raise self._error
        return self._result

    def handle_structured_request(
        self,
        *,
        channel: str,
        sender_userid: str,
        skill_id: str,
        text: str = "",
        material_text: str = "",
        urls: list[str] | None = None,
        files: list | None = None,
    ) -> PlatformResult:
        self.structured_calls.append(
            {
                "channel": channel,
                "sender_userid": sender_userid,
                "skill_id": skill_id,
                "text": text,
                "material_text": material_text,
                "urls": list(urls or []),
                "files": list(files or []),
            }
        )
        if self._error:
            raise self._error
        return self._result

    def classify_text_intent(self, *, channel: str, sender_userid: str, text: str) -> ConversationIntent:
        self.intent_calls.append((channel, sender_userid, text))
        return self._intent

    def preview_text_route(self, *, channel: str, sender_userid: str, text: str) -> RoutedRequest:
        self.preview_calls.append((channel, sender_userid, text))
        return RoutedRequest(
            skill_id=self._skill_id,
            confidence=1.0,
            needs_clarification=self._intent == ConversationIntent.CLARIFY,
            message="",
            inputs={"revision": self._intent == ConversationIntent.REVISE_PREVIOUS},
        )

    def resolve_sender_name(self, sender_userid: str) -> str:
        return {"user-001": "test-user"}.get(sender_userid, sender_userid)


class LoopCheckingPlatformApp:
    def __init__(self):
        self.ran_outside_event_loop = False

    def handle_text_message(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
        ack_message: str = "",
    ) -> PlatformResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.ran_outside_event_loop = True
        return PlatformResult(
            skill_id="direct_report",
            output={"title": "标题", "body": "正文", "sources": []},
            needs_clarification=False,
            message="",
        )


def _frame(content: str):
    return {
        "body": {
            "text": {"content": content},
            "from": {"userid": "user-001"},
        }
    }


def _file_frame(filename: str = "material.docx", *, size: int | None = None):
    file_body = {
        "download_url": "https://wecom.example.com/file",
        "aes_key": "aes-key",
        "filename": filename,
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    if size is not None:
        file_body["size"] = size
    return {
        "body": {
            "file": file_body,
            "from": {"userid": "user-001"},
        }
    }


LONG_MATERIAL = (
    "近日，某银行围绕普惠金融和科技金融服务实体经济推出专项举措，"
    "通过数字化手段提升小微企业融资效率，并同步加强风险识别和客户服务。"
)


def test_build_platform_config_uses_writing_bot_runtime_settings(tmp_path):
    config = WritingBotConfig(
        wecom_bot_id="bot-id",
        wecom_bot_secret="secret",
        model_name="MiniMax-M2.7",
        anthropic_api_key="api-key",
        anthropic_base_url="https://example.com/anthropic",
        skills_dir=Path("skills"),
        jobs_dir=tmp_path / "jobs",
        model_max_tokens=6144,
        direct_report_critic_mode="advisory",
        chat_log_enabled=False,
        chat_log_dir=tmp_path / "chat_logs",
        ops_events_dir=tmp_path / "ops_events",
        access_policy_path=tmp_path / "policy.yaml",
        user_registry_path=tmp_path / "users.yaml",
    )

    platform_config = build_platform_config(config)

    assert isinstance(platform_config, PlatformConfig)
    assert platform_config.model_name == "MiniMax-M2.7"
    assert platform_config.anthropic_api_key == "api-key"
    assert platform_config.model_max_tokens == 6144
    assert platform_config.direct_report_critic_mode == "advisory"
    assert platform_config.chat_log_enabled is False
    assert platform_config.chat_log_dir == tmp_path / "chat_logs"
    assert platform_config.user_registry_path == tmp_path / "users.yaml"
    assert platform_config.skills_dir == Path("skills")
    assert platform_config.jobs_dir == tmp_path / "jobs"
    assert platform_config.policy_db_path.name == "policies.sqlite3"
    assert platform_config.bank_db_path.name == "bank.sqlite3"
    assert platform_config.access_policy_path == tmp_path / "policy.yaml"


def test_writing_load_config_prefers_model_api_settings_over_legacy_anthropic(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "WRITING_BOT_ID=bot-id",
                "WRITING_BOT_SECRET=bot-secret",
                "MODEL_NAME=deepseek-v4-flash",
                "MODEL_BASE_URL=https://api.deepseek.com/v1",
                "MODEL_API_KEY=model-key",
                "M_AGENT_MODEL_MAX_TOKENS=6144",
                "M_AGENT_DIRECT_REPORT_CRITIC_MODE=off",
                "M_AGENT_CHAT_LOG_ENABLED=false",
                "M_AGENT_CHAT_LOG_DIR=custom-writing-chat-logs",
                "M_AGENT_OPS_EVENTS_DIR=custom-ops-events",
                "M_AGENT_USER_REGISTRY_PATH=custom-users.yaml",
                "M_AGENT_WRITING_INTAKE_TTL=900",
                "ANTHROPIC_BASE_URL=https://legacy.example.com/anthropic",
                "ANTHROPIC_API_KEY=legacy-key",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path)

    assert config.model_name == "deepseek-v4-flash"
    assert config.anthropic_base_url == "https://api.deepseek.com/v1"
    assert config.anthropic_api_key == "model-key"
    assert config.model_max_tokens == 6144
    assert config.direct_report_critic_mode == "off"
    assert config.chat_log_enabled is False
    assert config.chat_log_dir == Path(__file__).resolve().parent.parent / "custom-writing-chat-logs"
    assert config.ops_events_dir == Path(__file__).resolve().parent.parent / "custom-ops-events"
    assert config.user_registry_path == Path(__file__).resolve().parent.parent / "custom-users.yaml"
    assert config.intake_ttl_seconds == 900


def test_writing_load_config_defaults_to_local_only_portal(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "WRITING_BOT_ID=bot-id",
                "WRITING_BOT_SECRET=bot-secret",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(env_path)

    assert config.portal_host == "127.0.0.1"
    assert config.portal_port == 8790
    assert config.portal_base_url == "http://127.0.0.1:8790"


def test_writing_load_config_prefers_explicit_portal_base_url_over_detected_host(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "WRITING_BOT_ID=bot-id",
                "WRITING_BOT_SECRET=bot-secret",
                "M_AGENT_PORTAL_HOST=0.0.0.0",
                "M_AGENT_PORTAL_PORT=9000",
                "M_AGENT_PORTAL_BASE_URL=https://writer.example.com",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(writing_config, "_detect_portal_public_host", lambda: "192.168.10.55")

    config = load_config(env_path)

    assert config.portal_host == "0.0.0.0"
    assert config.portal_port == 9000
    assert config.portal_base_url == "https://writer.example.com"


def test_detect_portal_public_host_falls_back_to_ifconfig_output(monkeypatch):
    class _BrokenSocket:
        def __enter__(self):
            raise PermissionError("blocked")

        def __exit__(self, exc_type, exc, tb):
            return False

    class _CompletedProcess:
        stdout = "\n".join(
            [
                "lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384",
                "\tinet 127.0.0.1 netmask 0xff000000",
                "en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500",
                "\tinet 10.65.48.42 netmask 0xfffffc00 broadcast 10.65.51.255",
            ]
        )

    monkeypatch.setattr(writing_config.socket, "socket", lambda *args, **kwargs: _BrokenSocket())
    monkeypatch.setattr(writing_config.socket, "getaddrinfo", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no host")))
    monkeypatch.setattr(writing_config.subprocess, "run", lambda *args, **kwargs: _CompletedProcess())

    assert writing_config._detect_portal_public_host() == "10.65.48.42"


def test_detect_portal_public_host_skips_reserved_candidates(monkeypatch):
    class _SocketWithReservedIP:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def connect(self, address):
            return None

        def getsockname(self):
            return ("240.0.0.2", 8790)

    monkeypatch.setattr(writing_config.socket, "socket", lambda *args, **kwargs: _SocketWithReservedIP())
    monkeypatch.setattr(
        writing_config.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (writing_config.socket.AF_INET, 0, 0, "", ("240.0.0.2", 0)),
            (writing_config.socket.AF_INET, 0, 0, "", ("10.65.48.42", 0)),
        ],
    )

    assert writing_config._detect_portal_public_host() == "10.65.48.42"


def test_mask_config_value_hides_most_of_sensitive_value():
    assert mask_config_value("abcdef123456") == "abcd...3456"
    assert mask_config_value("short") == "***"


@pytest.mark.anyio
async def test_handle_text_with_platform_sends_ack_then_final_reply():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()

    await handle_text_with_platform(
        frame=_frame("写直报：https://example.com"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
    )

    assert platform_app.calls == [("wecom", "user-001", "写直报：https://example.com")]
    assert ws_client.stream_replies[0][1] == "writing-platform-001"
    assert ws_client.stream_replies[0][2] == "收到，正在按直报写作流程处理，请稍后……"
    assert ws_client.stream_replies[1][1] == "writing-result-001"
    assert ws_client.stream_replies[1][2] == "标题\n\n正文"
    assert ws_client.sent_messages == []


@pytest.mark.anyio
async def test_handle_text_with_platform_uses_brief_ack_for_writer1():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")

    await handle_text_with_platform(
        frame=_frame("写简报：https://example.com"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
    )

    assert platform_app.calls == [("wecom", "user-001", "写简报：https://example.com")]
    assert ws_client.stream_replies[0][2] == "收到，正在按简报写作流程处理，请稍后……"


@pytest.mark.anyio
async def test_handle_text_with_platform_uses_multi_brief_ack_for_writer2():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer2")

    await handle_text_with_platform(
        frame=_frame("写简报：https://example.com/a https://example.com/b"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
    )

    assert platform_app.calls == [("wecom", "user-001", "写简报：https://example.com/a https://example.com/b")]
    assert ws_client.stream_replies[0][2] == "收到，正在按多素材简报写作流程处理，请稍后……"


@pytest.mark.anyio
async def test_handle_text_with_platform_collects_material_before_intent():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")
    intake_store = WritingIntakeStore()

    await handle_text_with_platform(
        frame=_frame(LONG_MATERIAL),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )

    assert platform_app.calls == []
    assert platform_app.structured_calls == []
    assert "你希望我怎么处理" in ws_client.stream_replies[0][2]

    await handle_text_with_platform(
        frame=_frame("写简报"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-002",
        intake_store=intake_store,
    )

    assert platform_app.calls == []
    assert len(platform_app.structured_calls) == 1
    structured_call = platform_app.structured_calls[0]
    assert structured_call["skill_id"] == "writer1"
    assert "普惠金融" in structured_call["material_text"]
    assert ws_client.stream_replies[-2][2] == "收到，正在按简报写作流程处理，请稍后……"
    assert ws_client.stream_replies[-1][2] == "标题\n\n正文"


@pytest.mark.anyio
async def test_handle_text_with_platform_collects_intent_before_material_until_start():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")
    intake_store = WritingIntakeStore()

    await handle_text_with_platform(
        frame=_frame("帮我写简报"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )
    await handle_text_with_platform(
        frame=_frame(LONG_MATERIAL),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-002",
        intake_store=intake_store,
    )

    assert platform_app.calls == []
    assert platform_app.structured_calls == []
    assert "继续发送材料" in ws_client.stream_replies[-1][2]

    await handle_text_with_platform(
        frame=_frame("开始写"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-003",
        intake_store=intake_store,
    )

    assert len(platform_app.structured_calls) == 1
    assert platform_app.structured_calls[0]["skill_id"] == "writer1"
    assert "普惠金融" in platform_app.structured_calls[0]["material_text"]


def test_rewrite_session_treats_long_text_with_intent_words_as_material():
    intake_store = WritingIntakeStore()

    first = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="帮我润色",
    )
    material = (
        "本方案将优化客户服务流程，并修改原有操作步骤。"
        "项目团队已经完成需求梳理、风险评估和资源准备，后续将按计划推进实施并跟踪效果。"
    )
    second = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text=material,
    )
    third = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="开始写",
    )

    assert first.action == "wait"
    assert second.action == "wait"
    assert "已收到第 1 份材料" in second.reply
    assert third.action == "run"
    assert third.skill_id == "rewrite"
    assert third.material_text == material


@pytest.mark.anyio
async def test_handle_text_with_platform_uses_writer2_for_collected_multi_materials():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer2")
    intake_store = WritingIntakeStore()

    await handle_text_with_platform(
        frame=_frame("帮我写简报"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )
    await handle_text_with_platform(
        frame=_frame("https://example.com/a"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-002",
        intake_store=intake_store,
    )
    await handle_text_with_platform(
        frame=_frame("https://example.com/b"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-003",
        intake_store=intake_store,
    )
    await handle_text_with_platform(
        frame=_frame("开始写"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-004",
        intake_store=intake_store,
    )

    assert len(platform_app.structured_calls) == 1
    structured_call = platform_app.structured_calls[0]
    assert structured_call["skill_id"] == "writer2"
    assert structured_call["urls"] == ["https://example.com/a", "https://example.com/b"]
    assert ws_client.stream_replies[-2][2] == "收到，正在按多素材简报写作流程处理，请稍后……"


@pytest.mark.anyio
async def test_handle_text_with_platform_bypasses_intake_when_intent_and_material_are_same_message():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")
    intake_store = WritingIntakeStore()

    await handle_text_with_platform(
        frame=_frame("写简报：https://example.com"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )

    assert platform_app.calls == [("wecom", "user-001", "写简报：https://example.com")]
    assert platform_app.structured_calls == []
    assert ws_client.stream_replies[0][2] == "收到，正在按简报写作流程处理，请稍后……"


@pytest.mark.anyio
async def test_handle_file_with_platform_collects_file_before_intent():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")
    intake_store = WritingIntakeStore()

    await handle_file_with_platform(
        frame=_file_frame(),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )

    assert platform_app.structured_calls == []
    assert "你希望我怎么处理" in ws_client.stream_replies[-1][2]

    await handle_text_with_platform(
        frame=_frame("写简报"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-002",
        intake_store=intake_store,
    )

    assert len(platform_app.structured_calls) == 1
    structured_call = platform_app.structured_calls[0]
    assert structured_call["skill_id"] == "writer1"
    assert structured_call["files"][0].filename == "material.docx"


@pytest.mark.anyio
async def test_handle_file_with_platform_does_not_advertise_unsupported_rewrite():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")
    intake_store = WritingIntakeStore()

    await handle_file_with_platform(
        frame=_file_frame(),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )

    reply = ws_client.stream_replies[-1][2]
    assert "写简报" in reply
    assert "写直报" in reply
    assert "改写" not in reply


@pytest.mark.anyio
async def test_handle_file_with_platform_does_not_route_file_only_material_to_rewrite():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="rewrite")
    intake_store = WritingIntakeStore()

    await handle_file_with_platform(
        frame=_file_frame(),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )
    await handle_text_with_platform(
        frame=_frame("改写"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-002",
        intake_store=intake_store,
    )

    assert platform_app.structured_calls == []
    assert "直接粘贴" in ws_client.stream_replies[-1][2]


@pytest.mark.anyio
async def test_handle_file_with_platform_rejects_unsupported_suffix_before_download():
    class NoDownloadWsClient(FakeWsClient):
        async def download_file(self, url, aes_key):
            raise AssertionError("unsupported file must not be downloaded")

    ws_client = NoDownloadWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")

    await handle_file_with_platform(
        frame=_file_frame("material.xlsx"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=WritingIntakeStore(),
    )

    assert platform_app.structured_calls == []
    assert "Word(.docx)" in ws_client.stream_replies[-1][2]


@pytest.mark.anyio
async def test_handle_file_with_platform_rejects_oversized_download(monkeypatch):
    class LargeDownloadWsClient(FakeWsClient):
        async def download_file(self, url, aes_key):
            return {"buffer": b"12345", "filename": "material.docx"}

    monkeypatch.setattr(writing_bot, "MAX_WRITING_FILE_BYTES", 4)
    ws_client = LargeDownloadWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")

    await handle_file_with_platform(
        frame=_file_frame(),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=WritingIntakeStore(),
    )

    assert platform_app.structured_calls == []
    assert "文件过大" in ws_client.stream_replies[-1][2]


@pytest.mark.anyio
async def test_handle_file_with_platform_rejects_announced_size_before_download(monkeypatch):
    class NoDownloadWsClient(FakeWsClient):
        async def download_file(self, url, aes_key):
            raise AssertionError("announced oversized file must not be downloaded")

    monkeypatch.setattr(writing_bot, "MAX_WRITING_FILE_BYTES", 4)
    ws_client = NoDownloadWsClient()

    await handle_file_with_platform(
        frame=_file_frame(size=5),
        ws_client=ws_client,
        platform_app=FakePlatformApp(skill_id="writer1"),
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=WritingIntakeStore(),
    )

    assert "文件过大" in ws_client.stream_replies[-1][2]


def test_writing_intake_limits_file_count_and_total_bytes():
    store = WritingIntakeStore(max_files=1, max_total_file_bytes=4)
    first = store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=writing_bot.UploadedFile(filename="one.docx", content=b"1234"),
    )
    second = store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=writing_bot.UploadedFile(filename="two.docx", content=b"1"),
    )

    assert "已收到文件" in first.reply
    assert "最多接收 1 个文件" in second.reply


@pytest.mark.anyio
async def test_handle_text_with_platform_uses_lighter_ack_for_revision():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(intent=ConversationIntent.REVISE_PREVIOUS)

    await handle_text_with_platform(
        frame=_frame("还是太像新闻稿，开头有点虚"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
    )

    assert platform_app.preview_calls == [("wecom", "user-001", "还是太像新闻稿，开头有点虚")]
    assert ws_client.stream_replies[0][1] == "writing-platform-001"
    assert ws_client.stream_replies[0][2] == "收到，我沿着上一稿继续改。"
    assert "正在按直报写作流程" not in ws_client.stream_replies[0][2]


@pytest.mark.anyio
async def test_handle_text_with_platform_rejects_empty_text_without_calling_app():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp()

    await handle_text_with_platform(
        frame=_frame("   "),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
    )

    assert platform_app.calls == []
    assert ws_client.stream_replies[0][2] == "请发送网页链接或文字素材，我会根据需求选择对应写作流程处理。"
    assert ws_client.sent_messages == []


@pytest.mark.anyio
async def test_handle_text_with_platform_returns_safe_error_message():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(error=RuntimeError("secret stack detail"))

    await handle_text_with_platform(
        frame=_frame("写直报：https://example.com"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
    )

    assert "secret stack detail" not in ws_client.stream_replies[1][2]


@pytest.mark.anyio
async def test_handle_text_with_platform_records_ops_event_on_processing_error(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(error=RuntimeError("model timeout"), skill_id="writer1")
    ops_logger = OpsEventLogger(tmp_path / "ops_events")

    await handle_text_with_platform(
        frame=_frame("写简报：https://example.com"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        ops_event_logger=ops_logger,
    )

    events = read_ops_events(tmp_path / "ops_events", __import__("datetime").date.today())
    assert len(events) == 1
    assert events[0].source == "writing_bot"
    assert events[0].severity == "error"
    assert events[0].subject == "写作处理失败"
    assert events[0].sender_name == "test-user"
    assert events[0].skill_id == "writer1"
    assert "model timeout" in events[0].detail


@pytest.mark.anyio
async def test_handle_text_with_platform_records_ops_event_on_link_read_clarification(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(
        result=PlatformResult(
            skill_id="writer2",
            output={},
            needs_clarification=True,
            message="有链接读取失败，请确认是否继续使用已读取素材。",
        ),
        skill_id="writer2",
    )
    ops_logger = OpsEventLogger(tmp_path / "ops_events")

    await handle_text_with_platform(
        frame=_frame("写简报：https://example.com/a https://example.com/b"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        ops_event_logger=ops_logger,
    )

    events = read_ops_events(tmp_path / "ops_events", __import__("datetime").date.today())
    assert len(events) == 1
    assert events[0].severity == "warning"
    assert events[0].subject == "链接读取失败待用户确认"
    assert events[0].skill_id == "writer2"


@pytest.mark.anyio
async def test_handle_text_with_platform_does_not_crash_when_final_reply_fails():
    ws_client = FailingFinalReplyWsClient()
    platform_app = FakePlatformApp()

    await handle_text_with_platform(
        frame=_frame("写直报：https://example.com"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
    )

    assert platform_app.calls == [("wecom", "user-001", "写直报：https://example.com")]
    assert ws_client.stream_replies[0][1] == "writing-platform-001"
    assert len(ws_client.stream_replies) == 1


@pytest.mark.anyio
async def test_handle_text_with_platform_runs_platform_app_outside_event_loop():
    ws_client = FakeWsClient()
    platform_app = LoopCheckingPlatformApp()

    await handle_text_with_platform(
        frame=_frame("写直报：https://example.com"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
    )

    assert platform_app.ran_outside_event_loop is True
