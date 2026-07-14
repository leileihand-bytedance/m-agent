from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.models import PlatformResult  # noqa: E402
from app.platform.storage import JobStore  # noqa: E402


def test_job_store_creates_isolated_job_directories(tmp_path):
    store = JobStore(root_dir=tmp_path)

    job = store.create_job(
        channel="wecom",
        sender_userid="user-001",
        message="帮我写直报：https://example.com/news",
    )

    assert job.job_dir.exists()
    assert job.input_dir.exists()
    assert job.work_dir.exists()
    assert job.output_dir.exists()
    assert job.meta_path.exists()
    assert job.status_path.exists()
    now = job.job_id[:8]
    assert job.job_dir.parent == tmp_path / now[:4] / now[4:6]

    meta = json.loads(job.meta_path.read_text(encoding="utf-8"))
    assert meta["channel"] == "wecom"
    assert meta["sender_userid"] == "user-001"
    assert meta["sender_name"] == "user-001"
    assert meta["message_preview"] == "帮我写直报：https://example.com/news"
    assert "created_at" in meta
    status = json.loads(job.status_path.read_text(encoding="utf-8"))
    assert status["processing_status"] == "processing"
    assert status["delivery_status"] == "unknown"


def test_job_store_records_sender_name(tmp_path):
    store = JobStore(root_dir=tmp_path)

    job = store.create_job(
        channel="wecom",
        sender_userid="user-001",
        sender_name="test-user",
        message="写直报",
    )

    meta = json.loads(job.meta_path.read_text(encoding="utf-8"))
    assert meta["sender_userid"] == "user-001"
    assert meta["sender_name"] == "test-user"


def test_job_store_truncates_message_preview(tmp_path):
    store = JobStore(root_dir=tmp_path, message_preview_chars=10)

    job = store.create_job(channel="wecom", sender_userid="user-001", message="一" * 40)

    meta = json.loads(job.meta_path.read_text(encoding="utf-8"))
    assert meta["message_preview"] == "一" * 10 + "..."


def test_job_store_writes_result_without_secrets(tmp_path):
    store = JobStore(root_dir=tmp_path)
    job = store.create_job(channel="wecom", sender_userid="user-001", message="写直报")

    store.write_result(
        job,
        PlatformResult(
            skill_id="direct_report",
            output={"title": "标题", "body": "正文", "sources": ["https://example.com"]},
            needs_clarification=False,
            message="",
        ),
    )

    payload = json.loads((job.output_dir / "result.json").read_text(encoding="utf-8"))
    status = json.loads(job.status_path.read_text(encoding="utf-8"))
    assert payload["skill_id"] == "direct_report"
    assert payload["output"]["title"] == "标题"
    assert "api_key" not in json.dumps(payload, ensure_ascii=False).lower()
    assert status["processing_status"] == "completed"
    assert "title" not in status
    assert "body" not in status


def test_job_store_records_needs_input_and_failed_statuses(tmp_path):
    store = JobStore(root_dir=tmp_path)
    needs_input = store.create_job(channel="wecom", sender_userid="user-001", message="写简报")
    failed = store.create_job(channel="wecom", sender_userid="user-001", message="写直报")

    store.write_result(
        needs_input,
        PlatformResult(
            skill_id="writer1",
            output={},
            needs_clarification=True,
            message="请补充素材",
        ),
    )
    store.write_result(
        failed,
        PlatformResult(
            skill_id="direct_report",
            output={},
            needs_clarification=False,
            message="处理失败，请稍后重试。",
        ),
    )

    needs_input_status = json.loads(needs_input.status_path.read_text(encoding="utf-8"))
    failed_status = json.loads(failed.status_path.read_text(encoding="utf-8"))
    assert needs_input_status["processing_status"] == "needs_input"
    assert failed_status["processing_status"] == "failed"


def test_job_store_finds_latest_result_for_user_and_channel(tmp_path):
    store = JobStore(root_dir=tmp_path)
    older = store.create_job(channel="wecom", sender_userid="user-001", message="写直报")
    other_channel = store.create_job(channel="local", sender_userid="user-001", message="写直报")
    newer = store.create_job(channel="wecom", sender_userid="user-001", message="写直报")
    other_user = store.create_job(channel="wecom", sender_userid="user-002", message="写直报")

    store.write_result(
        older,
        PlatformResult(
            skill_id="direct_report",
            output={"title": "旧标题", "body": "旧正文"},
            needs_clarification=False,
            message="",
        ),
    )
    store.write_result(
        other_channel,
        PlatformResult(
            skill_id="direct_report",
            output={"title": "其他入口标题", "body": "其他入口正文"},
            needs_clarification=False,
            message="",
        ),
    )
    store.write_result(
        newer,
        PlatformResult(
            skill_id="direct_report",
            output={"title": "新标题", "body": "新正文"},
            needs_clarification=False,
            message="",
        ),
    )
    store.write_result(
        other_user,
        PlatformResult(
            skill_id="direct_report",
            output={"title": "他人标题", "body": "他人正文"},
            needs_clarification=False,
            message="",
        ),
    )

    latest = store.find_latest_result_for_user(sender_userid="user-001", channel="wecom")

    assert latest is not None
    assert latest.job_id == newer.job_id
    assert latest.skill_id == "direct_report"
    assert latest.output["title"] == "新标题"
