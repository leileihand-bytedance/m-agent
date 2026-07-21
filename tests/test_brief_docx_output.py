from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
import zipfile

from lxml import etree

from app.platform.models import PlatformResult
from app.platform.task_relations import (
    RelationAction,
    TaskCardStatus,
    TaskRelation,
    TaskRelationRepository,
    TaskRelationService,
)
from app.platform.tools import ToolGateway
from app.writing.task_execution import WritingTaskService
from skills.writer1.docx_output import (
    DEFAULT_TEMPLATE_PATH,
    generate_brief_docx,
    is_brief_docx_export_only,
    should_generate_brief_docx,
)
from skills.writer1.workflow import run


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONFIRMED_TEMPLATE_SHA256 = "c724de2ded541d90e094e32fa8c2e9be27ced36f05ec4309db2b9c83ea2a8c87"


def _document_paragraphs(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return [
        "".join(paragraph.itertext()).strip()
        for paragraph in root.xpath("//w:body/w:p", namespaces={"w": W})
    ]


def _document_paragraph_elements(path: Path) -> list[etree._Element]:
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return root.xpath("//w:body/w:p", namespaces={"w": W})


def _paragraph_text(paragraph: etree._Element) -> str:
    return "".join(paragraph.xpath(".//w:t/text()", namespaces={"w": W})).strip()


def _format_xml(paragraph: etree._Element, path: str) -> bytes:
    node = paragraph.find(path, namespaces={"w": W})
    return etree.tostring(node) if node is not None else b""


def test_brief_word_request_detection_distinguishes_export_only():
    assert should_generate_brief_docx("请写简报") is False
    assert should_generate_brief_docx("请写简报并输出Word") is True
    assert should_generate_brief_docx("生成正式文档") is True
    assert should_generate_brief_docx("请导出为 Word 文档") is True
    assert is_brief_docx_export_only("请把这篇简报导出Word") is True
    assert is_brief_docx_export_only("请输出 Word 文档") is True
    assert is_brief_docx_export_only("压缩第三段并输出Word") is False


def test_generate_brief_docx_replaces_title_and_body_only(tmp_path: Path):
    assert hashlib.sha256(DEFAULT_TEMPLATE_PATH.read_bytes()).hexdigest() == CONFIRMED_TEMPLATE_SHA256
    output = generate_brief_docx(
        title="微众银行持续提升普惠金融服务质效",
        body="第一段说明工作背景。\n\n第二段说明主要做法。\n第三段说明下一步安排。",
        output_dir=tmp_path,
    )

    assert output.is_file()
    assert output.parent == tmp_path
    paragraphs = _document_paragraphs(output)
    assert "微众银行持续提升普惠金融服务质效" in paragraphs
    assert "第一段说明工作背景。" in paragraphs
    assert "第二段说明主要做法。" in paragraphs
    assert "第三段说明下一步安排。" in paragraphs
    assert "标题" not in paragraphs
    assert "正文正文正文。" not in paragraphs
    assert "微众银行信息动态简报" in paragraphs
    assert "（2026年第XX期）" in paragraphs
    assert "（责任编辑：周雷，0755- 89959999-87796）" in paragraphs

    source_paragraphs = _document_paragraph_elements(DEFAULT_TEMPLATE_PATH)
    final_paragraphs = _document_paragraph_elements(output)
    source_title = next(item for item in source_paragraphs if _paragraph_text(item) == "标题")
    source_body = next(item for item in source_paragraphs if _paragraph_text(item) == "正文正文正文。")
    final_title = next(
        item for item in final_paragraphs if _paragraph_text(item) == "微众银行持续提升普惠金融服务质效"
    )
    final_body = [
        item
        for item in final_paragraphs
        if _paragraph_text(item)
        in {
            "第一段说明工作背景。",
            "第二段说明主要做法。",
            "第三段说明下一步安排。",
        }
    ]
    assert _format_xml(final_title, "w:pPr") == _format_xml(source_title, "w:pPr")
    assert _format_xml(final_title, "w:r/w:rPr") == _format_xml(source_title, "w:r/w:rPr")
    assert len(final_body) == 3
    for paragraph in final_body:
        assert _format_xml(paragraph, "w:pPr") == _format_xml(source_body, "w:pPr")
        assert _format_xml(paragraph, "w:r/w:rPr") == _format_xml(source_body, "w:r/w:rPr")

    with zipfile.ZipFile(DEFAULT_TEMPLATE_PATH) as source, zipfile.ZipFile(output) as final:
        assert set(source.namelist()) == set(final.namelist())
        for part in source.namelist():
            if part != "word/document.xml":
                assert final.read(part) == source.read(part), part


def test_writer1_generates_word_when_user_requests_formal_document(tmp_path: Path):
    gateway = ToolGateway(
        allowed_tools=("policy_materials", "llm_writer"),
        tools={
            "policy_materials": lambda user_instruction, materials, limit=3: [],
            "llm_writer": lambda payload: {
                "title": "微众银行持续提升普惠金融服务质效",
                "body": "深圳前海微众银行（以下简称“我行”）持续完善服务机制。\n\n未来，我行将继续提升服务质效。",
            },
        },
    )

    result = run(
        inputs={
            "text": "请写简报并输出正式文档：微众银行持续完善普惠金融服务机制，提升服务效率。",
            "output_dir": str(tmp_path),
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert Path(result.output_file).is_file()
    assert Path(result.output_file).parent == tmp_path


def test_writer1_export_only_revision_does_not_call_model(tmp_path: Path):
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: (_ for _ in ()).throw(
                AssertionError("export-only request must not call the model")
            )
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": "请把这篇简报导出Word",
            "previous_title": "上一稿简报标题",
            "previous_body": "上一稿第一段。\n\n上一稿第二段。",
            "previous_sources": ["https://example.com/source"],
            "output_dir": str(tmp_path),
        },
        tools=gateway,
    )

    assert result.title == "上一稿简报标题"
    assert result.body == "上一稿第一段。\n\n上一稿第二段。"
    assert result.sources == ["https://example.com/source"]
    assert Path(result.output_file).is_file()


def test_word_export_request_continues_selected_brief(tmp_path: Path):
    repository = TaskRelationRepository(tmp_path / "relations.sqlite3")
    repository.create_task(
        task_id="brief-task",
        channel="wecom",
        user_id="user-001",
        skill_id="writer1",
        title="普惠金融服务简报",
        status=TaskCardStatus.COMPLETED,
        current_job_id="job-001",
    )

    decision = TaskRelationService(repository).resolve_text(
        channel="wecom",
        user_id="user-001",
        text="请把这篇简报导出Word",
        route_skill_id=None,
    )

    assert decision.relation is TaskRelation.CONTINUE
    assert decision.target_task_id == "brief-task"
    assert decision.action is RelationAction.EXECUTE


def test_brief_word_delivery_sends_draft_text_then_attachment(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output_file = output_dir / "简报正式文档.docx"
    output_file.write_bytes(b"word-result")
    service = object.__new__(WritingTaskService)
    service._attachment_sender = object()
    workspace = SimpleNamespace(task_dir=tmp_path)

    items = service._build_delivery_items(
        workspace,
        PlatformResult(
            skill_id="writer1",
            output={
                "title": "简报标题",
                "body": "简报正文",
                "sources": [],
                "output_file": str(output_file),
            },
            needs_clarification=False,
            message="已生成简报正式文档。",
        ),
    )

    assert items[0]["kind"] == "text"
    assert items[0]["text"] == "简报标题\n\n简报正文"
    assert items[1]["kind"] == "attachment"
    assert items[1]["file"] == "output/简报正式文档.docx"
