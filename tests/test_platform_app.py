from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app.platform.app import PlatformApp  # noqa: E402
from app.platform.app import build_platform_tools  # noqa: E402
from app.platform.chat_log import ChatLogStore  # noqa: E402
from app.platform.config import PlatformConfig  # noqa: E402
from app.platform.conversation import ConversationStore  # noqa: E402
from app.platform.identity import AccessPolicy  # noqa: E402
from app.platform.models import UploadedFile  # noqa: E402
from app.platform.registry import SkillRegistry  # noqa: E402
from app.platform.storage import JobStore  # noqa: E402
from app.platform.user_registry import UserRegistry  # noqa: E402


def _compliant_body():
    return "微众银行" + "围绕小微企业融资需求持续完善数字化服务能力。" * 30


def _tools():
    return {
        "web_reader": lambda url: {
            "title": "网页标题",
            "text": "网页正文，包含可供直报写作的核心事实。",
            "url": url,
        },
        "llm_writer": lambda payload: {
            "title": "微众银行直报标题",
            "body": _compliant_body(),
        },
    }


def test_platform_app_handles_allowed_text_request_and_records_job(tmp_path):
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools=_tools(),
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    result = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )

    assert result.skill_id == "direct_report"
    assert result.output["title"] == "微众银行直报标题"

    result_files = list(tmp_path.glob("**/output/result.json"))
    assert len(result_files) == 1
    recorded = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert recorded["skill_id"] == "direct_report"


def test_platform_app_routes_short_followup_to_previous_draft_revision(tmp_path):
    seen_payloads = []
    web_calls = []

    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: web_calls.append(url)
            or {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": (
                    "微众银行修改后标题"
                    if payload.get("revision")
                    else "微众银行直报标题"
                ),
                "body": _compliant_body(),
            },
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    first = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    second = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="再压缩一点，突出政策背景",
    )

    assert first.output["title"] == "微众银行直报标题"
    assert second.skill_id == "direct_report"
    assert second.output["title"] == "微众银行修改后标题"
    assert web_calls == ["https://example.com/news"]
    revision_payload = next(p for p in seen_payloads if p.get("revision") is True)
    assert revision_payload["revision_request"] == "再压缩一点，突出政策背景"
    assert revision_payload["materials"][0]["source"] == "previous_draft"
    assert "微众银行直报标题" in revision_payload["materials"][0]["text"]


def test_platform_app_routes_followup_with_skill_keyword_to_previous_draft_revision(tmp_path):
    seen_payloads = []
    web_calls = []

    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: web_calls.append(url)
            or {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": (
                    "微众银行修改后标题"
                    if payload.get("revision")
                    else "微众银行直报标题"
                ),
                "body": _compliant_body(),
            },
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    second = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="这篇直报整体再正式一点，标题也改得更稳",
    )

    assert second.skill_id == "direct_report"
    assert second.output["title"] == "微众银行修改后标题"
    assert web_calls == ["https://example.com/news"]
    revision_payload = next(p for p in seen_payloads if p.get("revision") is True)
    assert revision_payload["revision_request"] == "这篇直报整体再正式一点，标题也改得更稳"
    assert revision_payload["materials"][0]["source"] == "previous_draft"


def test_platform_app_routes_critique_without_revision_keyword_to_revision(tmp_path):
    seen_payloads = []

    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "微众银行修改后标题" if payload.get("revision") else "微众银行直报标题",
                "body": _compliant_body(),
            },
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=ConversationStore(tmp_path / "conversations"),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    revised = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="还是太像新闻稿，开头也有点虚",
    )

    assert revised.skill_id == "direct_report"
    assert revised.output["title"] == "微众银行修改后标题"
    revision_payload = next(p for p in seen_payloads if p.get("revision") is True)
    assert revision_payload["revision_request"] == "还是太像新闻稿，开头也有点虚"


