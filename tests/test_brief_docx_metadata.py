from __future__ import annotations

from pathlib import Path
import zipfile

from lxml import etree

from app.platform.app import PlatformApp
from app.platform.conversation import ConversationStore
from app.platform.gateway.wecom import format_text_reply
from app.platform.identity import AccessPolicy
from app.platform.models import PlatformResult, RoutedRequest
from app.platform.registry import SkillRegistry
from app.platform.runtime import PlatformRuntime
from app.platform.storage import JobStore
from app.platform.task_relations import TaskRelationRepository, TaskRelationService
from app.platform.tools import ToolGateway
from skills.writer1.document_metadata import (
    extract_brief_document_metadata,
    is_brief_document_metadata_only,
    requests_brief_signer_change,
    strip_brief_document_metadata_instructions,
)
from skills.writer1.docx_output import generate_brief_docx
from skills.writer1.schema import BriefResult
from skills.writer1.workflow import run


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _paragraphs(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return [
        "".join(paragraph.xpath(".//w:t/text()", namespaces={"w": W})).strip()
        for paragraph in root.xpath("//w:body/w:p", namespaces={"w": W})
    ]


def _compliant_body() -> str:
    return "深圳前海微众银行（以下简称“我行”）" + "持续提升普惠金融服务质效。" * 70


def test_extracts_supported_brief_document_metadata_but_not_signer():
    text = (
        "日期写7月21日，期号是第18期，责任编辑是谁谁谁，"
        "联系电话为0755-89950000，签发人改成其他人"
    )

    assert extract_brief_document_metadata(text) == {
        "issue_date": "7月21日",
        "issue_number": "18",
        "editor": "谁谁谁",
        "contact": "0755-89950000",
    }
    assert is_brief_document_metadata_only(text) is True
    assert requests_brief_signer_change(text) is True
    assert is_brief_document_metadata_only("压缩第二段，日期改为7月21日") is False
    assert (
        strip_brief_document_metadata_instructions("压缩第二段，日期改为7月21日")
        == "压缩第二段"
    )


def test_generate_brief_docx_updates_supported_metadata_and_keeps_signer(tmp_path: Path):
    output = generate_brief_docx(
        title="微众银行持续提升普惠金融服务质效",
        body="第一段正文。\n\n第二段正文。",
        output_dir=tmp_path,
        document_metadata={
            "issue_date": "7月21日",
            "issue_number": "18",
            "editor": "张三",
            "contact": "0755-89950000",
            "signer": "不应写入的人名",
        },
    )

    paragraphs = _paragraphs(output)
    assert "（2026年第18期）" in paragraphs
    assert any("2026年7月21日" in item and "签发人：李南青" in item for item in paragraphs)
    assert "（责任编辑：张三，0755-89950000）" in paragraphs
    assert all("不应写入的人名" not in item for item in paragraphs)


def test_metadata_only_revision_merges_previous_values_without_calling_model(tmp_path: Path):
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: (_ for _ in ()).throw(
                AssertionError("metadata-only request must not call the model")
            )
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": "日期改为7月21日，期号第18期，责任编辑改成张三",
            "previous_title": "上一稿简报标题",
            "previous_body": "上一稿第一段。\n\n上一稿第二段。",
            "previous_sources": ["https://example.com/source"],
            "previous_document_metadata": {
                "issue_date": "7月1日",
                "editor": "周雷",
                "contact": "0755-89959999-87796",
            },
            "output_dir": str(tmp_path),
        },
        tools=gateway,
    )

    assert result.title == "上一稿简报标题"
    assert result.body == "上一稿第一段。\n\n上一稿第二段。"
    assert result.document_metadata == {
        "issue_date": "7月21日",
        "issue_number": "18",
        "editor": "张三",
        "contact": "0755-89959999-87796",
    }
    assert Path(result.output_file).is_file()


def test_signer_only_revision_is_rejected_without_calling_model(tmp_path: Path):
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: (_ for _ in ()).throw(
                AssertionError("signer-only request must not call the model")
            )
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": "签发人改成张三",
            "previous_title": "上一稿简报标题",
            "previous_body": "上一稿正文。",
            "previous_sources": [],
            "previous_document_metadata": {"issue_number": "18"},
            "output_dir": str(tmp_path),
        },
        tools=gateway,
    )

    assert result.output_file == ""
    assert result.document_metadata == {"issue_number": "18"}
    assert result.message_only is True
    assert "签发人" in result.message
    assert "固定" in result.message
    platform_result = PlatformResult(
        skill_id="writer1",
        output={
            "title": result.title,
            "body": result.body,
            "message_only": True,
        },
        needs_clarification=False,
        message=result.message,
    )
    assert format_text_reply(platform_result) == "签发人为模板固定项，未作修改。"


def test_runtime_and_conversation_preserve_document_metadata(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "skills.writer1.workflow.run",
        lambda inputs, tools: BriefResult(
            title="简报标题",
            body="简报正文",
            sources=[],
            document_metadata={"issue_number": "18", "editor": "张三"},
        ),
    )
    runtime = PlatformRuntime(registry=SkillRegistry.from_directory(Path("skills")), tools={})
    result = runtime.run(
        RoutedRequest(
            skill_id="writer1",
            confidence=1.0,
            needs_clarification=False,
            message="",
            inputs={},
        )
    )
    assert result.output["document_metadata"] == {"issue_number": "18", "editor": "张三"}

    store = ConversationStore(tmp_path / "conversations")
    store.record_result(
        channel="wecom",
        sender_userid="user-001",
        job_id="job-001",
        result=result,
    )
    conversation = store.get_active_conversation(channel="wecom", sender_userid="user-001")
    assert conversation is not None
    assert conversation.current_draft.document_metadata == {
        "issue_number": "18",
        "editor": "张三",
    }


def test_platform_followup_updates_brief_metadata_and_reuses_previous_values(tmp_path: Path):
    model_calls = 0

    def writer(_payload):
        nonlocal model_calls
        model_calls += 1
        return {
            "title": "微众银行持续提升普惠金融服务质效",
            "body": _compliant_body(),
        }

    app = PlatformApp(
        registry=SkillRegistry.from_directory(Path("skills")),
        tools={
            "web_reader": lambda url: {
                "title": "简报素材",
                "text": "微众银行持续提升普惠金融服务质效。",
                "url": url,
            },
            "bank_materials": lambda user_instruction, materials, limit=3: [],
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": writer,
        },
        job_store=JobStore(tmp_path / "jobs"),
        conversation_store=ConversationStore(tmp_path / "conversations"),
        task_relation_service=TaskRelationService(
            TaskRelationRepository(tmp_path / "task-relations.sqlite3")
        ),
        access_policy=AccessPolicy.allow_all_for_skills(["writer1"]),
    )

    first = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text=(
            "请根据这个链接写简报并输出Word，日期写7月21日，期号第18期："
            "https://example.com/brief"
        ),
    )
    assert first.output["document_metadata"] == {
        "issue_date": "7月21日",
        "issue_number": "18",
    }
    calls_after_first = model_calls

    revised = app.handle_text_message(
        channel="wecom",
        sender_userid="user-001",
        text="责任编辑改成张三，联系电话改为0755-89950000",
    )

    assert model_calls == calls_after_first
    assert revised.output["document_metadata"] == {
        "issue_date": "7月21日",
        "issue_number": "18",
        "editor": "张三",
        "contact": "0755-89950000",
    }
    assert Path(str(revised.output["output_file"])).is_file()
