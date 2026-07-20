from pathlib import Path
import asyncio
from datetime import date
import json
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import app.writing.config as writing_config  # noqa: E402
import app.writing.bot as writing_bot  # noqa: E402
from app.platform.config import PlatformConfig  # noqa: E402
from app.platform.intent import ConversationIntent  # noqa: E402
from app.platform.models import PlatformResult, RoutedRequest, UploadedFile  # noqa: E402
from app.platform.ops.events import OpsEventLogger, read_ops_events  # noqa: E402
from app.platform.task_relations import (  # noqa: E402
    MaterialRole,
    TaskCardStatus,
    TaskRelationRepository,
    TaskRelationService,
)
from app.platform.task_status import write_task_status  # noqa: E402
from app.writing.bot import build_platform_config, handle_file_with_platform, handle_text_with_platform, mask_config_value  # noqa: E402
from app.writing.config import WritingBotConfig, load_config  # noqa: E402
from app.writing.intake import WritingIntakeStore  # noqa: E402


SENSITIVE_LOCAL_PATH = "/" + "Users/private"


class FakeWsClient:
    def __init__(self):
        self.stream_replies = []
        self.sent_messages = []
        self.uploaded_media = []
        self.media_replies = []

    async def reply_stream(self, frame, stream_id, message, finish):
        self.stream_replies.append((frame, stream_id, message, finish))

    async def send_message(self, sender, payload):
        self.sent_messages.append((sender, payload))
        return {"headers": {"req_id": "send-001"}, "errcode": 0, "errmsg": "ok"}

    async def download_file(self, url, aes_key):
        return {"buffer": b"fake docx", "filename": "material.docx"}

    async def upload_media(self, content, *, type, filename):
        self.uploaded_media.append((content, type, filename))
        return {"media_id": "media-001"}

    async def reply_media(self, frame, media_type, media_id):
        self.media_replies.append((frame, media_type, media_id))
        return {"headers": {"req_id": "reply-001"}, "errcode": 0, "errmsg": "ok"}


class FailingFinalReplyWsClient(FakeWsClient):
    async def reply_stream(self, frame, stream_id, message, finish):
        if stream_id.startswith("writing-result"):
            raise RuntimeError("reply failed")
        await super().reply_stream(frame, stream_id, message, finish)


class FailingUploadWsClient(FakeWsClient):
    async def upload_media(self, content, *, type, filename):
        raise RuntimeError(
            f"upload failed at {SENSITIVE_LOCAL_PATH} req_id=secret token=hidden"
        )


@pytest.mark.anyio
async def test_active_writing_text_uses_supported_markdown_message_type():
    ws_client = FakeWsClient()

    outcome = await writing_bot._send_active_writing_text(
        ws_client,
        "recipient-1",
        "简报已经生成",
    )

    assert outcome.delivered is True
    assert ws_client.sent_messages == [
        (
            "recipient-1",
            {
                "msgtype": "markdown",
                "markdown": {"content": "简报已经生成"},
            },
        )
    ]


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


def _frame(content: str, *, msgid: str = ""):
    frame = {
        "body": {
            "text": {"content": content},
            "from": {"userid": "user-001"},
        }
    }
    if msgid:
        frame["msgid"] = msgid
    return frame


class FakeWritingTaskService:
    def __init__(self, *, created: bool = True, active: bool = False):
        self.created = created
        self.active = active
        self.text_submissions = []
        self.structured_submissions = []

    def has_active_task(self, _sender_userid: str) -> bool:
        return self.active

    def submit_text(self, **kwargs):
        self.text_submissions.append(kwargs)
        return SimpleNamespace(
            created=self.created,
            task=SimpleNamespace(task_id="task-writing-001", status="queued"),
        )

    def submit_structured(self, **kwargs):
        self.structured_submissions.append(kwargs)
        return SimpleNamespace(
            created=self.created,
            task=SimpleNamespace(task_id="task-writing-002", status="queued"),
        )


