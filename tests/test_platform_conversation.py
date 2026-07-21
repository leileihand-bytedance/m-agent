from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.conversation import ConversationStore  # noqa: E402
from app.platform.models import PlatformResult  # noqa: E402


def test_conversation_store_records_active_draft_versions(tmp_path):
    store = ConversationStore(tmp_path)

    store.record_result(
        channel="wecom",
        sender_userid="user-001",
        sender_name="test-user",
        job_id="job-001",
        result=PlatformResult(
            skill_id="direct_report",
            output={"title": "微众银行标题一", "body": "正文一", "sources": ["https://example.com/1"]},
            needs_clarification=False,
            message="已生成。",
        ),
    )
    store.record_result(
        channel="wecom",
        sender_userid="user-001",
        job_id="job-002",
        result=PlatformResult(
            skill_id="direct_report",
            output={"title": "微众银行标题二", "body": "正文二", "sources": ["https://example.com/1"]},
            needs_clarification=False,
            message="已修改。",
        ),
        revision_request="再压缩一点",
        previous_job_id="job-001",
    )

    conversation = store.get_active_conversation(channel="wecom", sender_userid="user-001")

    assert conversation is not None
    assert conversation.active_skill_id == "direct_report"
    assert conversation.sender_name == "test-user"
    assert conversation.current_draft.job_id == "job-002"
    assert conversation.current_draft.version == 2
    assert conversation.current_draft.title == "微众银行标题二"
    assert [item.version for item in conversation.draft_versions] == [1, 2]
    assert conversation.revision_requests[-1].request == "再压缩一点"


def test_conversation_store_ignores_clarification_results(tmp_path):
    store = ConversationStore(tmp_path)

    store.record_result(
        channel="wecom",
        sender_userid="user-001",
        job_id="job-001",
        result=PlatformResult(
            skill_id="writer1",
            output={"title": "简报标题", "body": "简报正文"},
            needs_clarification=False,
            message="已生成。",
        ),
    )
    store.record_result(
        channel="wecom",
        sender_userid="user-001",
        job_id="job-002",
        result=PlatformResult(
            skill_id=None,
            output={},
            needs_clarification=True,
            message="我还不确定你要做什么。",
        ),
    )

    conversation = store.get_active_conversation(channel="wecom", sender_userid="user-001")

    assert conversation is not None
    assert conversation.current_draft.job_id == "job-001"
    assert conversation.current_draft.title == "简报标题"


def test_conversation_store_does_not_duplicate_same_job_after_worker_restart(tmp_path):
    store = ConversationStore(tmp_path)
    result = PlatformResult(
        skill_id="writer1",
        output={"title": "简报标题", "body": "简报正文", "sources": []},
        needs_clarification=False,
        message="已生成。",
    )

    store.record_result(
        channel="wecom",
        sender_userid="user-001",
        job_id="job-001",
        result=result,
    )
    store.record_result(
        channel="wecom",
        sender_userid="user-001",
        job_id="job-001",
        result=result,
    )

    conversation = store.get_active_conversation(channel="wecom", sender_userid="user-001")

    assert conversation is not None
    assert [item.job_id for item in conversation.draft_versions] == ["job-001"]


def test_conversation_store_normalizes_legacy_brief_skill_id(tmp_path):
    root = tmp_path / "conversations"
    store = ConversationStore(root)
    store.record_result(
        channel="wecom",
        sender_userid="user-001",
        job_id="job-001",
        result=PlatformResult(
            skill_id="writer2",
            output={"title": "简报", "body": "正文"},
            needs_clarification=False,
            message="",
        ),
    )
    state_path = next(root.glob("*.json"))
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["active_skill_id"] == "writer1"
    assert payload["draft_versions"][0]["skill_id"] == "writer1"
    payload["active_skill_id"] = "writer2"
    payload["draft_versions"][0]["skill_id"] = "writer2"
    state_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    state = store.get_active_conversation(channel="wecom", sender_userid="user-001")

    assert state is not None
    assert state.active_skill_id == "writer1"
    assert state.current_draft.skill_id == "writer1"
