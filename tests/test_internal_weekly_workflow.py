import json
from datetime import datetime
from pathlib import Path

import pytest

from app.platform.tools import ToolGateway
from skills.internal_weekly.schema import (
    ContentAssessmentBatch,
    ContentCandidateAssessment,
    FrontierSelection,
    MarketContextEvidence,
    MarketEvidenceBundle,
    MarketSeriesEvidence,
    WebCandidate,
)
from skills.internal_weekly.workflow import (
    _collect_pages,
    _collect_source_feed_pages,
    _evidence_in_body,
    _frontier_queries,
    _market_query_groups,
    _normalize_market_evidence_mode,
    _normalize_reported_market_period,
    _ordinary_items,
    _ordinary_query_groups,
    _resolve_market_evidence_excerpt,
    is_market_update_request,
    run,
)


def _market_series() -> list[MarketSeriesEvidence]:
    specs = [
        ("weekly_a", "000001", "上证指数", 3500, 3535),
        ("weekly_a", "399001", "深证成指", 11000, 10890),
        ("weekly_a", "399006", "创业板指", 2300, 2346),
        ("monday_a", "000001", "上证指数", 3535, 3560),
        ("monday_a", "399001", "深证成指", 10890, 11000),
        ("monday_a", "399006", "创业板指", 2346, 2390),
        ("weekly_hk", "HSI", "恒生指数", 24000, 24240),
        ("weekly_hk", "HSTECH", "恒生科技指数", 5200, 5096),
        ("weekly_hk", "HSCEI", "恒生中国企业指数", 8700, 8787),
        ("weekly_us", "DJIA", "道琼斯指数", 50000, 50500),
        ("weekly_us", "COMP", "纳斯达克指数", 26000, 26520),
        ("weekly_us", "SPX", "标普500指数", 7500, 7462.5),
    ]
    result = []
    for scope, code, name, start, end in specs:
        start_date = "2026-07-10" if scope == "monday_a" else "2026-07-03"
        end_date = "2026-07-13" if scope == "monday_a" else "2026-07-10"
        result.append(
            MarketSeriesEvidence(
                scope=scope,
                index_code=code,
                index_name=name,
                start_date=start_date,
                end_date=end_date,
                start_close=start,
                end_close=end,
                source_url="https://www.sse.com.cn/market/",
                source_title="市场数据",
                evidence_excerpt=f"{name} {start} {end}",
            )
        )
    return result


