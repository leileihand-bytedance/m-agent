from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.direct_report.guardrails import (
    check_body_length,
    check_direct_report_subject_name,
    check_no_standalone_case_paragraph,
    check_no_subheadings,
    check_title_format,
    validate_deterministic,
)


def test_title_must_contain_webank():
    assert check_title_format("微众银行推出外贸贷助力稳外贸") == []
    assert check_title_format("《微众银行推出外贸贷》") == []
    assert check_title_format('"微众银行推出外贸贷"') == []
    # 不要求以微众银行开头，只要包含即可
    assert check_title_format("稳外贸：微众银行推出外贸贷服务小微企业") == []
    assert check_title_format("微众银行推出外贸贷，助力外贸企业稳订单拓市场") == []


def test_title_without_webank_is_flagged():
    violations = check_title_format("名单制识别 批量化担保 助力外贸企业")
    assert len(violations) == 1
    assert violations[0].rule == "title-format"
    assert violations[0].severity == "hard"


def test_title_with_space_split_should_use_comma_or_colon():
    violations = check_title_format("微众银行名单制识别批量化担保 助力外贸企业稳订单")
    assert len(violations) == 1
    assert violations[0].rule == "title-format"
    assert "逗号或冒号" in violations[0].suggestion


def test_body_length_within_range_is_ok():
    body = "微众银行" + "通过数字化方式服务外贸企业。" * 50
    assert check_body_length(body) == []


def test_body_too_short_is_hard_violation():
    body = "微众银行推出外贸贷。"
    violations = check_body_length(body)
    assert len(violations) == 1
    assert violations[0].rule == "body-length"
    assert violations[0].severity == "hard"
    assert "700" in violations[0].message


def test_body_too_long_is_hard_violation():
    body = "微众银行" + "助力小微企业融资。" * 60
    violations = check_body_length(body)
    assert len(violations) == 1
    assert violations[0].rule == "body-length"
    assert violations[0].severity == "hard"


def test_subheading_patterns_are_flagged():
    for body in [
        "正文开头。\n一是推出产品。\n二是服务客户。",
        "正文开头。\n第一，推出产品。\n第二，服务客户。",
        "正文开头。\n1. 推出产品。\n2. 服务客户。",
        "正文开头。\n（一）推出产品。（二）服务客户。",
        "正文开头。\n## 小标题\n正文继续。",
    ]:
        violations = check_no_subheadings(body)
        assert violations, f"should flag: {body[:30]}"
        assert violations[0].rule == "no-subheadings"


def test_normal_text_is_not_flagged_as_subheading():
    assert check_no_subheadings("微众银行推出外贸贷，服务小微企业。") == []


def test_standalone_case_paragraph_is_flagged():
    body = "微众银行推出外贸贷。\n龙岗区一家电子消费品出口企业通过微众银行获批授信，及时补足备货资金。"
    violations = check_no_standalone_case_paragraph(body)
    assert len(violations) == 1
    assert violations[0].rule == "no-standalone-case-paragraph"


def test_case_as_supporting_evidence_is_allowed():
    body = "微众银行推出外贸贷，通过名单制识别和批量化担保机制服务外贸企业，龙岗区一家电子消费品出口企业即为受益企业之一。"
    assert check_no_standalone_case_paragraph(body) == []


def test_direct_report_must_use_short_subject_name_only():
    body = "深圳前海微众银行（以下简称“微众银行”）依托数字化能力服务小微企业。"
    violations = check_direct_report_subject_name(body)
    assert len(violations) == 1
    assert violations[0].rule == "direct-report-subject-name"
    assert violations[0].severity == "hard"
    assert "直接使用“微众银行”" in violations[0].suggestion


def test_direct_report_flags_wrong_full_bank_name():
    body = "深圳市微众银行股份有限公司持续完善数字化服务能力。"
    violations = check_direct_report_subject_name(body)
    assert len(violations) == 1
    assert violations[0].rule == "direct-report-subject-name"
    assert "微众银行" in violations[0].suggestion


def test_direct_report_does_not_use_our_bank_pronoun():
    body = "我行依托数字化能力服务小微企业。"
    violations = check_direct_report_subject_name(body)
    assert len(violations) == 1
    assert violations[0].rule == "direct-report-subject-name"


def test_direct_report_short_subject_name_is_allowed():
    body = "微众银行依托数字化能力服务小微企业。"
    assert check_direct_report_subject_name(body) == []


def test_validate_deterministic_collects_all_violations():
    title = "外贸贷助力稳外贸"
    body = "正文开头。\n一是推出产品。\n二是服务客户。"
    violations = validate_deterministic(title, body)
    rules = {v.rule for v in violations}
    assert "title-format" in rules
    assert "no-subheadings" in rules
    assert "body-length" in rules


def test_validate_deterministic_collects_direct_report_subject_name_violation():
    title = "稳外贸：微众银行推出外贸贷服务小微企业"
    body = "深圳前海微众银行（以下简称“微众银行”）" + "通过数字化方式服务外贸企业。" * 50
    violations = validate_deterministic(title, body)
    rules = {v.rule for v in violations}
    assert "direct-report-subject-name" in rules


def test_validate_deterministic_passes_clean_draft():
    title = "稳外贸：微众银行推出外贸贷服务小微企业"
    body = "微众银行" + "通过数字化方式服务外贸企业。" * 50
    violations = validate_deterministic(title, body)
    assert not violations