@pytest.mark.anyio
async def test_active_user_can_submit_an_explicit_independent_task_to_the_queue():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(
        skill_id="direct_report",
        intent=ConversationIntent.NEW_TASK,
    )
    task_service = FakeWritingTaskService(active=True)

    await handle_text_with_platform(
        frame=_frame("新任务：写直报 https://example.com/new", msgid="message-new-002"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        task_service=task_service,
    )

    assert len(task_service.text_submissions) == 1
    assert task_service.text_submissions[0]["message_id"] == "message-new-002"
    assert "完成后会自动发送初稿" in ws_client.stream_replies[-1][2]


@pytest.mark.anyio
async def test_active_user_revision_is_queued_for_the_identified_existing_task():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(
        skill_id="direct_report",
        intent=ConversationIntent.REVISE_PREVIOUS,
    )
    task_service = FakeWritingTaskService(active=True)

    await handle_text_with_platform(
        frame=_frame("把第二段再压缩一点"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        task_service=task_service,
    )

    assert len(task_service.text_submissions) == 1
    assert task_service.text_submissions[0]["skill_id"] == "direct_report"
    assert "修改稿" in ws_client.stream_replies[-1][2]


def test_writing_intake_can_bind_collected_material_to_an_existing_task(tmp_path):
    store = WritingIntakeStore(storage_dir=tmp_path / "intake")
    store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="新增数据.docx", content=b"new-data"),
    )

    decision = store.apply_task_relation(
        channel="wecom",
        sender_userid="user-001",
        instruction="把这份数据补到第二段",
        skill_id="writer1",
        task_relation="add_material",
        target_task_id="logical-task-001",
        material_role="supplement",
    )

    assert decision.action == "run"
    assert decision.skill_id == "writer1"
    assert decision.target_task_id == "logical-task-001"
    assert decision.task_relation == "add_material"
    assert decision.files[0].filename == "新增数据.docx"