class FakeTools:
    def __init__(self):
        self.search_calls: list[str] = []
        self.search_limits: list[int] = []
        self.market_required_scopes: list[tuple[str, ...]] = []
        self.market_instructions: list[str] = []
        self.pages = {
            "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json": {
                "url": "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json",
                "canonical_url": "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json",
                "site": "gov.cn",
                "title": "中国政府网要闻列表",
                "publish_date": "",
                "date_extracted_from": "",
                "text": "国务院常务会议研究部署重点工作",
                "links": [
                    {
                        "url": "https://www.gov.cn/meeting.htm",
                        "title": "国务院常务会议研究部署重点工作",
                        "publish_date": "2026-07-08",
                    }
                ],
            },
            "https://www.gov.cn/meeting.htm": {
                "url": "https://www.gov.cn/meeting.htm",
                "canonical_url": "https://www.gov.cn/meeting.htm",
                "site": "gov.cn",
                "title": "国务院常务会议研究部署重点工作",
                "publish_date": "2026-07-08",
                "date_extracted_from": "meta:publishdate",
                "text": "国务院总理主持召开国务院常务会议，研究部署金融支持实体经济重点工作。会议提出提升政策协同效率。",
            },
            "https://www.pbc.gov.cn/rule.htm": {
                "url": "https://www.pbc.gov.cn/rule.htm",
                "canonical_url": "https://www.pbc.gov.cn/rule.htm",
                "site": "pbc.gov.cn",
                "title": "中国人民银行发布金融统计数据",
                "publish_date": "2026-07-09",
                "date_extracted_from": "meta:publishdate",
                "text": "中国人民银行发布金融统计数据。数据显示，金融对实体经济支持保持稳定，相关统计口径同步披露。",
            },
            "https://www.cs.com.cn/market.htm": {
                "url": "https://www.cs.com.cn/market.htm",
                "canonical_url": "https://www.cs.com.cn/market.htm",
                "site": "cs.com.cn",
                "title": "银行理财市场结构继续分化",
                "publish_date": "2026-07-10",
                "date_extracted_from": "meta:publishdate",
                "text": "银行理财市场结构继续分化，固收类产品收益中枢有所变化，多资产配置能力成为机构关注重点。",
            },
            "https://www.cmbyc.com/about/news/company-info/48.html": {
                "url": "https://www.cmbyc.com/about/news/company-info/48.html",
                "canonical_url": "https://www.cmbyc.com/about/news/company-info/48.html",
                "site": "cmbyc.com",
                "title": "招银云创发布全球司库数字化项目进展",
                "publish_date": "2026-07-10",
                "date_extracted_from": "meta:publishdate",
                "text": "招银云创发布全球司库数字化项目进展，披露跨境金融科技服务和标准化交付能力。",
            },
            "https://www.bis.org/publ/work999.htm": {
                "url": "https://www.bis.org/publ/work999.htm",
                "canonical_url": "https://www.bis.org/publ/work999.htm",
                "site": "bis.org",
                "title": "利率传导与银行净息差",
                "publish_date": "2026-07-09",
                "date_extracted_from": "meta:publishdate",
                "text": "报告研究利率变化如何影响银行资产负债结构。第二段分析银行净息差变化。第三段提示风险边界。",
            },
            "https://www.sse.com.cn/market/": {
                "url": "https://www.sse.com.cn/market/",
                "canonical_url": "https://www.sse.com.cn/market/",
                "site": "sse.com.cn",
                "title": "市场数据",
                "publish_date": "2026-07-13",
                "date_extracted_from": "page",
                "text": " ".join(item.evidence_excerpt for item in _market_series()),
            },
        }

    def search(self, query: str, max_results: int = 5):
        self.search_calls.append(query)
        self.search_limits.append(max_results)
        if "研究报告" in query:
            return [{"url": "https://www.bis.org/publ/work999.htm", "title": "利率传导与银行净息差"}]
        if "指数" in query or "A股" in query or "港股" in query or "美股" in query:
            return [{"url": "https://www.sse.com.cn/market/", "title": "市场数据"}]
        return [
            {"url": "https://www.gov.cn/meeting.htm", "title": "国务院常务会议研究部署重点工作"},
            {"url": "https://www.pbc.gov.cn/rule.htm", "title": "中国人民银行发布金融统计数据"},
            {"url": "https://www.cs.com.cn/market.htm", "title": "银行理财市场结构继续分化"},
            {
                "url": "https://www.cmbyc.com/about/news/company-info/48.html",
                "title": "招银云创发布全球司库数字化项目进展",
            },
        ]

    def web_reader(self, url: str):
        return self.pages[url]

    def llm_writer(self, payload):
        if payload["task"] == "internal_weekly_content_assessment":
            return ContentAssessmentBatch(
                items=[
                    ContentCandidateAssessment(
                        source_url="https://www.gov.cn/meeting.htm",
                        include=True,
                        section="党政要闻",
                        title="国务院常务会议研究部署重点工作",
                        summary="国务院常务会议研究部署金融支持实体经济重点工作，并提出提升政策协同效率。",
                        evidence_excerpt="研究部署金融支持实体经济重点工作",
                        score=9,
                        reason="全国性重要会议",
                    ),
                    ContentCandidateAssessment(
                        source_url="https://www.pbc.gov.cn/rule.htm",
                        include=True,
                        section="监管动态",
                        title="中国人民银行发布金融统计数据",
                        summary="中国人民银行发布金融统计数据，并同步披露相关统计口径。",
                        evidence_excerpt="中国人民银行发布金融统计数据",
                        score=9,
                        reason="金融监管部门动态",
                    ),
                    ContentCandidateAssessment(
                        source_url="https://www.cmbyc.com/about/news/company-info/48.html",
                        include=True,
                        section="同业动向",
                        title="招银云创发布全球司库数字化项目进展",
                        summary="招银云创披露跨境金融科技服务和标准化交付能力。",
                        evidence_excerpt="披露跨境金融科技服务和标准化交付能力",
                        score=8.5,
                        reason="银行科技子公司实质经营动态",
                    ),
                    ContentCandidateAssessment(
                        source_url="https://www.cs.com.cn/market.htm",
                        include=True,
                        section="市场观察",
                        title="银行理财市场结构继续分化",
                        summary="银行理财市场结构继续分化，多资产配置能力成为机构关注重点。",
                        evidence_excerpt="银行理财市场结构继续分化",
                        score=8,
                        reason="金融市场动态",
                    ),
                ]
            )
        if payload["task"] == "internal_weekly_market_extraction":
            required_scopes = tuple(payload["required_scopes"])
            self.market_required_scopes.append(required_scopes)
            self.market_instructions.append(payload["instruction"])
            return MarketEvidenceBundle(
                series=[
                    item for item in _market_series() if item.scope in required_scopes
                ]
            )
        if payload["task"] == "internal_weekly_frontier_selection":
            return FrontierSelection(
                source_url="https://www.bis.org/publ/work999.htm",
                title="利率传导与银行净息差",
                institution="国际清算银行",
                authors=["研究员甲"],
                publish_date="2026-07-09",
                selected_passages=["第二段分析银行净息差变化。", "第三段提示风险边界。"],
                source_location="网页摘要",
                reason="与银行资产负债管理相关",
            )
        raise AssertionError(payload["task"])


class InvalidOptionalMarketContextTools(FakeTools):
    def llm_writer(self, payload):
        result = super().llm_writer(payload)
        if (
            payload["task"] == "internal_weekly_market_extraction"
            and tuple(payload["required_scopes"]) == ("weekly_hk",)
        ):
            result.contexts = [
                MarketContextEvidence(
                    scope="weekly_hk",
                    summary="港股市场情绪有所回暖。",
                    source_url="https://www.sse.com.cn/market/",
                    source_title="市场数据",
                    evidence_excerpt="这是模型改写后、并不存在于页面中的背景句。",
                )
            ]
        return result


