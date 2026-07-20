import json
from pathlib import Path

from app.platform.tools import ToolGateway
from skills.brief_quality import (
    brief_case_profile,
    build_brief_plan,
    validate_brief_deterministic,
)
from skills.writer1.schema import BriefPlanResult


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "brief_quality_cases.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_brief_quality_fixture_covers_all_case_types_without_real_materials():
    fixture = _fixture()
    cases = fixture["cases"]

    assert {case["brief_type"] for case in cases} == {
        "综合成果型",
        "机制成果型",
        "产品工具型",
        "平台合作型",
        "标准引领型",
        "能力建设型",
        "外部认可型",
        "活动亮相型",
        "专项治理型",
    }
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert set(fixture["manual_review_dimensions"]) == {
        "usable",
        "reporting_value",
        "fact_accuracy",
        "type_fit",
        "structure",
        "brief_style",
        "revision_cost",
    }


def test_deterministic_planner_classifies_all_quality_cases():
    for case in _fixture()["cases"]:
        plan = build_brief_plan(
            case["instruction"],
            case["materials"],
            multi_source=case["multi_source"],
        )

        assert f"简报类型：{case['brief_type']}" in plan


def test_each_case_type_has_a_concrete_writing_card():
    for case in _fixture()["cases"]:
        profile = brief_case_profile(case["brief_type"])

        assert profile["must_cover"]
        assert profile["compress"]
        assert profile["structure"]


def test_semantic_planner_selects_facts_and_sections_from_the_ledger():
    seen_payloads = []
    gateway = ToolGateway(
        allowed_tools=("llm_planner",),
        tools={
            "llm_planner": lambda payload: seen_payloads.append(payload)
            or {
                "brief_type": "能力建设型",
                "core_message": "我行把外部AI发展趋势与长期技术积累转化为工程化能力",
                "audience_value": "展示我行金融科技能力建设的阶段性进展",
                "section_plan": ["趋势与基础", "平台建设", "应用成效"],
                "selected_fact_ids": ["F1", "F2"],
                "selected_data_ids": [],
                "excluded_details": ["会议流程"],
            },
        },
    )

    plan = build_brief_plan(
        "请围绕AI工程化平台写简报",
        [
            {"title": "趋势", "text": "外部人工智能技术加速发展，我行持续跟踪相关趋势。"},
            {"title": "平台", "text": "我行结合长期技术积累建设AI工程化平台。"},
        ],
        multi_source=True,
        tools=gateway,
        skill_id="writer2",
    )

    assert "语义策划：已完成" in plan
    assert "核心信息：我行把外部AI发展趋势与长期技术积累转化为工程化能力" in plan
    assert "结构安排：趋势与基础；平台建设；应用成效" in plan
    assert "语义类型卡必须覆盖：外部趋势或业务需求、已有基础、能力建设路径和应用成效" in plan
    assert "F1" in plan and "F2" in plan
    assert seen_payloads[0]["output_type"] is BriefPlanResult
    assert seen_payloads[0]["task"] == "writer2_plan"


def test_brief_length_over_1200_chars_is_a_hard_violation():
    body = "深圳前海微众银行（以下简称“我行”）" + "持续完善服务机制。" * 140

    violations = validate_brief_deterministic("微众银行持续完善服务机制", body)

    assert any(item.rule == "brief-length" and item.severity == "hard" for item in violations)


def test_numbers_not_found_in_materials_are_rejected():
    materials = [{"title": "服务进展", "text": "相关服务已覆盖120家企业。"}]
    body = "深圳前海微众银行（以下简称“我行”）持续完善服务机制，相关服务已覆盖150家企业。"

    violations = validate_brief_deterministic(
        "微众银行持续完善服务机制",
        body,
        materials=materials,
    )

    assert any(item.rule == "numeric-grounding" for item in violations)


def test_number_grounding_requires_an_exact_token_not_a_substring():
    materials = [{"title": "系统进展", "text": "相关系统版本150已发布。"}]
    body = "深圳前海微众银行（以下简称“我行”）持续完善服务机制，相关系统版本15已发布。"

    violations = validate_brief_deterministic(
        "微众银行持续完善服务机制",
        body,
        materials=materials,
    )

    assert any(item.rule == "numeric-grounding" for item in violations)


def test_unsupported_superlative_claim_is_rejected():
    materials = [{"title": "工具上线", "text": "我行推出面向小微企业的金融健康自测工具。"}]
    body = "深圳前海微众银行（以下简称“我行”）推出国内首个小微企业金融健康自测工具。"

    violations = validate_brief_deterministic(
        "微众银行推出金融健康自测工具",
        body,
        materials=materials,
    )

    assert any(item.rule == "claim-grounding" for item in violations)