@pytest.mark.anyio
async def test_material_relation_clarification_answer_resumes_with_original_file(tmp_path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    for task_id, title in (("task-a", "普惠金融简报"), ("task-b", "数字金融简报")):
        repository.create_task(
            task_id=task_id,
            channel="wecom",
            user_id="user-001",
            skill_id="writer1",
            title=title,
            status=TaskCardStatus.COMPLETED,
            current_job_id=f"{task_id}-job",
            materials=[("url", f"https://example.com/{task_id}", MaterialRole.NEW_TASK)],
        )
    relation_service = TaskRelationService(repository)
    platform_app = FakePlatformApp(skill_id="writer1")
    platform_app.task_relation_service = relation_service
    platform_app.resolve_task_relation = lambda **kwargs: relation_service.resolve_text(
        channel=kwargs["channel"],
        user_id=kwargs["sender_userid"],
        text=kwargs["text"],
        route_skill_id=kwargs.get("route_skill_id"),
        has_new_material=kwargs.get("has_new_material", False),
        persist=kwargs.get("persist", True),
    )
    intake_store = WritingIntakeStore(storage_dir=tmp_path / "intake")
    intake_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="新增数据.docx", content=b"new-data"),
    )
    task_service = FakeWritingTaskService()
    ws_client = FakeWsClient()

    await handle_text_with_platform(
        frame=_frame("把这份数据补到第二段", msgid="relation-question"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
        task_service=task_service,
    )
    assert "我需要确认你指的是哪一项" in ws_client.stream_replies[-1][2]

    await handle_text_with_platform(
        frame=_frame("数字金融那篇", msgid="relation-answer"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-002",
        intake_store=intake_store,
        task_service=task_service,
    )

    assert len(task_service.structured_submissions) == 1
    submission = task_service.structured_submissions[0]
    assert submission["target_task_id"] == "task-b"
    assert submission["task_relation"] == "add_material"
    assert submission["files"][0].filename == "新增数据.docx"


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
        policy_db_path=tmp_path / "policy" / "policies.sqlite3",
        bank_db_path=tmp_path / "bank" / "bank.sqlite3",
        conversation_dir=tmp_path / "conversations",
        model_max_tokens=6144,
        model_timeout_seconds=90,
        model_max_attempts=3,
        model_retry_backoff_seconds=2.5,
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
    assert platform_config.model_timeout_seconds == 90
    assert platform_config.model_max_attempts == 3
    assert platform_config.model_retry_backoff_seconds == 2.5
    assert platform_config.direct_report_critic_mode == "advisory"
    assert platform_config.chat_log_enabled is False
    assert platform_config.chat_log_dir == tmp_path / "chat_logs"
    assert platform_config.user_registry_path == tmp_path / "users.yaml"
    assert platform_config.skills_dir == Path("skills")
    assert platform_config.jobs_dir == tmp_path / "jobs"
    assert platform_config.policy_db_path == tmp_path / "policy" / "policies.sqlite3"
    assert platform_config.bank_db_path == tmp_path / "bank" / "bank.sqlite3"
    assert platform_config.conversation_dir == tmp_path / "conversations"
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
                "M_AGENT_MODEL_TIMEOUT_SECONDS=90",
                "M_AGENT_MODEL_MAX_ATTEMPTS=3",
                "M_AGENT_MODEL_RETRY_BACKOFF_SECONDS=2.5",
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
    assert config.model_timeout_seconds == 90
    assert config.model_max_attempts == 3
    assert config.model_retry_backoff_seconds == 2.5
    assert config.direct_report_critic_mode == "off"
    assert config.chat_log_enabled is False
    assert config.chat_log_dir == Path(__file__).resolve().parent.parent / "custom-writing-chat-logs"
    assert config.ops_events_dir == Path(__file__).resolve().parent.parent / "custom-ops-events"
    assert config.user_registry_path == Path(__file__).resolve().parent.parent / "custom-users.yaml"
    assert config.intake_ttl_seconds == 900


def test_writing_load_config_uses_single_external_data_root(tmp_path):
    data_root = tmp_path / "M-Agent-Files"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "WRITING_BOT_ID=bot-id",
                "WRITING_BOT_SECRET=bot-secret",
                f"M_AGENT_DATA_DIR={data_root}",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(env_path)

    assert config.jobs_dir == data_root / "tasks" / "writing"
    assert config.policy_db_path == data_root / "knowledge" / "policy" / "policies.sqlite3"
    assert config.bank_db_path == data_root / "knowledge" / "bank" / "bank.sqlite3"
    assert config.conversation_dir == data_root / "runtime" / "conversations"
    assert config.chat_log_dir == data_root / "runtime" / "chat-logs"
    assert config.ops_events_dir == data_root / "runtime" / "ops" / "events"
    assert config.ops_heartbeat_dir == data_root / "runtime" / "ops" / "heartbeats"
    assert config.user_registry_path == data_root / "runtime" / "users" / "review_users.yaml"
    assert config.intake_dir == data_root / "runtime" / "intake"
    assert config.task_queue_db_path == data_root / "runtime" / "task-execution" / "writing.sqlite3"
    assert config.task_worker_count == 1
    assert config.task_poll_seconds == 0.25
    assert config.task_recovery_seconds == 5.0
    assert config.task_lease_seconds == 120


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
        frame=_frame("写直报：https://example.com", msgid="message-001"),
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
async def test_new_direct_report_is_accepted_into_persistent_queue():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="direct_report")
    task_service = FakeWritingTaskService()

    await handle_text_with_platform(
        frame=_frame("写直报：https://example.com", msgid="message-001"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        task_service=task_service,
    )

    assert platform_app.calls == []
    assert task_service.text_submissions[0]["message_id"] == "message-001"
    assert task_service.text_submissions[0]["skill_id"] == "direct_report"
    assert ws_client.stream_replies == [
        (
            _frame("写直报：https://example.com", msgid="message-001"),
            "writing-queued-001",
            "已进入直报写作队列，完成后会自动发送初稿。",
            True,
        )
    ]


@pytest.mark.anyio
async def test_shenyinxie_news_is_accepted_into_persistent_queue():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="shenyinxie_news")
    task_service = FakeWritingTaskService()

    await handle_text_with_platform(
        frame=_frame("生成7月上半月深银协动态", msgid="message-shenyinxie-001"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        task_service=task_service,
    )

    assert platform_app.calls == []
    assert task_service.text_submissions[0]["message_id"] == "message-shenyinxie-001"
    assert task_service.text_submissions[0]["skill_id"] == "shenyinxie_news"
    assert ws_client.stream_replies[-1][2] == (
        "已进入深银协动态队列，完成后会自动发送初稿。"
    )


@pytest.mark.anyio
async def test_revision_uses_persistent_queue_when_queue_is_enabled():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(
        skill_id="direct_report",
        intent=ConversationIntent.REVISE_PREVIOUS,
    )
    task_service = FakeWritingTaskService()

    await handle_text_with_platform(
        frame=_frame("标题再稳一点"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        task_service=task_service,
    )

    assert len(task_service.text_submissions) == 1
    assert task_service.text_submissions[0]["text"] == "标题再稳一点"
    assert platform_app.calls == []
    assert ws_client.stream_replies[-1][2] == "已进入直报写作队列，完成后会自动发送修改稿。"


@pytest.mark.anyio
async def test_revision_clears_empty_intake_session_and_continues_previous_draft(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(
        skill_id="writer1",
        intent=ConversationIntent.REVISE_PREVIOUS,
    )
    intake_store = WritingIntakeStore(storage_dir=tmp_path / "intake")
    waiting = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="在上一轮的基础上继续改稿",
    )

    await handle_text_with_platform(
        frame=_frame("深化业务融合这一段，只保留一个信贷审批案例"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )

    assert waiting.action == "wait"
    assert platform_app.calls == [
        ("wecom", "user-001", "深化业务融合这一段，只保留一个信贷审批案例")
    ]
    assert ws_client.stream_replies[0][2] == "收到，我沿着上一稿继续改。"
    assert not list((tmp_path / "intake").glob("*/session.json"))


@pytest.mark.anyio
async def test_revision_does_not_bypass_intake_with_collected_new_material(tmp_path):
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(
        skill_id="writer1",
        intent=ConversationIntent.REVISE_PREVIOUS,
    )
    intake_store = WritingIntakeStore(storage_dir=tmp_path / "intake")
    intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="写简报",
    )
    intake_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="新材料.docx", content=b"new-material"),
    )

    await handle_text_with_platform(
        frame=_frame("开头再精简一点"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )

    assert platform_app.calls == []
    assert "已补充要求" in ws_client.stream_replies[-1][2]
    assert list((tmp_path / "intake").glob("*/session.json"))


@pytest.mark.anyio
async def test_handle_text_with_platform_collects_research_synthesis_files_until_start():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="research_synthesis")
    intake_store = WritingIntakeStore()

    await handle_text_with_platform(
        frame=_frame("帮我做综合调研材料整合"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-001",
        intake_store=intake_store,
    )
    first_file = intake_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="调研提纲.docx", content=b"outline"),
    )
    second_file = intake_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="部门素材.docx", content=b"material"),
    )
    decision = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="开始写",
    )

    assert first_file.action == "wait"
    assert second_file.action == "wait"
    assert decision.action == "run"
    assert decision.skill_id == "research_synthesis"
    assert decision.ack_message == "收到，正在按综合调研整合流程处理，请稍后……"
    assert [item.filename for item in decision.files] == ["调研提纲.docx", "部门素材.docx"]