def test_workflow_outputs_traceable_review_bundle_without_word(tmp_path):
    fake = FakeTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": fake.search,
            "web_reader": fake.web_reader,
            "llm_writer": fake.llm_writer,
        },
    )
    output_dir = tmp_path / "output"

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 17, 10, 0),
            "output_dir": str(output_dir),
        },
        gateway,
    )

    assert result.needs_clarification is False
    assert result.ready_for_approval is True
    assert [section.name for section in result.sections] == [
        "党政要闻",
        "监管动态",
        "同业动向",
        "市场观察",
        "前沿观点",
    ]
    market_section = next(section for section in result.sections if section.name == "市场观察")
    assert market_section.items[0].title == "资本市场综述"
    frontier = next(section for section in result.sections if section.name == "前沿观点")
    assert frontier.items[0].content_mode == "report_extract"
    assert "第二段分析银行净息差变化。" in frontier.items[0].body
    assert "这是模型" not in frontier.items[0].body
    assert result.draft_version
    assert result.output_file.endswith(".md")
    assert not list(output_dir.glob("*.docx"))

    review_text = Path(result.output_file).read_text(encoding="utf-8")
    assert "内容核对稿" in review_text
    assert "原文链接" in review_text
    assert "报告位置：网页摘要" in review_text

    manifest = json.loads(Path(result.manifest_file).read_text(encoding="utf-8"))
    assert manifest["draft_version"] == result.draft_version
    assert manifest["ready_for_approval"] is True
    assert any(section["name"] == "市场观察" for section in manifest["sections"])
    assert all(record["url"] for record in manifest["source_records"])
    assert all(record["content_sha256"] for record in manifest["source_records"])
    assert any("中国政府网" in query for query in fake.search_calls)
    assert any("中国人民银行" in query for query in fake.search_calls)
    assert any("BIS" in query for query in fake.search_calls)
    assert all("site:" not in query for query in fake.search_calls)
    assert all(" OR " not in query for query in fake.search_calls)
    monday_queries = [
        query
        for query in fake.search_calls
        if "2026年7月13日" in query and ("A股收盘" in query or "A股收评" in query)
    ]
    assert len(monday_queries) == 2
    assert all("证券时报" not in query for query in monday_queries)
    bis_queries = [query for query in fake.search_calls if query.startswith("BIS")]
    assert len(bis_queries) >= 4
    assert any("latest bulletin" in query for query in bis_queries)
    assert any("Annual Economic Report 2026" in query for query in bis_queries)
    assert all(
        limit == 10
        for query, limit in zip(fake.search_calls, fake.search_limits, strict=True)
        if query.startswith("BIS") or query.startswith("IMF")
    )
    hk_queries = [query for query in fake.search_calls if "恒生中国企业指数" in query]
    assert len(hk_queries) == 2
    assert any("港股一周复盘" in query for query in hk_queries)
    assert all("2026年7月10日" in query for query in hk_queries)
    assert fake.market_required_scopes == [
        ("weekly_a",),
        ("monday_a",),
        ("weekly_hk",),
        ("weekly_us",),
    ]
    weekly_instructions = [
        instruction
        for scopes, instruction in zip(
            fake.market_required_scopes, fake.market_instructions, strict=True
        )
        if scopes[0].startswith("weekly_")
    ]
    assert all("网页发布日期不能当作行情结束日期" in item for item in weekly_instructions)


def test_workflow_drops_invalid_optional_market_context_but_keeps_fixed_summary(tmp_path):
    fake = InvalidOptionalMarketContextTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": fake.search,
            "web_reader": fake.web_reader,
            "llm_writer": fake.llm_writer,
        },
    )

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 17, 10, 0),
            "output_dir": str(tmp_path / "output"),
        },
        gateway,
    )

    assert result.ready_for_approval is True
    market_section = next(section for section in result.sections if section.name == "市场观察")
    assert market_section.items[0].title == "资本市场综述"
    assert "港股市场情绪有所回暖" not in market_section.items[0].body
    assert "行情背景句证据无法逐字核对，已排除：weekly_hk" in result.warnings


def test_workflow_generates_previous_week_before_monday_close_with_pending_marker(tmp_path):
    fake = FakeTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={"search": fake.search, "web_reader": fake.web_reader, "llm_writer": fake.llm_writer},
    )

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 13, 14, 0),
            "output_dir": str(tmp_path / "output"),
        },
        gateway,
    )

    assert result.needs_clarification is False
    assert result.ready_for_approval is False
    market_section = next(section for section in result.sections if section.name == "市场观察")
    assert "今日资本市场内容待收盘后更新" in market_section.items[0].body
    assert 'style="color:#C00000' in market_section.items[0].body
    assert "今日A股收盘数据待15:00收盘后更新" in result.warnings
    assert "暂不生成洁净版本" in result.message
    assert result.output_file.endswith(".md")
    assert all("A股收评" not in query for query in fake.search_calls)
    assert fake.market_required_scopes == [
        ("weekly_a",),
        ("weekly_hk",),
        ("weekly_us",),
    ]


