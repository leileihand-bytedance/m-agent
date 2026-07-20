from app.platform.tools import ToolGateway
from skills.brief_revision import (
    apply_revision_constraints,
    build_revision_plan,
    validate_revision_result,
)
from skills.writer1.schema import BriefRevisionPlanResult


PREVIOUS_TITLE = "微众银行持续提升科技管理效能"
PREVIOUS_BODY = (
    "第一段介绍行业趋势和总体背景。\n\n"
    "第二段介绍原有技术平台建设情况，服务已覆盖120个场景。\n\n"
    "第三段介绍下一步工作安排。"
)


def test_explicit_second_paragraph_revision_preserves_title_and_other_paragraphs():
    plan = build_revision_plan(
        "第二段要先讲外部AI发展趋势，再讲我行结合技术积累打造工程化平台，其他不变",
        previous_title=PREVIOUS_TITLE,
        previous_body=PREVIOUS_BODY,
    )
    generated_body = (
        "模型改写了第一段。\n\n"
        "第二段改为介绍外部AI趋势、长期积累和工程化平台。\n\n"
        "模型也改写了第三段。"
    )

    title, body = apply_revision_constraints(
        plan,
        previous_title=PREVIOUS_TITLE,
        previous_body=PREVIOUS_BODY,
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
    plan = build_revision_plan(
        "只改标题，正文不要动",
        previous_title=PREVIOUS_TITLE,
        previous_body=PREVIOUS_BODY,
    )

    title, body = apply_revision_constraints(
        plan,
        previous_title=PREVIOUS_TITLE,
        previous_body=PREVIOUS_BODY,
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

    plan = build_revision_plan(
        "把技术积累和外部趋势的关系讲清楚，别动其他部分",
        previous_title=PREVIOUS_TITLE,
        previous_body=PREVIOUS_BODY,
        tools=gateway,
        skill_id="writer1",
    )

    assert plan.scope == "paragraph"
    assert plan.target_paragraphs == [2]
    assert seen_payloads[0]["output_type"] is BriefRevisionPlanResult
    assert seen_payloads[0]["task"] == "writer1_revision_plan"


def test_revision_target_length_and_number_preservation_are_checked():
    plan = build_revision_plan(
        "全文压缩到700字左右，但不要改变事实和数据",
        previous_title=PREVIOUS_TITLE,
        previous_body=PREVIOUS_BODY,
    )
    revised_body = "深圳前海微众银行（以下简称“我行”）服务已覆盖150个场景。" + "继续推进相关工作。" * 80

    violations = validate_revision_result(
        plan,
        previous_title=PREVIOUS_TITLE,
        previous_body=PREVIOUS_BODY,
        revised_title=PREVIOUS_TITLE,
        revised_body=revised_body,
    )

    rules = {item.rule for item in violations}
    assert "revision-target-length" in rules
    assert "revision-number-preservation" in rules
