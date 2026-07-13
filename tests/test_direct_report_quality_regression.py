import json
from pathlib import Path

from app.platform.registry import SkillRegistry
from app.platform.router import route_message
from skills.direct_report.guardrails import validate_deterministic


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "direct_report_quality_cases.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_quality_regression_fixture_contains_user_seed_links():
    fixture = _fixture()
    urls = {case["url"] for case in fixture["cases"]}

    assert urls == {
        "https://baijiahao.baidu.com/s?id=1869477855753615781&wfr=spider&for=pc",
        "https://www.cs.com.cn/yh/04/202306/t20230614_6350212.html?bsh_bid=5960157538",
        "https://www.yicai.com/news/102917116.html",
        "http://news.10jqka.com.cn/20250527/c668479924.shtml",
    }


def test_quality_regression_cases_route_to_direct_report():
    registry = SkillRegistry.from_directory(Path("skills"))

    for case in _fixture()["cases"]:
        route = route_message(case["user_input"], registry)
        assert route.skill_id == "direct_report"
        assert route.inputs["urls"] == [case["url"]]


def test_quality_regression_cases_have_manual_review_contract():
    required_dimensions = {
        "usable",
        "direct_report_style",
        "subject_centered",
        "policy_connection",
        "fact_accuracy",
        "revision_priority",
    }

    for case in _fixture()["cases"]:
        assert case["case_id"]
        assert case["material_type"] in {
            "financial_service",
            "risk_governance",
            "consumer_finance",
            "unknown_until_read",
        }
        assert set(case["manual_review"].keys()) == required_dimensions


def test_quality_regression_bad_examples_trigger_expected_hard_rules():
    for example in _fixture()["bad_output_examples"]:
        violations = validate_deterministic(
            title=example["title"],
            body=example["body"],
        )
        rules = {violation.rule for violation in violations}
        assert set(example["expected_rules"]).issubset(rules)