def test_full_weekly_includes_monday_close_immediately_after_market_close(tmp_path):
    fake = FakeTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={"search": fake.search, "web_reader": fake.web_reader, "llm_writer": fake.llm_writer},
    )

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 13, 15, 1),
            "output_dir": str(tmp_path / "output"),
        },
        gateway,
    )

    market_section = next(section for section in result.sections if section.name == "市场观察")
    assert "截至7月13日收盘，A股" in market_section.items[0].body
    assert "今日资本市场内容待收盘后更新" not in market_section.items[0].body
    assert ("monday_a",) in fake.market_required_scopes


def test_market_update_request_before_close_returns_highlighted_placeholder_without_search(tmp_path):
    fake = FakeTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={"search": fake.search, "web_reader": fake.web_reader, "llm_writer": fake.llm_writer},
    )

    result = run(
        {
            "text": "生成一下今天的资本市场综述",
            "now": datetime(2026, 7, 13, 14, 0),
            "output_dir": str(tmp_path / "output"),
        },
        gateway,
    )

    assert result.generation_mode == "market_update"
    assert [section.name for section in result.sections] == ["市场观察"]
    assert result.sections[0].items[0].title == "今日资本市场综述更新"
    assert "今日资本市场内容待收盘后更新" in result.sections[0].items[0].body
    assert 'style="color:#C00000' in result.sections[0].items[0].body
    assert result.ready_for_approval is False
    assert fake.search_calls == []
    assert "今日资本市场更新" in Path(result.output_file).name


def test_market_update_request_after_close_only_collects_current_day_market(tmp_path):
    fake = FakeTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={"search": fake.search, "web_reader": fake.web_reader, "llm_writer": fake.llm_writer},
    )

    result = run(
        {
            "text": "生成一下今天的资本市场综述",
            "now": datetime(2026, 7, 13, 15, 1),
            "output_dir": str(tmp_path / "output"),
        },
        gateway,
    )

    assert result.generation_mode == "market_update"
    assert [section.name for section in result.sections] == ["市场观察"]
    assert result.sections[0].items[0].title == "今日资本市场综述更新"
    assert "截至7月13日收盘，A股" in result.sections[0].items[0].body
    assert fake.market_required_scopes == [("monday_a",)]
    assert all("研究报告" not in query for query in fake.search_calls)
    assert all("中国政府网" not in query for query in fake.search_calls)
    assert result.ready_for_approval is True


def test_internal_weekly_ordinary_queries_are_split_by_section_and_cover_expanded_peers():
    groups = dict(_ordinary_query_groups(datetime(2026, 7, 6).date(), datetime(2026, 7, 12).date()))

    assert set(groups) == {"党政要闻", "监管动态", "同业动向", "市场观察"}
    party_queries = groups["党政要闻"]
    regulatory_queries = groups["监管动态"]
    assert any(query.startswith("中国政府网") for query in party_queries)
    assert any(query.startswith("新华社") for query in party_queries)
    assert any(query.startswith("人民网") for query in party_queries)
    party_query_text = "\n".join(party_queries)
    for topic in (
        "宏观经济",
        "科技创新",
        "人工智能",
        "促进消费",
        "小微企业",
    ):
        assert topic in party_query_text
    assert any(query.startswith("中国人民银行") for query in regulatory_queries)
    assert any(query.startswith("国家金融监督管理总局") for query in regulatory_queries)
    assert any(query.startswith("中国证监会") for query in regulatory_queries)
    assert any(query.startswith("国家外汇管理局") for query in regulatory_queries)
    peer_queries = "\n".join(groups["同业动向"])
    assert "Monzo" in peer_queries
    assert "ZA Bank" in peer_queries
    assert "建信金科" in peer_queries
    assert "工银科技" in peer_queries


def test_party_assessment_instruction_covers_broad_management_relevance():
    page = WebCandidate(
        url="https://www.gov.cn/yaowen/liebiao/202607/content_123.htm",
        canonical_url="https://www.gov.cn/yaowen/liebiao/202607/content_123.htm",
        title="习近平出席世界人工智能大会并发表重要讲话",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-10",
        body=(
            "习近平出席世界人工智能大会并发表重要讲话，强调推动人工智能创新发展，"
            "促进科技创新和产业创新深度融合。"
        ),
    )
    captured: dict[str, object] = {}

    def llm_writer(payload):
        captured.update(payload)
        return ContentAssessmentBatch(
            items=[
                ContentCandidateAssessment(
                    source_url=page.canonical_url,
                    include=True,
                    section="党政要闻",
                    title=page.title,
                    summary="中央部署推动人工智能创新发展和科技产业融合。",
                    evidence_excerpt="强调推动人工智能创新发展",
                    score=9,
                    reason="与银行数字化经营和科技创新相关的中央部署",
                )
            ]
        )

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        [page],
        gateway,
        expected_section="党政要闻",
        retrieved_at="2026-07-19T12:00:00+08:00",
    )

    instruction = str(captured["instruction"])
    for topic in ("宏观经济", "科技创新", "人工智能", "促进消费", "小微企业"):
        assert topic in instruction
    assert "每一条候选都必须返回判断" in instruction
    assert [item.title for item in items] == [page.title]
    assert len(records) == 1
    assert warnings == []