def test_writing_intake_recognizes_natural_research_summary_wording():
    intake_store = WritingIntakeStore()

    decision = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="帮我把下面的调研材料做个汇总",
    )

    assert decision.action == "wait"
    assert "调研提纲" in decision.reply


def test_writing_intake_runs_shenyinxie_news_without_materials():
    intake_store = WritingIntakeStore()

    decision = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="生成深银协动态",
    )

    assert decision.action == "run"
    assert decision.skill_id == "shenyinxie_news"
    assert "深银协动态" in decision.ack_message


def test_writing_intake_runs_internal_weekly_without_materials():
    intake_store = WritingIntakeStore()

    decision = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="生成本周内参周报",
    )

    assert decision.action == "run"
    assert decision.skill_id == "internal_weekly"
    assert "内参周报" in decision.ack_message


def test_writing_intake_routes_current_day_market_summary_update_to_internal_weekly():
    intake_store = WritingIntakeStore()

    decision = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="生成一下今天的资本市场综述",
    )

    assert decision.action == "run"
    assert decision.skill_id == "internal_weekly"
    assert "资本市场" in decision.ack_message


def test_shenyinxie_news_clarification_keeps_skill_and_user_period(tmp_path):
    intake_store = WritingIntakeStore(storage_dir=tmp_path / "intake")

    intake_store.restore_clarification(
        channel="wecom",
        sender_userid="user-001",
        skill_id="shenyinxie_news",
        text="生成深银协动态",
        material_text="",
        urls=(),
        files=(),
        message="请明确要生成哪个月的上半月还是下半月。",
    )
    resumed = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        message_id="followup-001",
        text="7月上半月",
    )

    assert resumed.action == "run"
    assert resumed.skill_id == "shenyinxie_news"
    assert "生成深银协动态" in resumed.text
    assert "7月上半月" in resumed.text


