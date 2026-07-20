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
    BANK_TECH_SUBSIDIARY_ENTITIES,
    INTERNATIONAL_DIGITAL_BANK_ENTITIES,
    PENDING_MARKET_NOTICE,
    build_monday_market_update,
    build_market_item,
    calculate_weekly_window,
    classify_section,
    is_allowed_party_building_content,
    validate_frontier_selection,
)
from skills.internal_weekly.source_policy import candidate_allowed, domain_allowed_for_section
from skills.internal_weekly.source_registry import (
    load_source_registry,
    section_source_entry_urls,
    section_source_feed_urls,
    section_source_feed_specs,
)


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
        ("中共中央部署金融系统党的建设", "党中央作出重要部署。", "党政要闻"),
        ("国务院常务会议召开", "国务院总理主持会议。", "党政要闻"),
        ("金融监管总局发布新规", "国家金融监督管理总局发布规定。", "监管动态"),
        ("数字银行发布经营数据", "网商银行公布年度经营数据。", "同业动向"),
        ("全球债市本周波动", "美债收益率和黄金价格出现变化。", "市场观察"),
    ],
)
def test_classify_section_copies_internal_weekly_taxonomy(title, body, expected):
    assert classify_section(title, body) == expected


@pytest.mark.parametrize(
    "entity",
    ["Monzo", "Starling", "Revolut", "N26", "Nubank", "ZA Bank", "Mox Bank"],
)
def test_international_digital_banks_are_registered_as_peer_entities(entity):
    assert entity in INTERNATIONAL_DIGITAL_BANK_ENTITIES
    assert classify_section(f"{entity}发布经营进展", "披露数字银行经营和产品动态。") == "同业动向"


@pytest.mark.parametrize(
    "entity",
    ["建信金科", "工银科技", "兴业数金", "招银云创"],
)
def test_domestic_bank_technology_subsidiaries_are_registered_as_peer_entities(entity):
    assert entity in BANK_TECH_SUBSIDIARY_ENTITIES
    assert classify_section(f"{entity}发布新平台", "披露银行科技产品和经营动态。") == "同业动向"


def test_source_registry_keeps_official_references_for_expanded_peer_groups():
    registry = load_source_registry()
    peer_groups = registry["peer_entities"]

    for category in (
        "domestic_digital_banks",
        "international_digital_banks",
        "bank_technology_subsidiaries",
    ):
        assert peer_groups[category]
        assert all(item["official_domain"] for item in peer_groups[category])
        assert all(item["reference_url"].startswith("http") for item in peer_groups[category])


def test_party_source_registry_includes_state_council_news_list_entry():
    registry = load_source_registry()
    party_sources = registry["section_sources"]["党政要闻"]

    assert any(
        item.get("entry_url") == "https://www.gov.cn/yaowen/liebiao/"
        for item in party_sources
    )
    assert section_source_entry_urls("党政要闻") == (
        "https://www.gov.cn/yaowen/liebiao/",
    )
    assert section_source_feed_urls("党政要闻") == (
        "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json",
    )


def test_regulatory_source_registry_includes_user_designated_priority_entries():
    entry_urls = section_source_entry_urls("监管动态")
    feed_specs = section_source_feed_specs("监管动态")

    assert entry_urls == (
        "https://www.pbc.gov.cn/goutongjiaoliu/113456/113469/index.html",
        "https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=914&itemId=915&itemUrl=ItemListRightList.html&itemName=监管动态",
        "https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=914&itemId=917&itemUrl=ItemListRightList.html&itemName=政策解读&itemsubPId=916",
        "https://www.nfra.gov.cn/cn/view/pages/ItemList.html?itemPId=914&itemId=919&itemUrl=ItemListRightList.html&itemName=领导活动及讲话",
        "https://www.csrc.gov.cn/csrc/c100028/common_xq_list.shtml",
    )
    assert [item["source_group"] for item in feed_specs] == [
        "pbc",
        "nfra",
        "nfra",
        "nfra",
        "csrc",
    ]
    assert all(item["tier"] == "primary" for item in feed_specs)


def test_unknown_ordinary_content_is_not_misclassified_as_market_observation():
    assert classify_section("某地举办文化活动", "活动介绍与金融、银行和资本市场无关。") is None


@pytest.mark.parametrize(
    ("title", "body", "section", "expected"),
    [
        ("党中央部署推进党的建设", "中共中央部署党建重点工作。", "党政要闻", True),
        ("金融监管总局党委部署党建工作", "监管系统推进党的建设。", "监管动态", True),
        ("某部党组召开党建会议", "某部部署机关党的建设。", "党政要闻", False),
    ],
)
def test_party_building_only_keeps_party_central_and_financial_regulator_content(
    title, body, section, expected
):
    assert is_allowed_party_building_content(title, body, section) is expected


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


