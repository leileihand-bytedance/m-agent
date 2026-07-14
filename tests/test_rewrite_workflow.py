from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.platform.tools import ToolGateway
from skills.rewrite.workflow import run


def test_rewrite_workflow_polishes_inline_text():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "",
                "body": "润色后的正文内容。",
                "revision_note": "调整了表述，并把语气改得更正式。",
            },
        },
    )

    result = run(
        inputs={
            "text": "帮我把下面这段更正式一点：这段话现在有点口语化，但是核心意思先保留。",
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.body == "润色后的正文内容。"
    assert result.revision_note == "调整了表述，并把语气改得更正式。"
    assert seen_payloads[0]["task"] == "rewrite"
    assert seen_payloads[0]["materials"][0]["source"] == "user_text"
    assert "这段话现在有点口语化" in seen_payloads[0]["materials"][0]["text"]


def test_rewrite_workflow_asks_for_source_text_when_missing():
    gateway = ToolGateway(allowed_tools=("llm_writer",), tools={})

    result = run(inputs={"text": "帮我润色一下"}, tools=gateway)

    assert result.needs_clarification is True
    assert "原文" in result.message


def test_rewrite_workflow_accepts_material_before_request():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "",
                "body": "润色后的正文内容。",
                "revision_note": "我按新贴原文做了整体润色。",
            },
        },
    )

    result = run(
        inputs={
            "text": (
                "县域经济作为国民经济的基本单元，是国家推动乡村振兴的重要切入点。"
                "微众银行持续完善县域金融服务供给。\n\n帮我整体润色一下"
            )
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.body == "润色后的正文内容。"
    assert seen_payloads[0]["instruction"] == "帮我整体润色一下"
    assert "县域经济作为国民经济的基本单元" in seen_payloads[0]["materials"][0]["text"]


def test_rewrite_workflow_revises_previous_result():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "",
                "body": "第二版润色正文。",
                "revision_note": "进一步压缩了句子，保留原意。",
            },
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": "再简洁一点，第二句不要这么硬。",
            "previous_title": "",
            "previous_body": "第一版润色正文。",
            "previous_sources": [],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert result.body == "第二版润色正文。"
    assert seen_payloads[0]["revision"] is True
    assert seen_payloads[0]["materials"][0]["source"] == "previous_draft"
    assert "第一版润色正文" in seen_payloads[0]["materials"][0]["text"]


def test_rewrite_workflow_rejects_links_and_files_in_v1():
    gateway = ToolGateway(allowed_tools=("llm_writer",), tools={})

    result = run(
        inputs={
            "text": "帮我润色下面链接：https://example.com/article",
            "urls": ["https://example.com/article"],
            "files": ["/tmp/material.docx"],
        },
        tools=gateway,
    )

    assert result.needs_clarification is True
    assert "直接粘贴" in result.message


def test_rewrite_workflow_drops_model_generated_title_and_sources():
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: {
                "title": "模型擅自生成的标题",
                "body": "润色后的正文。",
                "sources": ["https://example.com/not-allowed"],
            }
        },
    )

    result = run(
        inputs={"text": "帮我润色这段：这是一段需要优化表达的原文。"},
        tools=gateway,
    )

    assert result.title == ""
    assert result.sources == []
