from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.chat_log import ChatLogStore  # noqa: E402
from app.platform.intent import ConversationIntent  # noqa: E402
from app.platform.models import PlatformResult  # noqa: E402


def test_chat_log_store_records_full_turn_for_debugging(tmp_path):
    store = ChatLogStore(tmp_path, enabled=True)

    store.record_turn(
        channel="wecom",
        sender_userid="user-001",
        sender_name="test-user",
        job_id="job-001",
        user_text="大标题中间用逗号隔开，正文的产品名称微业贷，要加双引号。",
        ack_message="收到，我沿着上一稿继续改。",
        final_reply="标题\n\n正文",
        intent=ConversationIntent.REVISE_PREVIOUS,
        route_skill_id="direct_report",
        result=PlatformResult(
            skill_id="direct_report",
            output={"title": "标题", "body": "正文", "sources": ["https://example.com"]},
            needs_clarification=False,
            message="已生成。",
        ),
        draft_version=2,
        previous_job_id="job-000",
        error=None,
    )

    log_files = list(tmp_path.glob("*.jsonl"))
    assert len(log_files) == 1
    entry = json.loads(log_files[0].read_text(encoding="utf-8").strip())
    assert entry["user_text"] == "大标题中间用逗号隔开，正文的产品名称微业贷，要加双引号。"
    assert entry["sender_userid"] == "user-001"
    assert entry["sender_name"] == "test-user"
    assert entry["ack_message"] == "收到，我沿着上一稿继续改。"
    assert entry["final_reply"] == "标题\n\n正文"
    assert entry["intent"] == "revise_previous"
    assert entry["route_skill_id"] == "direct_report"
    assert entry["result_skill_id"] == "direct_report"
    assert entry["draft_version"] == 2
    assert entry["previous_job_id"] == "job-000"
    assert entry["output"]["title"] == "标题"


def test_chat_log_store_can_be_disabled(tmp_path):
    store = ChatLogStore(tmp_path, enabled=False)

    store.record_turn(
        channel="wecom",
        sender_userid="user-001",
        job_id="job-001",
        user_text="完整用户输入",
        ack_message="收到",
        final_reply="回复",
        intent=ConversationIntent.NEW_TASK,
        route_skill_id="direct_report",
        result=PlatformResult(
            skill_id="direct_report",
            output={"title": "标题", "body": "正文"},
            needs_clarification=False,
            message="",
        ),
    )

    assert list(tmp_path.glob("*")) == []