def test_platform_app_routes_add_original_content_request_to_revision(tmp_path):
    seen_payloads = []

    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含社会责任、数字普惠金融和科技能力等内容。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "微众银行修改后标题" if payload.get("revision") else "微众银行直报标题",
                "body": _compliant_body(),
            },
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=ConversationStore(tmp_path / "conversations"),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    revised = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="增加社会责任作为正文的第三部分。全文的篇幅再控制一下",
    )

    assert revised.skill_id == "direct_report"
    assert revised.output["title"] == "微众银行修改后标题"
    revision_payload = next(p for p in seen_payloads if p.get("revision") is True)
    assert revision_payload["revision_request"] == "增加社会责任作为正文的第三部分。全文的篇幅再控制一下"


def test_platform_app_revision_uses_last_successful_draft_after_clarification(tmp_path):
    seen_payloads = []

    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": (
                    "微众银行修改后标题"
                    if payload.get("revision")
                    else "微众银行直报标题"
                ),
                "body": _compliant_body(),
            },
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    unclear = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="我再看看",
    )
    revised = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="再压缩一点",
    )

    assert unclear.needs_clarification is True
    assert revised.skill_id == "direct_report"
    assert revised.output["title"] == "微众银行修改后标题"
    revision_payload = next(p for p in seen_payloads if p.get("revision") is True)
    assert revision_payload["previous_job_id"]
    assert "微众银行直报标题" in revision_payload["materials"][0]["text"]


def test_platform_app_uses_conversation_store_for_brief_revision(tmp_path):
    seen_payloads = []

    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供简报写作的核心事实。",
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "修改后简报标题" if payload.get("revision") else "初始简报标题",
                "body": "简报正文",
            },
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=ConversationStore(tmp_path / "conversations"),
        access_policy=AccessPolicy.allow_all_for_skills(["writer1"]),
    )

    first = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写简报：https://example.com/news",
    )
    revised = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="这篇简报标题再正式一点",
    )

    assert first.skill_id == "writer1"
    assert revised.skill_id == "writer1"
    assert revised.output["title"] == "修改后简报标题"
    revision_payload = next(p for p in seen_payloads if p.get("revision") is True)
    assert revision_payload["task"] == "writer1"
    assert revision_payload["materials"][0]["source"] == "previous_draft"


def test_platform_app_records_draft_versions_in_conversation_store(tmp_path):
    conversation_store = ConversationStore(tmp_path / "conversations")
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行修改后标题" if payload.get("revision") else "微众银行直报标题",
                "body": _compliant_body(),
            },
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=conversation_store,
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="这篇稿子再压缩一点",
    )

    conversation = conversation_store.get_active_conversation(
        channel="wecom",
        sender_userid="user-001",
    )

    assert conversation is not None
    assert conversation.active_skill_id == "direct_report"
    assert conversation.current_draft.version == 2
    assert conversation.current_draft.title == "微众银行修改后标题"
    assert [item.version for item in conversation.draft_versions] == [1, 2]
    assert conversation.revision_requests[-1].request == "这篇稿子再压缩一点"


def test_platform_app_writes_chat_log_for_turn(tmp_path):
    chat_log_store = ChatLogStore(tmp_path / "chat_logs", enabled=True)
    conversation_store = ConversationStore(tmp_path / "conversations")
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行修改后标题" if payload.get("revision") else "微众银行直报标题",
                "body": _compliant_body(),
            },
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=conversation_store,
        chat_log_store=chat_log_store,
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="还是太像新闻稿，开头有点虚",
        ack_message="收到，我沿着上一稿继续改。",
    )

    entries = []
    for path in sorted((tmp_path / "chat_logs").glob("*.jsonl")):
        entries.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())

    assert len(entries) == 2
    assert entries[-1]["user_text"] == "还是太像新闻稿，开头有点虚"
    assert entries[-1]["ack_message"] == "收到，我沿着上一稿继续改。"
    assert entries[-1]["intent"] == "revise_previous"
    assert entries[-1]["route_skill_id"] == "direct_report"
    assert entries[-1]["draft_version"] == 2
    assert entries[-1]["output"]["title"] == "微众银行修改后标题"