def test_party_items_dedupe_same_event_and_prefer_formal_central_source():
    speech = WebCandidate(
        url="https://www.gov.cn/yaowen/liebiao/202607/speech.htm",
        canonical_url="https://www.gov.cn/yaowen/liebiao/202607/speech.htm",
        title="习近平：在国家科学技术奖励大会、两院院士大会、中国科协第十一次全国代表大会上的讲话",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-08",
        body="习近平发表重要讲话，强调加快推进高水平科技自立自强。",
    )
    commentary = WebCandidate(
        url="https://www.gov.cn/yaowen/liebiao/202607/commentary.htm",
        canonical_url="https://www.gov.cn/yaowen/liebiao/202607/commentary.htm",
        title="人民日报评论员：向着科技强国目标坚定迈进——论学习贯彻习近平总书记在国家科学技术奖励大会、两院院士大会、中国科协十一大上重要讲话",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-10",
        body="人民日报评论员文章阐释习近平总书记重要讲话，提出建设科技强国。",
    )
    decision = WebCandidate(
        url="https://www.gov.cn/yaowen/liebiao/202607/decision.htm",
        canonical_url="https://www.gov.cn/yaowen/liebiao/202607/decision.htm",
        title="中共中央 国务院关于2025年度国家科学技术奖励的决定",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-08",
        body="中共中央、国务院决定授予国家科学技术奖励，推动科技创新发展。",
    )

    def assessment(page, *, score, summary, evidence):
        return ContentCandidateAssessment(
            source_url=page.canonical_url,
            include=True,
            section="党政要闻",
            title=page.title,
            summary=summary,
            evidence_excerpt=evidence,
            score=score,
            reason="中央科技创新重要信息",
        )

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: ContentAssessmentBatch(
                items=[
                    assessment(
                        commentary,
                        score=10,
                        summary="评论员文章阐释科技强国部署。",
                        evidence="提出建设科技强国",
                    ),
                    assessment(
                        speech,
                        score=8,
                        summary="习近平强调推进高水平科技自立自强。",
                        evidence="加快推进高水平科技自立自强",
                    ),
                    assessment(
                        decision,
                        score=9,
                        summary="中央发布国家科学技术奖励决定。",
                        evidence="推动科技创新发展",
                    ),
                ]
            )
        },
    )

    items, records, warnings = _ordinary_items(
        [speech, commentary, decision],
        gateway,
        expected_section="党政要闻",
        retrieved_at="2026-07-19T12:00:00+08:00",
    )

    assert {item.title for item in items} == {speech.title, decision.title}
    assert len(records) == 2
    assert warnings == []


def test_ordinary_assessment_retries_when_model_omits_the_whole_batch():
    page = WebCandidate(
        url="https://www.gov.cn/yaowen/liebiao/202607/content_456.htm",
        canonical_url="https://www.gov.cn/yaowen/liebiao/202607/content_456.htm",
        title="中共中央 国务院作出国家科学技术奖励决定",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-08",
        body="中共中央 国务院作出国家科学技术奖励决定，推动科技创新发展。",
    )
    calls = 0

    def llm_writer(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ContentAssessmentBatch(items=[])
        return ContentAssessmentBatch(
            items=[
                ContentCandidateAssessment(
                    source_url=page.canonical_url,
                    include=True,
                    section="党政要闻",
                    title=page.title,
                    summary="中央作出国家科学技术奖励决定，推动科技创新发展。",
                    evidence_excerpt="推动科技创新发展",
                    score=9,
                    reason="中央科技创新重要部署",
                )
            ]
        )

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        [page],
        gateway,
        expected_section="党政要闻",
        retrieved_at="2026-07-19T12:00:00+08:00",
    )

    assert calls == 2
    assert [item.title for item in items] == [page.title]
    assert len(records) == 1
    assert warnings == []


def test_ordinary_assessment_reports_consecutive_empty_batches():
    page = WebCandidate(
        url="https://www.gov.cn/yaowen/liebiao/202607/content_789.htm",
        canonical_url="https://www.gov.cn/yaowen/liebiao/202607/content_789.htm",
        title="国务院部署促进消费重点工作",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-09",
        body="国务院部署促进消费重点工作。",
    )
    calls = 0

    def llm_writer(payload):
        nonlocal calls
        calls += 1
        return ContentAssessmentBatch(items=[])

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        [page],
        gateway,
        expected_section="党政要闻",
        retrieved_at="2026-07-19T12:00:00+08:00",
    )

    assert calls == 2
    assert items == []
    assert records == []
    assert warnings == ["党政要闻第1批候选评估连续返回空判断"]


def test_frontier_queries_cover_report_series_and_both_months_in_fallback_window():
    queries = _frontier_queries(
        datetime(2026, 6, 13).date(),
        datetime(2026, 7, 12).date(),
        fallback=True,
    )

    combined = "\n".join(queries)
    assert "BIS Annual Economic Report 2026" in combined
    assert "BIS report banking digital payments" in combined
    assert "June July 2026" in combined


def test_empty_optional_market_observation_uses_unambiguous_warning():
    gateway = ToolGateway(allowed_tools=(), tools={})

    items, records, warnings = _ordinary_items(
        [],
        gateway,
        expected_section="市场观察",
        retrieved_at="2026-07-19T12:00:00+08:00",
    )

    assert items == []
    assert records == []
    assert warnings == ["市场观察（资本市场综述以外）未找到合格候选材料"]


