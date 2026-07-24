from app.platform.revision import (
    RevisionEngine,
    RevisionPlan,
    RevisionPolicy,
)
from app.platform.tools import ToolGateway


PREVIOUS_TITLE = "微众银行持续提升科技管理效能"
PREVIOUS_BODY = (
    "第一段介绍行业趋势和总体背景。\n\n"
    "第二段介绍原有技术平台建设情况，服务已覆盖120个场景。\n\n"
    "第三段介绍下一步工作安排。"
)


def test_explicit_second_paragraph_revision_preserves_title_and_other_paragraphs():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": "第二段要先讲外部AI发展趋势，再讲我行结合技术积累打造工程化平台，其他不变",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="writer1",
    )
    generated_body = (
        "模型改写了第一段。\n\n"
        "第二段改为介绍外部AI趋势、长期积累和工程化平台。\n\n"
        "模型也改写了第三段。"
    )

    title, body = prepared.apply(
        generated_title="模型擅自修改的标题",
        generated_body=generated_body,
    )

    assert title == PREVIOUS_TITLE
    assert body.split("\n\n") == [
        PREVIOUS_BODY.split("\n\n")[0],
        generated_body.split("\n\n")[1],
        PREVIOUS_BODY.split("\n\n")[2],
    ]


def test_title_only_revision_keeps_body_byte_for_byte():
    prepared = RevisionEngine().prepare(
        {
            "revision_request": "只改标题，正文不要动",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="writer1",
    )

    title, body = prepared.apply(
        generated_title="微众银行依托AI工程化平台提升科技管理效能",
        generated_body="模型重写了全文。",
    )

    assert title == "微众银行依托AI工程化平台提升科技管理效能"
    assert body == PREVIOUS_BODY


def test_semantic_revision_planner_is_used_for_natural_language_actions():
    seen_payloads = []
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
                "required_changes": ["补充外部AI发展趋势"],
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
        skill_id="writer1",
        tools=gateway,
    )

    assert prepared.plan.scope == "paragraph"
    assert prepared.plan.target_paragraphs == [2]
    assert seen_payloads[0]["output_type"] is RevisionPlan
    assert seen_payloads[0]["task"] == "writer1_revision_plan"


def test_revision_target_length_and_number_preservation_are_checked():
    prepared = RevisionEngine(
        policy=RevisionPolicy(min_target_length=100, max_target_length=1200)
    ).prepare(
        {
            "revision_request": "全文压缩到700字左右，但不要改变事实和数据",
            "previous_title": PREVIOUS_TITLE,
            "previous_body": PREVIOUS_BODY,
        },
        skill_id="writer1",
    )
    revised_body = "深圳前海微众银行（以下简称“我行”）服务已覆盖150个场景。" + "继续推进相关工作。" * 80

    violations = prepared.validate(
        revised_title=PREVIOUS_TITLE,
        revised_body=revised_body,
    )

    rules = {item.rule for item in violations}
    assert "revision-target-length" in rules
    assert "revision-number-preservation" in rules
