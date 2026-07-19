import json
from datetime import datetime
from pathlib import Path

from app.platform.tools import ToolGateway
from skills.internal_weekly.schema import (
    ContentAssessmentBatch,
    ContentCandidateAssessment,
    FrontierSelection,
    MarketContextEvidence,
    MarketEvidenceBundle,
    MarketSeriesEvidence,
)
from skills.internal_weekly.workflow import _collect_pages, _evidence_in_body, run


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
        self.market_required_scopes: list[tuple[str, ...]] = []
        self.pages = {
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
        if "研究报告" in query:
            return [{"url": "https://www.bis.org/publ/work999.htm", "title": "利率传导与银行净息差"}]
        if "指数" in query or "A股" in query or "港股" in query or "美股" in query:
            return [{"url": "https://www.sse.com.cn/market/", "title": "市场数据"}]
        return [
            {"url": "https://www.gov.cn/meeting.htm", "title": "国务院常务会议研究部署重点工作"},
            {"url": "https://www.pbc.gov.cn/rule.htm", "title": "中国人民银行发布金融统计数据"},
            {"url": "https://www.cs.com.cn/market.htm", "title": "银行理财市场结构继续分化"},
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
    assert result.sections[0].name == "党政要闻"
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
    assert len(bis_queries) == 2
    assert any("latest bulletin" in query for query in bis_queries)
    hk_queries = [query for query in fake.search_calls if "恒生中国企业指数" in query]
    assert len(hk_queries) == 2
    assert any("港股一周复盘" in query for query in hk_queries)
    assert all("2026年7月10日" in query for query in hk_queries)
    assert fake.market_required_scopes == [
        ("weekly_a", "weekly_us"),
        ("monday_a",),
        ("weekly_hk",),
    ]


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
    assert "7月13日A股收盘情况：待当日收盘数据发布后更新。" in (
        market_section.items[0].body
    )
    assert "周一A股收盘数据待当日15:30后更新" in result.warnings
    assert "暂不生成洁净版本" in result.message
    assert result.output_file.endswith(".md")
    assert all("A股收评" not in query for query in fake.search_calls)
    assert fake.market_required_scopes == [
        ("weekly_a", "weekly_us"),
        ("weekly_hk",),
    ]


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