def test_detects_only_explicit_current_day_market_update_requests():
    assert is_market_update_request("生成一下今天的资本市场综述") is True
    assert is_market_update_request("更新今日资本市场综述") is True
    assert is_market_update_request("生成本周内参周报") is False


def test_collect_pages_filters_unlisted_search_results_before_web_reader():
    read_urls: list[str] = []

    def search(query: str, max_results: int = 5):
        return [
            {"url": "https://notice.example/tender.htm", "title": "无关招标页"},
            {"url": "https://www.gov.cn/meeting.htm", "title": "国务院常务会议"},
        ]

    def web_reader(url: str):
        read_urls.append(url)
        if "example" in url:
            raise RuntimeError("raw curl timeout details must stay internal")
        return FakeTools().pages[url]

    gateway = ToolGateway(
        allowed_tools=("search", "web_reader"),
        tools={"search": search, "web_reader": web_reader},
    )

    pages, warnings = _collect_pages(
        ["测试"],
        gateway,
        period_start=datetime(2026, 7, 6).date(),
        period_end=datetime(2026, 7, 12).date(),
    )

    assert read_urls == ["https://www.gov.cn/meeting.htm"]
    assert [page.canonical_url for page in pages] == ["https://www.gov.cn/meeting.htm"]
    assert warnings == []


def test_collect_pages_uses_section_source_policy_before_reading_local_government_pages():
    read_urls: list[str] = []

    def search(query: str, max_results: int = 5):
        return [
            {"url": "https://gzw.hlj.gov.cn/local-party.htm", "title": "地方部门党建"},
            {"url": "https://www.gov.cn/meeting.htm", "title": "国务院常务会议"},
        ]

    def web_reader(url: str):
        read_urls.append(url)
        return FakeTools().pages[url]

    gateway = ToolGateway(
        allowed_tools=("search", "web_reader"),
        tools={"search": search, "web_reader": web_reader},
    )

    pages, warnings = _collect_pages(
        ["测试"],
        gateway,
        period_start=datetime(2026, 7, 6).date(),
        period_end=datetime(2026, 7, 12).date(),
        source_section="党政要闻",
    )

    assert read_urls == ["https://www.gov.cn/meeting.htm"]
    assert [page.canonical_url for page in pages] == ["https://www.gov.cn/meeting.htm"]
    assert warnings == []


def test_collect_source_feed_pages_reads_only_in_period_relevant_party_links():
    feed_url = "https://www.gov.cn/yaowen/liebiao/YAOWENLIEBIAO.json"
    ai_url = "https://www.gov.cn/yaowen/liebiao/202607/content_ai.htm"
    read_urls: list[str] = []

    def web_reader(url: str):
        read_urls.append(url)
        if url == feed_url:
            return {
                "url": feed_url,
                "canonical_url": feed_url,
                "title": "中国政府网要闻列表",
                "publish_date": "",
                "text": "",
                "links": [
                    {
                        "url": ai_url,
                        "title": "习近平出席世界人工智能大会并发表重要讲话",
                        "publish_date": "2026-07-10",
                    },
                    {
                        "url": "https://www.gov.cn/yaowen/liebiao/202607/content_old.htm",
                        "title": "国务院部署宏观经济工作",
                        "publish_date": "2026-07-05",
                    },
                    {
                        "url": "https://www.gov.cn/yaowen/liebiao/202607/content_culture.htm",
                        "title": "地方文艺展演举行",
                        "publish_date": "2026-07-10",
                    },
                    {
                        "url": "https://example.com/untrusted.htm",
                        "title": "人工智能营销活动",
                        "publish_date": "2026-07-10",
                    },
                ],
            }
        assert url == ai_url
        return {
            "url": ai_url,
            "canonical_url": ai_url,
            "site": "gov.cn",
            "publisher": "中国政府网",
            "title": "习近平出席世界人工智能大会并发表重要讲话",
            "publish_date": "2026-07-10",
            "date_extracted_from": "meta:publishdate",
            "text": (
                "习近平出席世界人工智能大会并发表重要讲话，"
                "强调推动人工智能创新发展和产业创新深度融合。"
            ),
        }

    gateway = ToolGateway(
        allowed_tools=("web_reader",),
        tools={"web_reader": web_reader},
    )

    pages, warnings = _collect_source_feed_pages(
        [feed_url],
        gateway,
        period_start=datetime(2026, 7, 6).date(),
        period_end=datetime(2026, 7, 12).date(),
        source_section="党政要闻",
    )

    assert read_urls == [feed_url, ai_url]
    assert [page.canonical_url for page in pages] == [ai_url]
    assert warnings == []


def test_evidence_matching_ignores_invisible_web_layout_marks_only():
    body = (
        "截至7月10日收盘，\u200b恒生指数报收24175.12点，单周上涨3.53%\u200b；"
        "恒生科技指数周涨幅达4.94%。"
    )

    assert _evidence_in_body(
        "截至7月10日收盘，恒生指数报收24175.12点，单周上涨3.53%；",
        body,
    )
    assert not _evidence_in_body(
        "截至7月10日收盘，恒生指数报收24175.12点，单周上涨4.53%；",
        body,
    )