def test_market_summary_keeps_monday_placeholder_before_close():
    bundle = _complete_market_bundle()
    bundle.series = [item for item in bundle.series if item.scope != "monday_a"]

    item, source_records = build_market_item(
        bundle,
        publication_date=date(2026, 7, 13),
        monday_pending=True,
    )

    assert item.body.index("上周A股") < item.body.index(PENDING_MARKET_NOTICE)
    assert item.body.index(PENDING_MARKET_NOTICE) < item.body.index("上周港股")
    assert '<span style="color:#C00000;font-weight:700">' in item.body
    assert PENDING_MARKET_NOTICE in item.body
    assert len(source_records) == 1


def test_build_monday_market_update_only_uses_publication_day_a_share_data():
    bundle = _complete_market_bundle()
    bundle.series = [item for item in bundle.series if item.scope == "monday_a"]

    item, sources = build_monday_market_update(
        bundle,
        publication_date=date(2026, 7, 13),
    )

    assert item.title == "今日资本市场综述更新"
    assert item.content_mode == "market_update"
    assert "截至7月13日收盘，A股" in item.body
    assert "上周A股" not in item.body
    assert len(sources) == 1


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


def test_market_summary_accepts_source_reported_changes_without_reverse_calculation():
    scopes = {
        "weekly_a": (("000001", -1.17), ("399001", -3.53), ("399006", -4.41)),
        "monday_a": (("000001", -3.05), ("399001", -3.48), ("399006", -7.15)),
        "weekly_hk": (("HSI", 3.53), ("HSTECH", 4.94), ("HSCEI", 4.41)),
        "weekly_us": (("DJIA", -0.50), ("COMP", 1.74), ("SPX", 1.23)),
    }
    series = []
    for scope, values in scopes.items():
        for code, change in values:
            series.append(
                MarketSeriesEvidence(
                    scope=scope,
                    index_code=code,
                    index_name="来源中的名称不作为最终名称",
                    start_date="2026-07-13" if scope == "monday_a" else "2026-07-06",
                    end_date="2026-07-13" if scope == "monday_a" else "2026-07-10",
                    reported_change_pct=change,
                    source_url="https://qhweb.eastmoney.com/news/weekly.html",
                    source_title="一周市场回顾",
                    evidence_excerpt=f"{code} 本期变动 {change}%",
                )
            )

    item, _ = build_market_item(
        MarketEvidenceBundle(series=series),
        publication_date=date(2026, 7, 13),
    )

    assert "上证指数下跌1.17%" in item.body
    assert "创业板指下跌7.15%" in item.body
    assert "恒生科技指数上涨4.94%" in item.body
    assert "标普500指数上涨1.23%" in item.body


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


def test_frontier_selection_drops_exact_but_truncated_passage():
    complete = "This is a complete and traceable report sentence."
    truncated = "The current two-tier monetary architecture remains intermedia"
    selection = FrontierSelection(
        source_url="https://www.bis.org/publ/work999.htm",
        title="Digital money report",
        institution="BIS",
        publish_date="2026-07-09",
        selected_passages=[complete, truncated],
        source_location="网页正文",
        reason="与银行经营相关",
    )

    validated = validate_frontier_selection(
        selection,
        complete + "\n" + truncated,
    )

    assert validated == [complete]


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


def test_party_source_policy_accepts_central_portals_but_rejects_local_gov_subdomains():
    assert domain_allowed_for_section("https://www.gov.cn/meeting.htm", "党政要闻")
    assert domain_allowed_for_section("https://www.news.cn/politics/item.htm", "党政要闻")
    assert not domain_allowed_for_section(
        "https://gzw.hlj.gov.cn/local-party.htm",
        "党政要闻",
    )


def test_regulatory_source_policy_accepts_registered_financial_media_fallbacks():
    assert domain_allowed_for_section(
        "https://www.cnfin.com/hb-lb/detail/20260707/example.html",
        "监管动态",
    )
    assert domain_allowed_for_section(
        "https://www.stcn.com/article/detail/4002669.html",
        "监管动态",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://qhweb.eastmoney.com/news/weekly.html",
        "https://apnews.com/article/market-weekly",
        "https://www.cnfin.com/market/weekly.html",
        "https://www.sfccn.com/market/weekly.html",
        "https://news.bjd.com.cn/2026/07/13/11862144.shtml",
    ],
)
def test_source_policy_accepts_registered_market_report_domains(url):
    candidate = WebCandidate(
        url=url,
        canonical_url=url,
        title="一周市场回顾",
        site="market",
        publish_date="2026-07-10",
        body="本周股票市场主要指数涨跌情况完整列示，可用于人工核对。",
    )

    allowed, reason = candidate_allowed(candidate)

    assert allowed is True
    assert reason == ""