def test_platform_app_records_registered_sender_name(tmp_path):
    registry_path = tmp_path / "users.yaml"
    registry_path.write_text("user-001: test-user\n", encoding="utf-8")
    chat_log_store = ChatLogStore(tmp_path / "chat_logs", enabled=True)
    conversation_store = ConversationStore(tmp_path / "conversations")
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: {
                "title": "微众银行直报标题",
                "body": _compliant_body(),
            },
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=conversation_store,
        chat_log_store=chat_log_store,
        user_registry=UserRegistry(registry_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    assert app.resolve_sender_name("user-001") == "test-user"
    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )

    meta_path = next((tmp_path / "jobs").glob("**/meta.json"))
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    conversation = conversation_store.get_active_conversation(
        channel="wecom",
        sender_userid="user-001",
    )
    log_path = next((tmp_path / "chat_logs").glob("*.jsonl"))
    log_entry = json.loads(log_path.read_text(encoding="utf-8").strip())

    assert meta["sender_name"] == "test-user"
    assert conversation is not None
    assert conversation.sender_name == "test-user"
    assert log_entry["sender_name"] == "test-user"


def test_platform_app_writes_chat_log_when_runtime_fails(tmp_path):
    chat_log_store = ChatLogStore(tmp_path / "chat_logs", enabled=True)
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: (_ for _ in ()).throw(RuntimeError("model timeout")),
        },
        job_store=JobStore(tmp_path / "jobs"),
        chat_log_store=chat_log_store,
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    with pytest.raises(RuntimeError, match="model timeout"):
        app.handle_text_message(
            channel="wecom",
            sender_userid="user-001",
            text="帮我根据这个链接写直报：https://example.com/news",
            ack_message="收到，正在按直报写作流程处理，请稍后……",
        )

    entries = []
    for path in sorted((tmp_path / "chat_logs").glob("*.jsonl")):
        entries.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())

    assert len(entries) == 1
    assert entries[0]["user_text"] == "帮我根据这个链接写直报：https://example.com/news"
    assert entries[0]["final_reply"] == "处理失败，请稍后重试。"
    assert entries[0]["error"] == "RuntimeError: model timeout"


def test_platform_app_can_revise_from_previous_version(tmp_path):
    seen_payloads = []
    conversation_store = ConversationStore(tmp_path / "conversations")
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "微众银行修改后标题" if payload.get("revision") else "微众银行直报标题",
                "body": (
                    "微众银行上一版正文"
                    if payload.get("revision")
                    else "微众银行第一版正文"
                ),
            },
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=conversation_store,
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="这篇稿子再压缩一点",
    )
    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="回到第一版，再把标题改得更正式",
    )

    revision_payloads = [p for p in seen_payloads if p.get("revision") is True]
    assert len(revision_payloads) == 2
    assert "微众银行第一版正文" in revision_payloads[-1]["materials"][0]["text"]


def test_platform_app_does_not_treat_explicit_new_brief_request_as_revision(tmp_path):
    seen_payloads = []

    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供简报写作的核心事实。",
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "修改后简报标题" if payload.get("revision") else "初始简报标题",
                "body": "简报正文",
            },
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=ConversationStore(tmp_path / "conversations"),
        access_policy=AccessPolicy.allow_all_for_skills(["writer1"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写简报：https://example.com/news",
    )
    result = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="根据这篇材料写简报：微众银行围绕数字金融支持小微企业发展，持续完善线上化服务能力，相关工作取得积极进展。",
    )

    assert result.skill_id == "writer1"
    assert result.output["title"] == "初始简报标题"
    assert seen_payloads[-1].get("revision") is not True


def test_platform_app_does_not_treat_followup_as_revision_without_previous_draft(tmp_path):
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools=_tools(),
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    result = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="再压缩一点，突出政策背景",
    )

    assert result.skill_id is None
    assert result.needs_clarification is True