def test_market_evidence_recovers_exact_source_clause_by_index_and_change_value():
    body = (
        "截至7月10日收盘，\u200b恒生指数报收24175.12点，单周上涨3.53%\u200b；"
        "\u200b恒生中国企业指数上涨4.41%\u200b。"
    )
    evidence = MarketSeriesEvidence(
        scope="weekly_hk",
        index_code="HSCEI",
        index_name="恒生中国企业指数",
        start_date="2026-07-06",
        end_date="2026-07-10",
        reported_change_pct=4.41,
        source_url="https://www.sfccn.com/market/weekly.html",
        source_title="港股周评",
        evidence_excerpt="恒生中国企业指数周涨4.41%",
    )

    assert _resolve_market_evidence_excerpt(evidence, body) == (
        "\u200b恒生中国企业指数上涨4.41%\u200b。"
    )
    assert _resolve_market_evidence_excerpt(
        evidence.model_copy(update={"reported_change_pct": 5.41}),
        body,
    ) is None


@pytest.mark.parametrize(
    ("code", "model_name", "change", "body", "expected"),
    [
        (
            "DJIA",
            "道琼斯工业平均指数",
            -0.5,
            "The Dow is down 263.06 points, or 0.5%.",
            "The Dow is down 263.06 points, or 0.5%.",
        ),
        (
            "COMP",
            "纳斯达克综合指数",
            1.7,
            "The Nasdaq is up 448.93 points, or 1.7%.",
            "The Nasdaq is up 448.93 points, or 1.7%.",
        ),
        (
            "SPX",
            "标准普尔500指数",
            1.2,
            "The S&P 500 is up 92.15 points, or 1.2%.",
            "The S&P 500 is up 92.15 points, or 1.2%.",
        ),
    ],
)
def test_market_evidence_recovers_english_index_aliases(
    code, model_name, change, body, expected
):
    evidence = MarketSeriesEvidence(
        scope="weekly_us",
        index_code=code,
        index_name=model_name,
        start_date="2026-07-06",
        end_date="2026-07-10",
        reported_change_pct=change,
        source_url="https://apnews.com/article/market-weekly",
        source_title="How major US stock indexes fared Friday",
        evidence_excerpt=f"{model_name}本周变动{change}%",
    )

    assert _resolve_market_evidence_excerpt(evidence, body) == expected


def test_market_evidence_prefers_source_reported_change_over_extra_close_values():
    evidence = MarketSeriesEvidence(
        scope="weekly_us",
        index_code="DJIA",
        index_name="道琼斯指数",
        start_date="2026-07-06",
        end_date="2026-07-10",
        start_close=50000,
        end_close=49750,
        reported_change_pct=-0.5,
        source_url="https://apnews.com/article/market-weekly",
        source_title="美股周评",
        evidence_excerpt="道琼斯指数本周下跌0.5%",
    )

    normalized = _normalize_market_evidence_mode(evidence)

    assert normalized.reported_change_pct == -0.5
    assert normalized.start_close is None
    assert normalized.end_close is None


def test_reported_weekly_change_uses_requested_week_instead_of_page_publication_date():
    evidence = MarketSeriesEvidence(
        scope="weekly_a",
        index_code="000001",
        index_name="上证指数",
        start_date="2026-07-06",
        end_date="2026-07-13",
        reported_change_pct=-1.17,
        source_url="https://www.cnfin.com/market/weekly.html",
        source_title="一周市场回顾",
        evidence_excerpt="上周上证指数下跌1.17%。",
    )
    page = WebCandidate(
        url=evidence.source_url,
        canonical_url=evidence.source_url,
        title="一周市场回顾",
        site="cnfin.com",
        publish_date="2026-07-13",
        body=evidence.evidence_excerpt,
    )

    normalized = _normalize_reported_market_period(
        evidence,
        page,
        publication_date=datetime(2026, 7, 13).date(),
        period_start=datetime(2026, 7, 6).date(),
        period_end=datetime(2026, 7, 12).date(),
    )

    assert normalized.start_date == "2026-07-06"
    assert normalized.end_date == "2026-07-10"


def test_collect_pages_does_not_expose_raw_reader_exception():
    def search(query: str, max_results: int = 5):
        return [{"url": "https://www.gov.cn/unreachable.htm", "title": "国务院页面"}]

    def web_reader(url: str):
        raise RuntimeError("curl (28) connection timed out after 20001 milliseconds")

    gateway = ToolGateway(
        allowed_tools=("search", "web_reader"),
        tools={"search": search, "web_reader": web_reader},
    )

    _, warnings = _collect_pages(["测试"], gateway)

    assert warnings == ["网页读取失败：gov.cn"]
    assert "curl" not in warnings[0]


def test_collect_pages_retries_transient_reader_failure_for_allowed_source():
    attempts = 0

    def search(query: str, max_results: int = 5):
        return [{"url": "https://www.cnfin.com/market/weekly.html", "title": "周评"}]

    def web_reader(url: str):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary connection reset")
        return {
            "url": url,
            "canonical_url": url,
            "site": "cnfin.com",
            "title": "一周市场回顾",
            "publish_date": "2026-07-12",
            "text": "本周市场主要指数涨跌情况完整列示，可供人工核验。",
        }

    gateway = ToolGateway(
        allowed_tools=("search", "web_reader"),
        tools={"search": search, "web_reader": web_reader},
    )

    pages, warnings = _collect_pages(
        ["测试"],
        gateway,
        source_section="市场观察",
    )

    assert attempts == 2
    assert [page.title for page in pages] == ["一周市场回顾"]
    assert warnings == []