def test_writing_intake_ignores_duplicate_file_message_ids():
    intake_store = WritingIntakeStore()
    uploaded = UploadedFile(filename="素材.docx", content=b"material")

    first = intake_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        message_id="file-message-001",
        file=uploaded,
    )
    duplicate = intake_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        message_id="file-message-001",
        file=uploaded,
    )
    decision = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        message_id="intent-message-001",
        text="写简报",
    )

    assert first.action == "wait"
    assert duplicate.action == "wait"
    assert "已经收到" in duplicate.reply
    assert decision.action == "run"
    assert decision.skill_id == "writer1"


def test_writing_intake_restores_single_message_task_after_background_clarification(tmp_path):
    intake_store = WritingIntakeStore(storage_dir=tmp_path / "intake")

    intake_store.restore_clarification(
        channel="wecom",
        sender_userid="user-001",
        skill_id="writer1",
        text="写简报：https://example.com/a",
        material_text="",
        urls=("https://example.com/a",),
        files=(),
        message="其中一个链接读取失败。",
    )
    resumed = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        message_id="followup-001",
        text="继续使用已读取素材写",
    )

    assert resumed.action == "run"
    assert resumed.skill_id == "writer1"
    assert resumed.urls == ("https://example.com/a",)
    assert "继续使用已读取素材写" in resumed.text