def test_platform_app_blocks_unauthorized_skill_before_runtime(tmp_path):
    calls = []
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: calls.append(url),
            "llm_writer": lambda payload: calls.append(payload),
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.from_dict(
            {
                "allow_unknown_users": False,
                "users": {"user-001": {"allowed_skills": []}},
            }
        ),
    )

    result = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )

    assert result.skill_id == "direct_report"
    assert result.needs_clarification is False
    assert result.message == "你没有使用该能力的权限。"
    assert calls == []


def test_platform_app_adds_job_context_to_workflow_inputs(tmp_path):
    seen_payloads = []
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "直报标题", "body": "直报正文"},
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report"]),
    )

    app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )

    assert seen_payloads
    assert "materials" in seen_payloads[0]
    meta_files = list(tmp_path.glob("**/meta.json"))
    assert len(meta_files) == 1


def test_build_platform_tools_exposes_common_material_readers(tmp_path):
    tools = build_platform_tools(
        PlatformConfig(
            model_name="MiniMax-M2.7",
            anthropic_api_key="test-key",
            anthropic_base_url="https://example.com/anthropic",
            skills_dir=Path("skills"),
            jobs_dir=tmp_path,
            policy_db_path=tmp_path / "policies.sqlite3",
            bank_db_path=tmp_path / "bank.sqlite3",
            access_policy_path=None,
        )
    )

    assert "web_reader" in tools
    assert "search" in tools
    assert "policy_search" in tools
    assert "policy_materials" in tools
    assert "policy_research" in tools
    assert "policy_wiki_materials" not in tools
    assert "bank_search" in tools
    assert "bank_materials" in tools
    assert "word_reader" in tools
    assert "pdf_reader" in tools
    assert "document_reader" in tools
    assert "llm_writer" in tools


def test_platform_app_runs_writer1_end_to_end_with_policy_materials(tmp_path):
    seen_payloads = []
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率，扩大普惠金融覆盖面。",
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_materials": lambda user_instruction, materials, limit=3: [
                {
                    "title": "关于提升小微企业金融服务质效的通知",
                    "text": "相关性说明：命中政策主题：小微企业金融服务\n政策摘录：提升小微企业金融服务质效。",
                    "url": "https://www.nfra.gov.cn/policy",
                    "source": "policy_knowledge",
                    "category": "policy_original",
                    "publish_date": "2026-05-18",
                }
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行提升小微企业金融服务质效", "body": "简报正文"},
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report", "writer1", "writer2"]),
    )

    result = app.handle_text_message(
        channel="local-test",
        sender_userid="user-001",
        text="帮我根据这个链接写简报：https://example.com/news",
    )

    assert result.skill_id == "writer1"
    assert result.output["title"] == "微众银行提升小微企业金融服务质效"
    assert result.output["sources"] == ["https://example.com/news", "https://www.nfra.gov.cn/policy"]
    assert seen_payloads[0]["skill_id"] == "writer1"
    assert seen_payloads[0]["materials"][1]["source"] == "policy_knowledge"

    result_files = list(tmp_path.glob("**/output/result.json"))
    assert len(result_files) == 1
    recorded = json.loads(result_files[0].read_text(encoding="utf-8"))
    assert recorded["skill_id"] == "writer1"


def test_platform_app_runs_writer2_when_trigger_is_specific(tmp_path):
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {"title": f"素材 {url}", "text": "素材正文", "url": url},
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: {"title": "微众银行多素材简报标题", "body": "多素材正文"},
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report", "writer1", "writer2"]),
    )

    result = app.handle_text_message(
        channel="local-test",
        sender_userid="user-001",
        text="帮我写多素材简报：https://example.com/a https://example.com/b",
    )

    assert result.skill_id == "writer2"
    assert result.output["title"] == "微众银行多素材简报标题"


