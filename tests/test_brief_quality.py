from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.brief_quality import (
    assess_multi_source_relation,
    build_brief_plan,
    check_brief_subject_name,
    check_brief_title_format,
    check_no_list_style,
    validate_brief_deterministic,
)


def test_build_brief_plan_guides_single_source_brief_away_from_news_style():
    plan = build_brief_plan(
        "请根据链接写简报",
        [
            {
                "title": "微众银行优化小微企业融资服务",
                "text": "近日，微众银行围绕小微企业融资需求持续完善数字化服务机制。截至2026年6月，相关服务已覆盖超180万户。",
                "url": "https://example.com/news",
            }
        ],
        multi_source=False,
    )

    assert "写作类型：单素材简报" in plan
    assert "不要沿用新闻稿或通稿写法" in plan
    assert "核心主线" in plan
    assert "优先写入数据" in plan


def test_assess_multi_source_relation_marks_obviously_unrelated_materials_as_weak():
    result = assess_multi_source_relation(
        [
            {
                "title": "微众银行优化小微企业融资服务",
                "text": "微众银行通过数字化方式提升小微企业融资服务效率。",
            },
            {
                "title": "微众银行举办员工羽毛球比赛",
                "text": "微众银行组织员工开展羽毛球比赛，丰富员工业余生活。",
            },
        ]
    )

    assert result["relation"] == "weak"
    assert "建议拆分" in result["message"]


def test_brief_title_connector_rule_matches_direct_report_style():
    assert check_brief_title_format("微众银行提升小微企业金融服务质效") == []

    violations = check_brief_title_format("微众银行提升小微企业金融服务质效 服务实体经济")
    assert len(violations) == 1
    assert violations[0].rule == "title-format"
    assert "逗号或冒号" in violations[0].suggestion


def test_brief_subject_name_requires_full_name_then_our_bank():
    clean_body = "深圳前海微众银行（以下简称“我行”）围绕小微企业融资需求持续完善数字化服务能力。后文我行将继续提升服务质效。"
    assert check_brief_subject_name(clean_body) == []

    violations = check_brief_subject_name("微众银行（以下简称“我行”）围绕小微企业融资需求持续完善数字化服务能力。")
    assert len(violations) == 1
    assert violations[0].rule == "brief-subject-name"
    assert "深圳前海微众银行" in violations[0].suggestion


def test_brief_forbids_list_style_body_structure():
    violations = check_no_list_style("深圳前海微众银行（以下简称“我行”）持续完善服务能力。\n1. 推出新产品。\n2. 优化审批流程。")
    assert len(violations) == 1
    assert violations[0].rule == "no-list-style"


def test_validate_brief_deterministic_collects_multiple_rules():
    violations = validate_brief_deterministic(
        "微众银行提升小微企业金融服务质效 服务实体经济",
        "微众银行（以下简称“我行”）持续完善服务能力。\n1. 推出新产品。\n2. 优化审批流程。",
    )

    rules = {violation.rule for violation in violations}
    assert "title-format" in rules
    assert "brief-subject-name" in rules
    assert "no-list-style" in rules