@pytest.mark.anyio
async def test_research_synthesis_clarification_keeps_files_and_followup_resumes(tmp_path):
    class ClarifyThenSucceedPlatformApp(FakePlatformApp):
        def handle_structured_request(self, **kwargs) -> PlatformResult:
            self.structured_calls.append(kwargs)
            if len(self.structured_calls) == 1:
                return PlatformResult(
                    skill_id="research_synthesis",
                    output={"title": "", "body": "", "sources": []},
                    needs_clarification=True,
                    message='请明确回复哪一份是调研提纲，例如“调研提纲.docx 是提纲”。',
                )
            return PlatformResult(
                skill_id="research_synthesis",
                output={"title": "综合调研材料", "body": "整合正文", "sources": []},
                needs_clarification=False,
                message="已完成。",
            )

    ws_client = FakeWsClient()
    platform_app = ClarifyThenSucceedPlatformApp(skill_id="research_synthesis")
    storage_dir = tmp_path / "runtime" / "intake"
    intake_store = WritingIntakeStore(storage_dir=storage_dir)

    await handle_text_with_platform(
        frame=_frame("按提纲整合综合调研材料"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-intent",
        intake_store=intake_store,
    )
    intake_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="调研提纲.docx", content=b"outline"),
    )
    intake_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="部门-调研提纲及回复.docx", content=b"reply"),
    )
    stored_files = sorted(storage_dir.glob("**/files/*"))

    await handle_text_with_platform(
        frame=_frame("开始写"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-start",
        intake_store=intake_store,
    )

    assert len(platform_app.structured_calls) == 1
    assert all(path.exists() for path in stored_files)
    assert list(storage_dir.glob("*/session.json"))

    await handle_text_with_platform(
        frame=_frame("调研提纲.docx 是提纲"),
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-answer",
        intake_store=intake_store,
    )

    assert len(platform_app.structured_calls) == 2
    assert "调研提纲.docx 是提纲" in platform_app.structured_calls[1]["text"]
    assert [item.filename for item in platform_app.structured_calls[1]["files"]] == [
        "调研提纲.docx",
        "部门-调研提纲及回复.docx",
    ]
    assert ws_client.stream_replies[-1][2] == "综合调研材料\n\n整合正文"
    assert not any(path.exists() for path in stored_files)
    assert not list(storage_dir.glob("*/session.json"))


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
async def test_collected_brief_material_is_snapshotted_into_persistent_queue():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")
    intake_store = WritingIntakeStore()
    task_service = FakeWritingTaskService()

    await handle_text_with_platform(
        frame={**_frame(LONG_MATERIAL), "msgid": "material-message"},
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-material",
        intake_store=intake_store,
        task_service=task_service,
    )
    await handle_text_with_platform(
        frame={**_frame("写简报"), "msgid": "intent-message"},
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-intent",
        intake_store=intake_store,
        task_service=task_service,
    )

    assert platform_app.structured_calls == []
    assert len(task_service.structured_submissions) == 1
    submission = task_service.structured_submissions[0]
    assert submission["message_id"] == "intent-message"
    assert submission["skill_id"] == "writer1"
    assert "普惠金融" in submission["material_text"]
    assert ws_client.stream_replies[-1][2] == (
        "已进入简报写作队列，完成后会自动发送初稿。"
    )


@pytest.mark.anyio
async def test_duplicate_completed_queue_submission_does_not_leave_stale_intake():
    ws_client = FakeWsClient()
    platform_app = FakePlatformApp(skill_id="writer1")
    intake_store = WritingIntakeStore()
    task_service = FakeWritingTaskService(created=False)

    await handle_text_with_platform(
        frame={**_frame(LONG_MATERIAL), "msgid": "material-message"},
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-material",
        intake_store=intake_store,
        task_service=task_service,
    )
    await handle_text_with_platform(
        frame={**_frame("写简报"), "msgid": "duplicate-intent-message"},
        ws_client=ws_client,
        platform_app=platform_app,
        req_id_factory=lambda prefix: f"{prefix}-intent",
        intake_store=intake_store,
        task_service=task_service,
    )
    later = intake_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        message_id="later-start-message",
        text="开始写",
    )

    assert len(task_service.structured_submissions) == 1
    assert ws_client.stream_replies[-1][2] == (
        "这项简报写作任务已经在处理中，无需重复提交。完成后会自动发送初稿。"
    )
    assert "task-writing-002" not in ws_client.stream_replies[-1][2]
    assert later.action == "bypass"


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


def test_result_output_file_uses_delivery_limit_separate_from_input_limit(tmp_path, monkeypatch):
    output_dir = tmp_path / "task" / "output"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "draft.docx"
    output_path.write_bytes(b"12345")
    monkeypatch.setattr(writing_bot, "MAX_WRITING_FILE_BYTES", 4)
    monkeypatch.setattr(writing_bot, "MAX_WRITING_OUTPUT_FILE_BYTES", 8)
    result = PlatformResult(
        skill_id="research_synthesis",
        output={"output_file": str(output_path)},
        needs_clarification=False,
        message="完成",
    )

    assert writing_bot._result_output_file(result) == output_path.resolve()


def test_result_output_file_recognizes_shenyinxie_news_docx(tmp_path):
    output_dir = tmp_path / "task" / "output"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "深银协动态.docx"
    output_path.write_bytes(b"fake-docx")
    result = PlatformResult(
        skill_id="shenyinxie_news",
        output={"output_file": str(output_path)},
        needs_clarification=False,
        message="完成",
    )

    assert writing_bot._result_output_file(result) == output_path.resolve()