def test_platform_app_handles_structured_brief_submission_with_uploaded_files(tmp_path):
    seen_payloads = []
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {"title": f"素材 {url}", "text": "链接素材正文", "url": url},
            "word_reader": lambda path, *, allowed_root: {
                "title": Path(path).name,
                "text": "Word 文件素材正文",
                "path": path,
            },
            "pdf_reader": lambda path, *, allowed_root: {
                "title": Path(path).name,
                "text": "PDF 文件素材正文",
                "path": path,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行多素材简报标题", "body": "多素材正文"},
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report", "writer1", "writer2"]),
    )

    result = app.handle_structured_request(
        channel="wecom-portal",
        sender_userid="user-001",
        skill_id="brief",
        text="请突出服务实体经济。",
        urls=["https://example.com/a"],
        files=[
            UploadedFile(filename="材料A.docx", content=b"docx-bytes"),
            UploadedFile(filename="材料B.pdf", content=b"pdf-bytes"),
        ],
    )

    assert result.skill_id == "writer2"
    assert result.output["title"] == "微众银行多素材简报标题"
    assert len(seen_payloads[0]["materials"]) == 3
    assert [item["title"] for item in seen_payloads[0]["materials"]] == [
        "素材 https://example.com/a",
        "材料A.docx",
        "材料B.pdf",
    ]

    saved_files = sorted(path.name for path in tmp_path.glob("**/input/*"))
    assert saved_files == ["材料A.docx", "材料B.pdf"]


def test_platform_app_rejects_unsupported_uploaded_files(tmp_path):
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={},
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report", "writer1", "writer2"]),
    )

    try:
        app.handle_structured_request(
            channel="wecom-portal",
            sender_userid="user-001",
            skill_id="brief",
            files=[UploadedFile(filename="材料C.xlsx", content=b"xlsx-bytes")],
        )
    except ValueError as exc:
        assert "Word(.docx)" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_platform_app_accepts_pptx_and_routes_it_through_document_reader(tmp_path):
    seen_payloads = []
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "document_reader": lambda path, *, allowed_root, work_dir: {
                "title": Path(path).name,
                "text": "PPT 中的经营成果和关键数据。",
                "path": path,
                "document_format": "pptx",
                "artifact_path": str(Path(work_dir) / "documents" / "x" / "document.json"),
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_research": lambda user_instruction, materials, usage_profile, limit=3: {},
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "微众银行经营成果简报", "body": "深圳前海微众银行（以下简称“我行”）持续提升服务能力。"},
        },
        job_store=JobStore(tmp_path),
        access_policy=AccessPolicy.allow_all_for_skills(["writer1"]),
    )

    result = app.handle_structured_request(
        channel="wecom",
        sender_userid="user-001",
        skill_id="writer1",
        files=[UploadedFile(filename="经营材料.pptx", content=b"pptx-bytes")],
    )

    assert result.skill_id == "writer1"
    assert seen_payloads[0]["materials"][0]["document_format"] == "pptx"
    assert list(tmp_path.glob("**/input/经营材料.pptx"))


def test_platform_app_checks_permission_before_saving_uploaded_files(tmp_path):
    jobs_dir = tmp_path / "jobs"
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={},
        job_store=JobStore(jobs_dir),
        access_policy=AccessPolicy.from_dict(
            {
                "allow_unknown_users": False,
                "default_allowed_skills": [],
                "users": {},
            }
        ),
    )

    result = app.handle_structured_request(
        channel="wecom",
        sender_userid="unauthorized-user",
        skill_id="direct_report",
        files=[UploadedFile(filename="材料.docx", content=b"private-material")],
    )

    assert result.message == "你没有使用该能力的权限。"
    assert list(jobs_dir.glob("**/input/*")) == []


