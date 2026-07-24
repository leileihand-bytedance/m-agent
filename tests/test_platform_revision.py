from __future__ import annotations

from app.platform.revision import (
    RevisionEngine,
    RevisionPlan,
    RevisionPolicy,
)
from app.platform.tools import ToolGateway


PREVIOUS_TITLE = "上一稿标题"
PREVIOUS_BODY = (
    "第一段介绍总体背景。\n\n"
    "第二段介绍原有平台建设情况，服务已覆盖120个场景。\n\n"
    "第三段介绍下一步工作安排。"
)


def test_shared_revision_engine_preserves_body_for_title_only_request():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": "只改标题，正文不要动",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="direct_report",
    )

    title, body = prepared.apply(
        generated_title="修改后的标题",
        generated_body="模型擅自重写了全文。",
    )

    assert title == "修改后的标题"
    assert body == PREVIOUS_BODY
    assert prepared.plan.scope == "title"


def test_shared_revision_engine_maps_compact_generated_paragraphs_to_targets():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": "只改第二段和第三段，第一段不动",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="writer1",
    )

    title, body = prepared.apply(
        generated_title="模型改了标题",
        generated_body="新的第二段。\n\n新的第三段。",
    )

    assert title == PREVIOUS_TITLE
    assert body.split("\n\n") == [
        "第一段介绍总体背景。",
        "新的第二段。",
        "新的第三段。",
    ]


def test_shared_revision_engine_disables_title_scope_for_body_only_skill():
    prepared = RevisionEngine(
        policy=RevisionPolicy(supports_title=False)
    ).prepare(
        {
            "revision_request": "第二段再柔和一点，其他不变",
            "previous_title": "",
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="rewrite",
    )

    _title, body = prepared.apply(
        generated_title="不应使用的标题",
        generated_body="模型改了第一段。\n\n新的第二段。\n\n模型改了第三段。",
    )

    assert prepared.plan.scope == "paragraph"
    assert body.split("\n\n") == [
        "第一段介绍总体背景。",
        "新的第二段。",
        "第三段介绍下一步工作安排。",
    ]


def test_shared_revision_engine_uses_common_semantic_planner_protocol():
    seen_payloads: list[dict[str, object]] = []
    gateway = ToolGateway(
        allowed_tools=("llm_planner",),
        tools={
            "llm_planner": lambda payload: seen_payloads.append(payload)
            or {
                "scope": "paragraph",
                "target_paragraphs": [2],
                "preserve_title": True,
                "preserve_other_paragraphs": True,
                "target_length": None,
                "preserve_facts_and_numbers": True,
                "required_changes": ["把技术积累和外部趋势的关系讲清楚"],
                "must_remove": [],
            }
        },
    )

    prepared = RevisionEngine().prepare(
        {
            "revision_request": "把技术积累和外部趋势的关系讲清楚，别动其他部分",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="direct_report",
        tools=gateway,
    )

    assert prepared.plan.scope == "paragraph"
    assert prepared.plan.target_paragraphs == [2]
    assert seen_payloads[0]["output_type"] is RevisionPlan
    assert seen_payloads[0]["platform_prompt"] == "revision-plan"
    assert seen_payloads[0]["task"] == "direct_report_revision_plan"


def test_shared_revision_engine_validates_length_numbers_and_removal():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": (
                "全文压缩到700字左右，不要改变事实和数据，并删除“宣传性表述”"
            ),
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="writer1",
    )

    violations = prepared.validate(
        revised_title=PREVIOUS_TITLE,
        revised_body="服务已覆盖150个场景。宣传性表述。",
    )

    rules = {item.rule for item in violations}
    assert rules == {
        "revision-target-length",
        "revision-number-preservation",
        "revision-removal",
    }


def test_shared_revision_engine_builds_previous_draft_payload():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": "再正式一点",
            "previous_job_id": "job-001",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
            "previous_sources": ["https://example.com/source"],
        },
        skill_id="rewrite",
    )

    assert prepared.payload["revision"] is True
    assert prepared.payload["previous_job_id"] == "job-001"
    assert prepared.payload["materials"][0]["source"] == "previous_draft"
    assert prepared.sources == ("https://example.com/source",)


def test_shared_revision_engine_renders_title_and_no_split_constraints():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": "我的意思是修改标题就好，不需要把这一段拆成2部分",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="writer1",
    )

    instruction = str(prepared.payload["instruction"])

    assert "只修改标题" in instruction
    assert "不得拆分段落" in instruction
    assert "未被点名的段落必须原样保留" in instruction


def test_shared_revision_engine_requires_source_check_honesty():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": "这句话改变了原文的意思，修改一下",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="direct_report",
    )

    instruction = str(prepared.payload["instruction"])

    assert "不能声称已经核对原始素材" in instruction
    assert "无法确认原文时" in instruction


def test_shared_revision_engine_appends_constraints_to_existing_instruction():
    prepared = RevisionEngine().prepare(
        {
            "text": "请基于上一稿进行修改。\n用户新的修改要求：只改标题就好",
            "revision_request": "只改标题就好",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="direct_report",
    )

    instruction = str(prepared.payload["instruction"])

    assert "未被点名的段落必须原样保留" in instruction
    assert "只修改标题" in instruction


def test_shared_revision_engine_honors_negated_title_only_request():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": "不要只改标题，正文也要一起调整",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="writer1",
    )

    assert prepared.plan.scope == "whole"
