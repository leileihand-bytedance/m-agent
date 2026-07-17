from datetime import date, datetime

import pytest

from skills.internal_weekly.schema import (
    FrontierSelection,
    MarketContextEvidence,
    MarketEvidenceBundle,
    MarketSeriesEvidence,
    WebCandidate,
)
from skills.internal_weekly.selection import (
    build_market_item,
    calculate_weekly_window,
    classify_section,
    validate_frontier_selection,
)
from skills.internal_weekly.source_policy import candidate_allowed


def _series(
    scope: str,
    code: str,
    name: str,
    start_date: str,
    end_date: str,
    start_close: float,
    end_close: float,
) -> MarketSeriesEvidence:
    return MarketSeriesEvidence(
        scope=scope,
        index_code=code,
        index_name=name,
        start_date=start_date,
        end_date=end_date,
        start_close=start_close,
        end_close=end_close,
        source_url="https://www.sse.com.cn/market/",
        source_title="官方市场数据",
        evidence_excerpt=f"{name} {start_close} {end_close}",
    )


def _complete_market_bundle() -> MarketEvidenceBundle:
    return MarketEvidenceBundle(
        series=[
            _series("weekly_a", "000001", "上证指数", "2026-07-03", "2026-07-10", 3500, 3535),
            _series("weekly_a", "399001", "深证成指", "2026-07-03", "2026-07-10", 11000, 10890),
            _series("weekly_a", "399006", "创业板指", "2026-07-03", "2026-07-10", 2300, 2346),
            _series("monday_a", "000001", "上证指数", "2026-07-10", "2026-07-13", 3535, 3560),
            _series("monday_a", "399001", "深证成指", "2026-07-10", "2026-07-13", 10890, 11000),
            _series("monday_a", "399006", "创业板指", "2026-07-10", "2026-07-13", 2346, 2390),
            _series("weekly_hk", "HSI", "恒生指数", "2026-07-03", "2026-07-10", 24000, 24240),
            _series("weekly_hk", "HSTECH", "恒生科技指数", "2026-07-03", "2026-07-10", 5200, 5096),
            _series("weekly_hk", "HSCEI", "恒生中国企业指数", "2026-07-03", "2026-07-10", 8700, 8787),
            _series("weekly_us", "DJIA", "道琼斯指数", "2026-07-03", "2026-07-10", 50000, 50500),
            _series("weekly_us", "COMP", "纳斯达克指数", "2026-07-03", "2026-07-10", 26000, 26520),
            _series("weekly_us", "SPX", "标普500指数", "2026-07-03", "2026-07-10", 7500, 7462.5),
        ],
        contexts=[
            MarketContextEvidence(
                scope="weekly_a",
                summary="银行板块表现较强，科技板块分化。",
                source_url="https://www.sse.com.cn/market/",
                source_title="官方市场数据",
                evidence_excerpt="银行板块表现较强，科技板块分化",
            )
        ],
    )


def test_calculate_weekly_window_uses_latest_monday_and_previous_monday_to_sunday():
    publication_date, period_start, period_end = calculate_weekly_window(
        datetime(2026, 7, 17, 10, 0)
    )

    assert publication_date == date(2026, 7, 13)
    assert period_start == date(2026, 7, 6)
    assert period_end == date(2026, 7, 12)


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("国务院常务会议召开", "国务院总理主持会议。", "党政要闻"),
        ("金融监管总局发布新规", "国家金融监督管理总局发布规定。", "监管动态"),
        ("数字银行发布经营数据", "网商银行公布年度经营数据。", "同业动向"),
        ("全球债市本周波动", "美债收益率和黄金价格出现变化。", "市场观察"),
    ],
)
def test_classify_section_copies_internal_weekly_taxonomy(title, body, expected):
    assert classify_section(title, body) == expected


def test_market_summary_has_fixed_position_content_and_code_calculated_returns():
    item, source_records = build_market_item(
        _complete_market_bundle(),
        publication_date=date(2026, 7, 13),
    )

    assert item.title == "资本市场综述"
    assert item.fixed_position == 1
    assert item.section == "市场观察"
    assert item.body.index("上周A股") < item.body.index("截至7月13日收盘")
    assert item.body.index("截至7月13日收盘") < item.body.index("上周港股")
    assert item.body.index("上周港股") < item.body.index("上周美股")
    assert "上证指数上涨1.00%" in item.body
    assert "深证成指下跌1.00%" in item.body
    assert len(source_records) == 1


