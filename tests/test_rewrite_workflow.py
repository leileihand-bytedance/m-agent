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


def test_rewrite_paragraph_revision_preserves_unmentioned_paragraphs():
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda _payload: {
                "title": "",
                "body": "模型改了第一段。\n\n新的第二段。\n\n模型改了第三段。",
                "revision_note": "调整了第二段。",
            }
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": "只改第二段，其他不变",
            "previous_title": "",
            "previous_body": "原第一段。\n\n原第二段。\n\n原第三段。",
        },
        tools=gateway,
    )

    assert result.body == "原第一段。\n\n新的第二段。\n\n原第三段。"
    assert result.revision_plan["scope"] == "paragraph"
    assert result.revision_plan["target_paragraphs"] == [2]


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


def test_rewrite_workflow_adds_bank_references_for_webank_material():
    calls = []
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("bank_materials", "llm_writer"),
        tools={
            "bank_materials": lambda user_instruction, materials, limit=3: calls.append(
                {
                    "user_instruction": user_instruction,
                    "materials": materials,
                    "limit": limit,
                }
            )
            or [
                {
                    "title": "微业贷标准表述",
                    "text": "来源文件：正式简介；微众银行素材摘录：微业贷是微众银行服务小微企业的产品。",
                    "url": "bank://product-1",
                    "source": "bank_knowledge",
                }
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "",
                "body": "微众银行持续优化微业贷服务体验。",
                "revision_note": "规范了机构和产品表述。",
            },
        },
    )

    result = run(
        inputs={"text": "帮我润色这段：微众银行持续优化微业贷服务体验。"},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert len(calls) == 1
    assert calls[0]["limit"] == 3
    assert calls[0]["materials"][0]["source"] == "user_text"
    assert [item["source"] for item in seen_payloads[0]["materials"]] == [
        "user_text",
        "bank_knowledge",
    ]
    assert seen_payloads[0]["materials"][1]["material_role"] == "verification_reference"
    assert "只用于核对机构名称、产品名称和已有标准表述" in seen_payloads[0]["instruction"]
    assert "不得把复核语料中原文没有的任何内容写入正文" in seen_payloads[0]["instruction"]
    assert "复核通过不等于允许补充" in seen_payloads[0]["instruction"]


def test_rewrite_workflow_skips_bank_references_for_unrelated_material():
    calls = []
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("bank_materials", "llm_writer"),
        tools={
            "bank_materials": lambda **kwargs: calls.append(kwargs) or [],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {"title": "", "body": "会议将于明日上午举行。", "revision_note": "调整了语序。"},
        },
    )

    result = run(
        inputs={"text": "帮我润色这段：会议安排在明天上午举行，请大家准时参加。"},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert calls == []
    assert [item["source"] for item in seen_payloads[0]["materials"]] == ["user_text"]
    assert "微众银行语料使用约束" not in seen_payloads[0]["instruction"]


def test_rewrite_workflow_continues_when_bank_materials_tool_is_unavailable():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "",
                "body": "微众银行持续优化服务体验。",
                "revision_note": "调整了表达。",
            }
        },
    )

    result = run(
        inputs={"text": "帮我润色这段：微众银行持续优化服务体验。"},
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert [item["source"] for item in seen_payloads[0]["materials"]] == ["user_text"]


def test_rewrite_workflow_adds_bank_references_when_revising_webank_text():
    calls = []
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("bank_materials", "llm_writer"),
        tools={
            "bank_materials": lambda user_instruction, materials, limit=3: calls.append(materials)
            or [
                {
                    "title": "微众银行标准表述",
                    "text": "微众银行素材摘录：微众银行是国内首家数字银行。",
                    "url": "bank://profile-1",
                    "source": "bank_knowledge",
                }
            ],
            "llm_writer": lambda payload: seen_payloads.append(payload)
            or {
                "title": "",
                "body": "微众银行持续提升数字化服务能力。",
                "revision_note": "进一步收紧了表达。",
            },
        },
    )

    result = run(
        inputs={
            "revision": True,
            "revision_request": "再正式一点。",
            "previous_title": "",
            "previous_body": "微众银行持续提升数字化服务能力。",
            "previous_sources": [],
        },
        tools=gateway,
    )

    assert result.needs_clarification is False
    assert calls[0][0]["source"] == "previous_draft"
    assert [item["source"] for item in seen_payloads[0]["materials"]] == [
        "previous_draft",
        "bank_knowledge",
    ]
    assert seen_payloads[0]["materials"][1]["material_role"] == "verification_reference"
    assert "微众银行语料使用约束" in seen_payloads[0]["instruction"]