def test_result_output_file_recognizes_internal_weekly_review_markdown(tmp_path):
    output_dir = tmp_path / "task" / "output"
    output_dir.mkdir(parents=True)
    output_path = output_dir / "内参周报-内容核对稿.md"
    output_path.write_text("# 内容核对稿\n", encoding="utf-8")
    result = PlatformResult(
        skill_id="internal_weekly",
        output={"output_file": str(output_path)},
        needs_clarification=False,
        message="完成",
    )

    assert writing_bot._result_output_file(result) == output_path.resolve()


def test_writing_intake_default_allows_ten_files_and_rejects_eleventh():
    store = WritingIntakeStore()

    accepted = [
        store.add_file(
            channel="wecom",
            sender_userid="user-001",
            file=UploadedFile(filename=f"材料-{index}.docx", content=b"x"),
        )
        for index in range(1, 11)
    ]
    blocked = store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="材料-11.docx", content=b"x"),
    )

    assert all("最多接收" not in decision.reply for decision in accepted)
    assert "最多接收 10 个文件" in blocked.reply


def test_writing_intake_persists_file_and_recovers_after_restart(tmp_path):
    storage_dir = tmp_path / "M-Agent-Files" / "runtime" / "intake"
    first_store = WritingIntakeStore(storage_dir=storage_dir)
    first_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="帮我写简报",
    )
    first_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=writing_bot.UploadedFile(filename="材料.docx", content=b"persistent-content"),
    )

    stored_files = list(storage_dir.glob("**/files/*"))
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == b"persistent-content"

    restarted_store = WritingIntakeStore(storage_dir=storage_dir)
    decision = restarted_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="开始写",
    )

    assert decision.action == "run"
    assert decision.skill_id == "writer1"
    assert decision.files[0].filename == "材料.docx"
    assert decision.files[0].content == b""
    assert Path(decision.files[0].stored_path).read_bytes() == b"persistent-content"
    assert decision.files[0].delete_after_read is True


def test_writing_intake_recovers_pending_clarification_after_restart(tmp_path):
    storage_dir = tmp_path / "M-Agent-Files" / "runtime" / "intake"
    first_store = WritingIntakeStore(storage_dir=storage_dir)
    first_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="按提纲整合综合调研材料",
    )
    first_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="调研提纲.docx", content=b"outline"),
    )
    first_store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=UploadedFile(filename="部门-调研提纲及回复.docx", content=b"reply"),
    )
    first_store.handle_text(channel="wecom", sender_userid="user-001", text="开始写")
    first_store.mark_clarification(
        channel="wecom",
        sender_userid="user-001",
        message="请明确哪一份是提纲。",
    )

    restarted_store = WritingIntakeStore(storage_dir=storage_dir)
    decision = restarted_store.handle_text(
        channel="wecom",
        sender_userid="user-001",
        text="调研提纲.docx 是提纲",
    )

    assert decision.action == "run"
    assert decision.skill_id == "research_synthesis"
    assert "调研提纲.docx 是提纲" in decision.text
    assert [item.filename for item in decision.files] == [
        "调研提纲.docx",
        "部门-调研提纲及回复.docx",
    ]


def test_writing_intake_removes_expired_persisted_sessions_on_startup(tmp_path):
    storage_dir = tmp_path / "M-Agent-Files" / "runtime" / "intake"
    store = WritingIntakeStore(storage_dir=storage_dir, ttl_seconds=60)
    store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=writing_bot.UploadedFile(filename="材料.docx", content=b"expired-content"),
    )
    state_path = next(storage_dir.glob("*/session.json"))
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["updated_at"] = 0
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    WritingIntakeStore(storage_dir=storage_dir, ttl_seconds=60)

    assert list(storage_dir.iterdir()) == []