def test_platform_app_moves_persisted_intake_file_into_job_input(tmp_path):
    pending = tmp_path / "M-Agent-Files" / "runtime" / "intake" / "session" / "files" / "材料.docx"
    pending.parent.mkdir(parents=True)
    pending.write_bytes(b"persisted-material")
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "document_reader": lambda path, *, allowed_root, work_dir: {
                "title": Path(path).name,
                "text": "持久化文件正文",
                "path": path,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_research": lambda user_instruction, materials, usage_profile, limit=3: {},
            "llm_writer": lambda payload: {
                "title": "微众银行简报标题",
                "body": "深圳前海微众银行（以下简称“我行”）持续提升服务能力。",
            },
        },
        job_store=JobStore(tmp_path / "M-Agent-Files" / "tasks" / "writing"),
        access_policy=AccessPolicy.allow_all_for_skills(["writer1"]),
    )

    result = app.handle_structured_request(
        channel="wecom",
        sender_userid="user-001",
        skill_id="writer1",
        files=[
            UploadedFile(
                filename="材料.docx",
                stored_path=str(pending),
                delete_after_read=True,
            )
        ],
    )

    assert result.skill_id == "writer1"
    saved = list((tmp_path / "M-Agent-Files" / "tasks" / "writing").glob("**/input/材料.docx"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"persisted-material"
    assert not pending.exists()


def test_platform_app_starts_new_rewrite_task_instead_of_revising_active_direct_report(tmp_path):
    seen_payloads = []
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供直报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or (
                {
                    "title": "微众银行直报标题",
                    "body": _compliant_body(),
                }
                if payload.get("task") == "direct_report"
                else {
                    "title": "",
                    "body": "这是润色后的新正文。",
                    "revision_note": "我把语气收得更正式，并顺了句子。",
                }
            ),
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=ConversationStore(tmp_path / "conversations"),
        access_policy=AccessPolicy.allow_all_for_skills(["direct_report", "rewrite"]),
    )

    first = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写直报：https://example.com/news",
    )
    second = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我润色这段：这个表述现在有点口语化，需要更正式一些。",
    )

    assert first.skill_id == "direct_report"
    assert second.skill_id == "rewrite"
    assert second.output["body"] == "这是润色后的新正文。"
    assert second.output["revision_note"] == "我把语气收得更正式，并顺了句子。"
    rewrite_payload = next(payload for payload in seen_payloads if payload.get("task") == "rewrite")
    assert rewrite_payload.get("revision") is not True
    assert rewrite_payload["materials"][0]["source"] == "user_text"
    assert "这个表述现在有点口语化" in rewrite_payload["materials"][0]["text"]


def test_platform_app_starts_new_rewrite_task_when_material_precedes_request(tmp_path):
    seen_payloads = []
    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "网页标题",
                "text": "网页正文，包含可供简报写作的核心事实。",
                "url": url,
            },
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or (
                {
                    "title": "微众银行简报标题",
                    "body": "深圳前海微众银行（以下简称“我行”）持续提升服务能力。",
                }
                if payload.get("task") != "rewrite"
                else {
                    "title": "",
                    "body": "这是润色后的新正文。",
                    "revision_note": "我按新的原文重新做了润色，没有沿用上一稿。",
                }
            ),
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=ConversationStore(tmp_path / "conversations"),
        access_policy=AccessPolicy.allow_all_for_skills(["writer1", "rewrite"]),
    )

    first = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="帮我根据这个链接写简报：https://example.com/news",
    )
    second = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text=(
            "县域经济作为国民经济的基本单元，是国家推动乡村振兴的重要切入点。"
            "微众银行持续完善县域金融服务供给。\n\n帮我整体润色一下"
        ),
    )

    assert first.skill_id == "writer1"
    assert second.skill_id == "rewrite"
    assert second.output["body"] == "这是润色后的新正文。"
    rewrite_payload = next(payload for payload in seen_payloads if payload.get("task") == "rewrite")
    assert rewrite_payload.get("revision") is not True
    assert rewrite_payload["materials"][0]["source"] == "user_text"
    assert "县域经济作为国民经济的基本单元" in rewrite_payload["materials"][0]["text"]