def test_collect_pages_supports_larger_result_limit_for_sparse_authority_search():
    limits: list[int] = []

    def search(query: str, max_results: int = 5):
        limits.append(max_results)
        return []

    gateway = ToolGateway(
        allowed_tools=("search",),
        tools={"search": search},
    )

    _collect_pages(["测试"], gateway, max_results_per_query=10)

    assert limits == [10]


def test_ordinary_assessment_continues_after_one_candidate_batch_fails():
    pages = [
        WebCandidate(
            url=f"https://www.cmbyc.com/news/{index}.html",
            canonical_url=f"https://www.cmbyc.com/news/{index}.html",
            title=f"招银云创发布项目进展{index}",
            site="cmbyc.com",
            publish_date="2026-07-10",
            body=f"招银云创发布项目进展{index}，披露银行科技服务能力。",
        )
        for index in range(7)
    ]
    calls = 0

    def llm_writer(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("structured output retry exceeded")
        page = pages[5]
        return ContentAssessmentBatch(
            items=[
                ContentCandidateAssessment(
                    source_url=page.canonical_url,
                    include=True,
                    section="同业动向",
                    title=page.title,
                    summary="招银云创披露银行科技服务能力。",
                    evidence_excerpt="披露银行科技服务能力",
                    score=8,
                    reason="实质科技经营动态",
                )
            ]
        )

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        pages,
        gateway,
        expected_section="同业动向",
        retrieved_at="2026-07-19T12:00:00+08:00",
    )

    assert calls == 2
    assert [item.title for item in items] == ["招银云创发布项目进展5"]
    assert len(records) == 1
    assert any("第1批候选评估失败" in warning for warning in warnings)


def test_weekly_us_queries_include_readable_ap_and_cnfin_sources():
    groups = dict(
        _market_query_groups(
            datetime(2026, 7, 13).date(),
            datetime(2026, 7, 6).date(),
            datetime(2026, 7, 12).date(),
        )
    )
    weekly_us = "\n".join(groups[("weekly_us",)])

    assert "Wall Street week ended July 10 2026" in weekly_us
    assert "新华财经 一周要闻 全球市场" in weekly_us


class FrontierFallbackTools(FakeTools):
    def __init__(self):
        super().__init__()
        self.pages["https://www.bis.org/publ/work999.htm"]["publish_date"] = "2026-06-25"

    def search(self, query: str, max_results: int = 5):
        if "研究报告" in query:
            self.search_calls.append(query)
            if "近30日补充" in query:
                return [
                    {
                        "url": "https://www.bis.org/publ/work999.htm",
                        "title": "利率传导与银行净息差",
                    }
                ]
            return []
        return super().search(query, max_results=max_results)

    def llm_writer(self, payload):
        result = super().llm_writer(payload)
        if payload["task"] == "internal_weekly_frontier_selection":
            result.publish_date = "2026-06-25"
        return result


def test_workflow_frontier_falls_back_to_recent_30_days(tmp_path):
    fake = FrontierFallbackTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": fake.search,
            "web_reader": fake.web_reader,
            "llm_writer": fake.llm_writer,
        },
    )

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 17, 10, 0),
            "output_dir": str(tmp_path / "output"),
        },
        gateway,
    )

    frontier = next(section for section in result.sections if section.name == "前沿观点")
    assert frontier.items[0].title == "利率传导与银行净息差"
    assert result.ready_for_approval is True
    assert any("近30日补充" in query for query in fake.search_calls)
    assert "前沿观点使用近30日兜底报告，发布日期不在本期统计周" in result.warnings


class PublicationDayFrontierTools(FakeTools):
    def __init__(self):
        super().__init__()
        self.pages["https://www.bis.org/publ/work999.htm"]["publish_date"] = "2026-07-13"


def test_workflow_does_not_use_publication_day_report_as_previous_week_frontier(tmp_path):
    fake = PublicationDayFrontierTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": fake.search,
            "web_reader": fake.web_reader,
            "llm_writer": fake.llm_writer,
        },
    )

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 17, 10, 0),
            "output_dir": str(tmp_path / "output"),
        },
        gateway,
    )

    frontier = next(section for section in result.sections if section.name == "前沿观点")
    assert frontier.items == []
    assert any("前沿观点未找到" in warning for warning in result.warnings)


class WrongFrontierDateTools(FakeTools):
    def llm_writer(self, payload):
        result = super().llm_writer(payload)
        if payload["task"] == "internal_weekly_frontier_selection":
            result.publish_date = "2026-07-08"
        return result


def test_workflow_rejects_frontier_date_that_differs_from_source_page(tmp_path):
    fake = WrongFrontierDateTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": fake.search,
            "web_reader": fake.web_reader,
            "llm_writer": fake.llm_writer,
        },
    )

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 17, 10, 0),
            "output_dir": str(tmp_path / "output"),
        },
        gateway,
    )

    frontier = next(section for section in result.sections if section.name == "前沿观点")
    assert frontier.items == []
    assert any("发布日期与来源页面不一致" in warning for warning in result.warnings)