@pytest.mark.anyio
async def test_structured_run_cleans_persisted_intake_file_after_failure(tmp_path):
    storage_dir = tmp_path / "M-Agent-Files" / "runtime" / "intake"
    store = WritingIntakeStore(storage_dir=storage_dir)
    store.handle_text(channel="wecom", sender_userid="user-001", text="帮我写简报")
    store.add_file(
        channel="wecom",
        sender_userid="user-001",
        file=writing_bot.UploadedFile(filename="材料.docx", content=b"pending-content"),
    )
    decision = store.handle_text(channel="wecom", sender_userid="user-001", text="开始写")
    stored_path = Path(decision.files[0].stored_path)

    await writing_bot._run_structured_decision(
        decision=decision,
        frame=_frame("开始写"),
        ws_client=FakeWsClient(),
        platform_app=FakePlatformApp(error=RuntimeError("model timeout")),
        req_id_factory=lambda prefix: f"{prefix}-001",
        sender_userid="user-001",
        sender_name="test-user",
        ops_event_logger=None,
        intake_store=store,
    )

    assert not stored_path.exists()
    assert not list(storage_dir.glob("*/session.json"))


@pytest.mark.anyio
async def test_structured_research_synthesis_returns_generated_word_file(tmp_path):
    output_path = tmp_path / "tasks" / "job-001" / "output" / "综合调研材料初稿.docx"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"generated-word")
    result = PlatformResult(
        skill_id="research_synthesis",
        output={
            "title": "综合调研材料",
            "body": "一、总体情况\n整合正文",
            "sources": ["调研提纲.docx", "科技部素材.docx"],
            "output_file": str(output_path),
        },
        needs_clarification=False,
        message="已生成综合调研 Word 初稿。",
    )
    ws_client = FakeWsClient()

    await writing_bot._run_structured_decision(
        decision=writing_bot.IntakeDecision(action="run", skill_id="research_synthesis"),
        frame=_frame("开始写"),
        ws_client=ws_client,
        platform_app=FakePlatformApp(result=result, skill_id="research_synthesis"),
        req_id_factory=lambda prefix: f"{prefix}-001",
        sender_userid="user-001",
        sender_name="test-user",
        ops_event_logger=None,
    )

    assert ws_client.stream_replies[-1][2] == "已生成综合调研 Word 初稿。"
    assert ws_client.uploaded_media == [(b"generated-word", "file", "综合调研材料初稿.docx")]
    assert ws_client.media_replies[-1][1:] == ("file", "media-001")
    assert (output_path.parent.parent / "delivery.json").is_file()


@pytest.mark.anyio
async def test_structured_output_file_failure_uses_public_delivery_and_safe_ops_alert(tmp_path):
    task_dir = tmp_path / "tasks" / "job-002"
    output_path = task_dir / "output" / "综合调研材料初稿.docx"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"generated-word")
    write_task_status(task_dir, processing_status="completed", delivery_status="unknown")
    result = PlatformResult(
        skill_id="research_synthesis",
        output={"output_file": str(output_path)},
        needs_clarification=False,
        message="已生成综合调研 Word 初稿。",
    )
    ws_client = FailingUploadWsClient()
    ops_logger = OpsEventLogger(tmp_path / "ops")

    await writing_bot._run_structured_decision(
        decision=writing_bot.IntakeDecision(action="run", skill_id="research_synthesis"),
        frame=_frame("开始写"),
        ws_client=ws_client,
        platform_app=FakePlatformApp(result=result, skill_id="research_synthesis"),
        req_id_factory=lambda prefix: f"{prefix}-001",
        sender_userid="user-001",
        sender_name="test-user",
        ops_event_logger=ops_logger,
    )

    assert ws_client.stream_replies[-1][2] == (
        "文件上传失败，已提醒管理员处理。处理编号：job-002。"
    )
    status = json.loads((task_dir / "status.json").read_text(encoding="utf-8"))
    assert status["processing_status"] == "completed"
    assert status["delivery_status"] == "failed"
    events = read_ops_events(tmp_path / "ops", date.today())
    assert events[-1].subject == "附件交付失败"
    assert SENSITIVE_LOCAL_PATH not in events[-1].detail
    assert "secret" not in events[-1].detail
    assert "hidden" not in events[-1].detail


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