def test_market_summary_rejects_missing_monday_a_group():
    bundle = _complete_market_bundle()
    bundle.series = [item for item in bundle.series if item.scope != "monday_a"]

    with pytest.raises(ValueError, match="monday_a"):
        build_market_item(bundle, publication_date=date(2026, 7, 13))


def test_market_summary_uses_code_owned_index_names_and_rejects_wrong_monday_date():
    bundle = _complete_market_bundle()
    bundle.series[0].index_name = "模型误写指数名"

    item, _ = build_market_item(bundle, publication_date=date(2026, 7, 13))

    assert "上证指数上涨1.00%" in item.body
    assert "模型误写指数名" not in item.body

    monday = next(item for item in bundle.series if item.scope == "monday_a")
    monday.end_date = "2026-07-12"
    with pytest.raises(ValueError, match="出版日"):
        build_market_item(bundle, publication_date=date(2026, 7, 13))


def test_market_summary_accepts_common_chinese_and_slash_date_formats():
    bundle = _complete_market_bundle()
    for item in bundle.series:
        if item.scope == "monday_a":
            item.start_date = "2026年7月10日"
            item.end_date = "7月13日"
        else:
            item.start_date = "2026/7/3"
            item.end_date = "2026年7月10日"

    item, _ = build_market_item(bundle, publication_date=date(2026, 7, 13))

    assert "截至7月13日收盘" in item.body


def test_frontier_selection_must_be_extract_from_report_body():
    report_body = "第一段说明利率传导机制。\n第二段分析银行净息差变化。\n第三段提示风险边界。"
    selection = FrontierSelection(
        source_url="https://www.bis.org/publ/work999.htm",
        title="利率传导与银行净息差",
        institution="国际清算银行",
        authors=["研究员甲"],
        publish_date="2026-07-09",
        selected_passages=["第二段分析银行净息差变化。", "第三段提示风险边界。"],
        source_location="网页摘要",
        reason="与银行经营相关",
    )

    validated = validate_frontier_selection(selection, report_body)

    assert validated == selection.selected_passages


def test_frontier_selection_rejects_model_written_passage():
    selection = FrontierSelection(
        source_url="https://www.bis.org/publ/work999.htm",
        title="利率传导与银行净息差",
        institution="国际清算银行",
        publish_date="2026-07-09",
        selected_passages=["这是模型自行概括的新观点。"],
        source_location="网页摘要",
        reason="与银行经营相关",
    )

    with pytest.raises(ValueError, match="逐字存在"):
        validate_frontier_selection(selection, "报告原文只有一段可核验内容。")


def test_source_policy_rejects_unlisted_domain_and_out_of_period_report():
    candidate = WebCandidate(
        url="https://blog.example/report",
        canonical_url="https://blog.example/report",
        title="研究报告",
        site="blog.example",
        publish_date="2026-07-09",
        body="这是一段长度足够但来源不符合白名单要求的研究报告正文。",
    )
    allowed, reason = candidate_allowed(
        candidate,
        period_start=date(2026, 7, 6),
        period_end=date(2026, 7, 12),
        require_research=True,
    )
    assert allowed is False
    assert "白名单" in reason

    candidate.url = candidate.canonical_url = "https://www.bis.org/publ/work999.htm"
    candidate.publish_date = "2026-07-01"
    allowed, reason = candidate_allowed(
        candidate,
        period_start=date(2026, 7, 6),
        period_end=date(2026, 7, 12),
        require_research=True,
    )
    assert allowed is False
    assert "统计期" in reason


def test_source_policy_accepts_common_chinese_publication_date():
    candidate = WebCandidate(
        url="https://www.bis.org/publ/work999.htm",
        canonical_url="https://www.bis.org/publ/work999.htm",
        title="研究报告",
        site="bis.org",
        publish_date="2026年7月9日",
        body="这是一段长度足够并且来源符合白名单要求的研究报告正文。",
    )

    allowed, reason = candidate_allowed(
        candidate,
        period_start=date(2026, 7, 6),
        period_end=date(2026, 7, 12),
        require_research=True,
    )

    assert allowed is True
    assert reason == ""
