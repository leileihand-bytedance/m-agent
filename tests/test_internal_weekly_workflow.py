import json
import threading
from datetime import datetime
from pathlib import Path

import pytest

from app.platform.tools import ToolGateway
from skills.internal_weekly import workflow as internal_weekly_workflow
from skills.internal_weekly.schema import (
    ContentAssessmentBatch,
    ContentCandidateAssessment,
    FrontierSelection,
    GroundingRepairBatch,
    GroundingRepairItem,
    MarketContextEvidence,
    MarketEvidenceBundle,
    MarketSeriesEvidence,
    PartyEventSynthesis,
    WebCandidate,
)
from skills.internal_weekly.workflow import (
    _collect_pages,
    _collect_regulatory_pages,
    _collect_source_feed_pages,
    _compact_chinese_summary,
    _evidence_in_body,
    _frontier_queries,
    _market_query_groups,
    _market_observation_query_groups,
    _merge_candidate_pages_balanced,
    _normalize_market_evidence_mode,
    _normalize_reported_market_period,
    _ordinary_items,
    _ordinary_query_groups,
    _party_major_event_keys,
    _peer_activity_query_groups,
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


def test_five_section_tasks_run_in_parallel_and_return_in_fixed_order():
    barrier = threading.Barrier(5, timeout=3)
    lock = threading.Lock()
    thread_ids: set[int] = set()

    def task(section_name: str):
        def execute() -> str:
            with lock:
                thread_ids.add(threading.get_ident())
            barrier.wait()
            return section_name

        return execute

    tasks = {section_name: task(section_name) for section_name in (
        "党政要闻",
        "监管动态",
        "同业动向",
        "市场观察",
        "前沿观点",
    )}

    results, failures = internal_weekly_workflow._run_parallel_section_tasks(tasks)

    assert failures == {}
    assert list(results) == list(tasks)
    assert list(results.values()) == list(tasks)
    assert len(thread_ids) == 5


def test_parallel_section_failure_does_not_cancel_other_sections():
    def fail_regulation() -> str:
        raise RuntimeError("regulation failed")

    tasks = {
        "党政要闻": lambda: "党政要闻",
        "监管动态": fail_regulation,
        "同业动向": lambda: "同业动向",
        "市场观察": lambda: "市场观察",
        "前沿观点": lambda: "前沿观点",
    }

    results, failures = internal_weekly_workflow._run_parallel_section_tasks(tasks)

    assert list(results) == ["党政要闻", "同业动向", "市场观察", "前沿观点"]
    assert list(failures) == ["监管动态"]
    assert isinstance(failures["监管动态"], RuntimeError)


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
                chinese_title="利率传导对银行净息差的影响",
                institution="国际清算银行",
                authors=["研究员甲"],
                publish_date="2026-07-09",
                selected_passages=["第二段分析银行净息差变化。", "第三段提示风险边界。"],
                chinese_summary=(
                    "报告分析利率变化向银行资产负债表的传导，指出净息差会受到资产与"
                    "负债重定价节奏差异的影响，并提示银行关注期限错配和利率风险。"
                ),
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
    assert len(market_section.items) >= 2
    assert any(item.title == "银行理财市场结构继续分化" for item in market_section.items[1:])
    frontier = next(section for section in result.sections if section.name == "前沿观点")
    assert frontier.items[0].content_mode == "report_summary"
    assert frontier.items[0].title == "利率传导对银行净息差的影响"
    assert frontier.items[0].body.startswith("7月9日，")
    assert "报告分析利率变化向银行资产负债表的传导" in frontier.items[0].body
    assert "（来源：国际清算银行《利率传导与银行净息差》）" in frontier.items[0].body
    assert "第二段分析银行净息差变化。" not in frontier.items[0].body
    assert "这是模型" not in frontier.items[0].body
    assert result.draft_version
    assert result.document_metadata == {
        "generation_mode": "full_weekly",
        "publication_date": "2026-07-13",
        "period_start": "2026-07-06",
        "period_end": "2026-07-12",
        "draft_version": result.draft_version,
        "ready_for_approval": "true",
    }
    assert result.output_file.endswith(".md")
    assert not list(output_dir.glob("*.docx"))

    review_text = Path(result.output_file).read_text(encoding="utf-8")
    assert "内容核对稿" in review_text
    assert "原文链接" in review_text
    assert "发生日期：2026-07-09" in review_text
    assert "发布日期：2026-07-09" in review_text
    assert "报告位置：网页摘要" in review_text

    manifest = json.loads(Path(result.manifest_file).read_text(encoding="utf-8"))
    assert manifest["draft_version"] == result.draft_version
    assert manifest["ready_for_approval"] is True
    assert any(section["name"] == "市场观察" for section in manifest["sections"])
    assert all(record["url"] for record in manifest["source_records"])
    assert all(record["content_sha256"] for record in manifest["source_records"])
    assert any("中国政府网" in query for query in fake.search_calls)
    market_queries = dict(
        _ordinary_query_groups(
            datetime(2026, 7, 6).date(),
            datetime(2026, 7, 12).date(),
        )
    )["市场观察"]
    for query in market_queries:
        assert fake.search_limits[fake.search_calls.index(query)] == 10
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


def test_full_weekly_dispatches_exactly_five_section_tasks(monkeypatch):
    fake = FakeTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": fake.search,
            "web_reader": fake.web_reader,
            "llm_writer": fake.llm_writer,
        },
    )
    captured_sections: list[str] = []
    original_runner = internal_weekly_workflow._run_parallel_section_tasks

    def capture_tasks(tasks):
        captured_sections.extend(tasks)
        return original_runner(tasks)

    monkeypatch.setattr(
        internal_weekly_workflow,
        "_run_parallel_section_tasks",
        capture_tasks,
    )

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 17, 10, 0),
        },
        gateway,
    )

    assert captured_sections == [
        "党政要闻",
        "监管动态",
        "同业动向",
        "市场观察",
        "前沿观点",
    ]
    assert result.ready_for_approval is True


def test_full_weekly_keeps_other_sections_when_one_parallel_task_fails(monkeypatch):
    fake = FakeTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": fake.search,
            "web_reader": fake.web_reader,
            "llm_writer": fake.llm_writer,
        },
    )

    def fail_regulation(*_args, **_kwargs):
        raise RuntimeError("sensitive provider detail")

    monkeypatch.setattr(
        internal_weekly_workflow,
        "_run_regulatory_section_task",
        fail_regulation,
    )

    result = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 17, 10, 0),
        },
        gateway,
    )

    sections = {section.name: section.items for section in result.sections}
    assert sections["监管动态"] == []
    assert all(
        sections[section_name]
        for section_name in ("党政要闻", "同业动向", "市场观察", "前沿观点")
    )
    assert result.ready_for_approval is False
    assert any(
        warning.startswith("监管动态模块执行失败（RuntimeError）")
        for warning in result.warnings
    )
    assert all("sensitive provider detail" not in warning for warning in result.warnings)


def test_full_weekly_uses_source_extract_instead_of_leaving_rewrite_problem():
    class InvalidRegulatorySummaryTools(FakeTools):
        def llm_writer(self, payload):
            if payload["task"] == "internal_weekly_grounding_repair":
                return GroundingRepairBatch(items=[])
            result = super().llm_writer(payload)
            if payload["task"] != "internal_weekly_content_assessment":
                return result
            return ContentAssessmentBatch(
                items=[
                    item.model_copy(
                        update={"summary": "中国人民银行发布999项金融统计数据。"}
                    )
                    if item.section == "监管动态"
                    else item
                    for item in result.items
                ]
            )

    fake = InvalidRegulatorySummaryTools()
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
        },
        gateway,
    )

    regulatory = next(
        section for section in result.sections if section.name == "监管动态"
    )
    assert len(regulatory.items) == 1
    assert regulatory.items[0].body.startswith("近日，中国人民银行发布金融统计数据")
    assert "999项" not in regulatory.items[0].body
    assert result.ready_for_approval is True
    assert result.document_metadata["ready_for_approval"] == "true"
    assert "加工状态：待整理" not in result.body
    assert all("已保留待整理" not in warning for warning in result.warnings)


def test_approved_revision_generates_word_without_researching_again(
    tmp_path,
    monkeypatch,
):
    def fake_generate_word(*, draft, request_text, output_dir):
        output = Path(output_dir) / (
            f"微众银行信息内参周报-{draft.publication_date.isoformat()}.docx"
        )
        output.write_bytes(b"test-docx")
        return output

    monkeypatch.setattr(
        "skills.internal_weekly.workflow.generate_internal_weekly_docx",
        fake_generate_word,
    )
    fake = FakeTools()
    gateway = ToolGateway(
        allowed_tools=("search", "web_reader", "llm_writer"),
        tools={
            "search": fake.search,
            "web_reader": fake.web_reader,
            "llm_writer": fake.llm_writer,
        },
    )
    original = run(
        {
            "text": "生成本周内参周报",
            "now": datetime(2026, 7, 17, 10, 0),
            "output_dir": str(tmp_path / "review-output"),
        },
        gateway,
    )
    calls_before_export = (
        len(fake.search_calls),
        len(fake.market_required_scopes),
    )

    confirmation = run(
        {
            "revision": True,
            "text": "请生成 Word 洁净版",
            "revision_request": "请生成 Word 洁净版",
            "previous_title": original.title,
            "previous_body": original.body,
            "previous_sources": original.sources,
            "previous_document_metadata": original.document_metadata,
            "output_dir": str(tmp_path / "unapproved-output"),
        },
        gateway,
    )

    assert confirmation.needs_clarification is True
    assert "核对无误" in confirmation.message
    assert confirmation.output_file == ""
    assert calls_before_export == (
        len(fake.search_calls),
        len(fake.market_required_scopes),
    )

    approved_output = tmp_path / "approved-output"
    approved_output.mkdir()
    exported = run(
        {
            "revision": True,
            "text": "请生成 Word 洁净版\n核对无误",
            "revision_request": "请生成 Word 洁净版",
            "previous_title": original.title,
            "previous_body": original.body,
            "previous_sources": original.sources,
            "previous_document_metadata": original.document_metadata,
            "output_dir": str(approved_output),
        },
        gateway,
    )

    assert exported.needs_clarification is False
    assert exported.output_file.endswith(".docx")
    assert Path(exported.output_file).is_file()
    assert exported.draft_version == original.draft_version
    assert "目录项和页码已生成" in exported.message
    assert "更新整个目录" not in exported.message
    assert calls_before_export == (
        len(fake.search_calls),
        len(fake.market_required_scopes),
    )
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
    assert len(fake.search_calls) == 2
    assert all("2026年7月13日" in query and "A股" in query for query in fake.search_calls)
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
    market_queries = groups["市场观察"]
    assert len(market_queries) >= 7
    market_query_text = "\n".join(market_queries)
    for topic in (
        "GDP",
        "CPI",
        "进出口",
        "LPR",
        "债券收益率",
        "人民币汇率",
        "银行理财",
        "美联储",
        "非农",
        "熔断",
        "地缘冲突",
        "黄金",
        "IPO",
    ):
        assert topic in market_query_text
    peer_queries = "\n".join(groups["同业动向"])
    assert "Monzo" in peer_queries
    assert "ZA Bank" in peer_queries
    assert "建信金科" in peer_queries
    assert "工银科技" in peer_queries


def test_market_observation_queries_are_registry_driven_and_grouped_by_topic():
    groups = _market_observation_query_groups(
        datetime(2026, 7, 6).date(),
        datetime(2026, 7, 12).date(),
    )

    assert [topic_id for topic_id, _ in groups] == [
        "domestic_macro",
        "domestic_rates_fx",
        "wealth_asset_management",
        "global_macro_central_banks",
        "global_market_stress",
        "geopolitics_trade_energy",
        "major_capital_events",
    ]
    assert all(queries for _, queries in groups)
    assert all("2026年7月6日至2026年7月12日" in query for _, queries in groups for query in queries)


def test_peer_activity_queries_are_registry_driven_and_grouped_by_entity_type():
    groups = _peer_activity_query_groups(
        datetime(2026, 7, 6).date(),
        datetime(2026, 7, 12).date(),
    )

    group_ids = [group_id for group_id, _ in groups]
    assert len(group_ids) == 7
    assert sum(group_id.startswith("domestic_digital_banks_") for group_id in group_ids) == 4
    assert sum(group_id.startswith("international_digital_banks_") for group_id in group_ids) == 2
    assert sum(group_id.startswith("bank_technology_subsidiaries_") for group_id in group_ids) == 1
    assert all(queries for _, queries in groups)
    assert all(
        "2026年7月6日至2026年7月12日" in query
        for _, queries in groups
        for query in queries
    )
    query_text = "\n".join(query for _, queries in groups for query in queries)
    for marker in (
        "经营业绩",
        "客户",
        "存款",
        "贷款",
        "产品",
        "科技",
        "风险管理",
        "牌照",
        "人工智能",
        "项目落地",
    ):
        assert marker in query_text


def test_peer_activity_applies_official_priority_score_floor_and_scope_instruction():
    official = WebCandidate(
        url="https://monzo.com/press/annual-results",
        canonical_url="https://monzo.com/press/annual-results",
        title="Monzo发布年度经营业绩",
        site="monzo.com",
        publisher="Monzo",
        publish_date="2026-07-10",
        body="Monzo公布年度经营业绩，客户存款和盈利能力显著增长。",
    )
    media = WebCandidate(
        url="https://www.reuters.com/business/finance/revolut-expansion",
        canonical_url="https://www.reuters.com/business/finance/revolut-expansion",
        title="Revolut取得新市场银行牌照",
        site="reuters.com",
        publisher="路透社",
        publish_date="2026-07-10",
        body="Revolut取得新市场银行牌照，并计划扩大存贷款和支付业务。",
    )
    low_score = WebCandidate(
        url="https://n26.com/en-eu/press/brand-campaign",
        canonical_url="https://n26.com/en-eu/press/brand-campaign",
        title="N26推出普通品牌营销活动",
        site="n26.com",
        publisher="N26",
        publish_date="2026-07-10",
        body="N26推出普通品牌营销活动，没有披露产品、经营或技术变化。",
    )
    assessments = {
        official.canonical_url: ContentCandidateAssessment(
            source_url=official.canonical_url,
            include=True,
            section="同业动向",
            title=official.title,
            summary="Monzo客户存款和盈利能力显著增长。",
            evidence_excerpt="客户存款和盈利能力显著增长",
            score=8,
            reason="经营指标具有可比性",
        ),
        media.canonical_url: ContentCandidateAssessment(
            source_url=media.canonical_url,
            include=True,
            section="同业动向",
            title=media.title,
            summary="Revolut取得新市场银行牌照并计划扩大业务。",
            evidence_excerpt="计划扩大存贷款和支付业务",
            score=8,
            reason="国际数字银行扩张信号",
        ),
        low_score.canonical_url: ContentCandidateAssessment(
            source_url=low_score.canonical_url,
            include=True,
            section="同业动向",
            title=low_score.title,
            summary="N26推出普通品牌活动。",
            evidence_excerpt="没有披露产品、经营或技术变化",
            score=6.9,
            reason="只有营销信息",
        ),
    }
    instructions: list[str] = []

    def llm_writer(payload):
        instructions.append(payload["instruction"])
        urls = [material["url"] for material in payload["materials"]]
        return ContentAssessmentBatch(items=[assessments[url] for url in urls])

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        [media, low_score, official],
        gateway,
        expected_section="同业动向",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert [item.title for item in items] == [official.title, media.title]
    assert len(records) == 2
    assert any("同业价值评分低于7分" in warning for warning in warnings)
    assert "经营业绩、产品业务、科技与风控、战略合作、组织治理" in instructions[0]
    assert "普通营销活动" in instructions[0]


def test_peer_activity_caps_items_at_five():
    pages = [
        WebCandidate(
            url=f"https://monzo.com/press/peer-{index}",
            canonical_url=f"https://monzo.com/press/peer-{index}",
            title=f"Monzo发布重大经营进展{index}",
            site="monzo.com",
            publisher="Monzo",
            publish_date="2026-07-10",
            body=f"Monzo发布重大经营进展{index}，披露存款、贷款和盈利能力变化。",
        )
        for index in range(6)
    ]
    assessments = {
        page.canonical_url: ContentCandidateAssessment(
            source_url=page.canonical_url,
            include=True,
            section="同业动向",
            title=page.title,
            summary=f"Monzo重大经营进展{index}反映存贷款和盈利变化。",
            evidence_excerpt="披露存款、贷款和盈利能力变化",
            score=9 - index * 0.1,
            reason="具有同业经营参考价值",
        )
        for index, page in enumerate(pages)
    }

    def llm_writer(payload):
        urls = [material["url"] for material in payload["materials"]]
        return ContentAssessmentBatch(items=[assessments[url] for url in urls])

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        pages,
        gateway,
        expected_section="同业动向",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert len(items) == 5
    assert len(records) == 5
    assert warnings == []


def test_balanced_market_merge_preserves_every_topic_before_second_page():
    groups = []
    for group_index in range(7):
        groups.append(
            [
                WebCandidate(
                    url=f"https://www.cs.com.cn/{group_index}/{page_index}.htm",
                    canonical_url=f"https://www.cs.com.cn/{group_index}/{page_index}.htm",
                    title=f"主题{group_index}候选{page_index}",
                    site="cs.com.cn",
                    publish_date="2026-07-10",
                    body="该候选包含足够长度的金融市场正文内容。",
                )
                for page_index in range(2)
            ]
        )

    merged = _merge_candidate_pages_balanced(*groups)

    assert [page.title for page in merged[:7]] == [
        f"主题{group_index}候选0" for group_index in range(7)
    ]


def test_market_observation_applies_source_priority_score_floor_and_impact_instruction():
    official = WebCandidate(
        url="https://www.stats.gov.cn/sj/zxfb/202607/macro.html",
        canonical_url="https://www.stats.gov.cn/sj/zxfb/202607/macro.html",
        title="国家统计局发布宏观经济数据",
        site="stats.gov.cn",
        publisher="国家统计局",
        publish_date="2026-07-10",
        body="国家统计局发布国内生产总值和居民消费价格数据，显示经济增长和通胀走势变化。",
    )
    media = WebCandidate(
        url="https://www.cs.com.cn/market/bond.html",
        canonical_url="https://www.cs.com.cn/market/bond.html",
        title="全球债市收益率显著波动",
        site="cs.com.cn",
        publisher="中国证券报",
        publish_date="2026-07-10",
        body="全球债市收益率显著波动，融资成本和市场风险偏好同步变化。",
    )
    low_score = WebCandidate(
        url="https://www.reuters.com/markets/minor.html",
        canonical_url="https://www.reuters.com/markets/minor.html",
        title="海外市场日常小幅波动",
        site="reuters.com",
        publisher="路透社",
        publish_date="2026-07-10",
        body="海外股票市场日常小幅波动，未形成跨市场传导或显著经济影响。",
    )
    assessments = {
        official.canonical_url: ContentCandidateAssessment(
            source_url=official.canonical_url,
            include=True,
            section="市场观察",
            title=official.title,
            summary="国内生产总值和居民消费价格数据反映增长与通胀变化。",
            evidence_excerpt="显示经济增长和通胀走势变化",
            score=8,
            reason="国内宏观数据影响利率和经营预期",
        ),
        media.canonical_url: ContentCandidateAssessment(
            source_url=media.canonical_url,
            include=True,
            section="市场观察",
            title=media.title,
            summary="全球债市收益率波动影响融资成本和风险偏好。",
            evidence_excerpt="融资成本和市场风险偏好同步变化",
            score=8,
            reason="全球市场风险事件",
        ),
        low_score.canonical_url: ContentCandidateAssessment(
            source_url=low_score.canonical_url,
            include=True,
            section="市场观察",
            title=low_score.title,
            summary="海外市场出现日常小幅波动。",
            evidence_excerpt="海外股票市场日常小幅波动",
            score=6.9,
            reason="影响范围有限",
        ),
    }
    instructions: list[str] = []

    def llm_writer(payload):
        instructions.append(payload["instruction"])
        urls = [material["url"] for material in payload["materials"]]
        return ContentAssessmentBatch(items=[assessments[url] for url in urls])

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        [media, low_score, official],
        gateway,
        expected_section="市场观察",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert [item.title for item in items] == [official.title, media.title]
    assert len(records) == 2
    assert any("评分低于7分" in warning for warning in warnings)
    assert "增长、通胀、利率、汇率、流动性、风险偏好" in instructions[0]
    assert "不设最低凑数要求" in instructions[0]


def test_market_observation_caps_non_fixed_items_at_five():
    pages = [
        WebCandidate(
            url=f"https://www.cs.com.cn/market/{index}.html",
            canonical_url=f"https://www.cs.com.cn/market/{index}.html",
            title=f"全球债市重大波动事件{index}",
            site="cs.com.cn",
            publisher="中国证券报",
            publish_date="2026-07-10",
            body=f"全球债市重大波动事件{index}影响融资成本、流动性和风险偏好。",
        )
        for index in range(6)
    ]
    assessments = {
        page.canonical_url: ContentCandidateAssessment(
            source_url=page.canonical_url,
            include=True,
            section="市场观察",
            title=page.title,
            summary=f"全球债市重大波动事件{index}影响融资成本和风险偏好。",
            evidence_excerpt=f"影响融资成本、流动性和风险偏好",
            score=9 - index * 0.1,
            reason="重大跨市场影响",
        )
        for index, page in enumerate(pages)
    }

    def llm_writer(payload):
        urls = [material["url"] for material in payload["materials"]]
        return ContentAssessmentBatch(items=[assessments[url] for url in urls])

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        pages,
        gateway,
        expected_section="市场观察",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert len(items) == 5
    assert len(records) == 5
    assert warnings == []


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


def test_party_items_merge_major_event_updates_and_drop_low_value_commentary():
    specs = [
        (
            "speech",
            "习近平出席2026世界人工智能大会开幕式并发表主旨讲话",
            "习近平在2026世界人工智能大会发表主旨讲话，提出完善全球治理。",
            "提出完善全球治理",
            "习近平提出推动人工智能创新发展并完善全球治理。",
            10,
        ),
        (
            "plan",
            "《人工智能合作发展行动计划》发布",
            "有关部门在2026世界人工智能大会发布行动计划，提出数据和算力合作。",
            "提出数据和算力合作",
            "大会发布人工智能合作发展行动计划，部署数据、算力和人才合作。",
            9,
        ),
        (
            "organization",
            "成立世界人工智能合作组织协定签署仪式在上海举行",
            "2026世界人工智能大会期间，多国签署成立世界人工智能合作组织协定。",
            "多国签署成立世界人工智能合作组织协定",
            "多国签署协定，推动成立世界人工智能合作组织。",
            8,
        ),
        (
            "commentary",
            "让人工智能成为造福全人类的国际公共产品——中国贡献智慧力量",
            "文章回顾2026世界人工智能大会成果并作综合评论。",
            "作综合评论",
            "综合评论回顾大会成果。",
            9.5,
        ),
    ]
    pages = [
        WebCandidate(
            url=f"https://www.gov.cn/yaowen/liebiao/202607/{slug}.htm",
            canonical_url=f"https://www.gov.cn/yaowen/liebiao/202607/{slug}.htm",
            title=title,
            site="gov.cn",
            publisher="中国政府网",
            publish_date="2026-07-17",
            body=body,
        )
        for slug, title, body, _, _, _ in specs
    ]
    assessments = [
        ContentCandidateAssessment(
            source_url=page.canonical_url,
            include=True,
            section="党政要闻",
            title=page.title,
            summary=summary,
            evidence_excerpt=evidence,
            score=score,
            reason="中央层面人工智能重大活动",
        )
        for page, (_, _, _, evidence, summary, score) in zip(pages, specs, strict=True)
    ]
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": lambda payload: ContentAssessmentBatch(items=assessments)},
    )

    items, records, warnings = _ordinary_items(
        pages,
        gateway,
        expected_section="党政要闻",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert len(items) == 1
    assert items[0].title == specs[0][1]
    assert len(items[0].source_ids) == 3
    assert "行动计划" in items[0].body
    assert "合作组织" in items[0].body
    assert "综合评论" not in items[0].body
    assert len(records) == 3
    assert warnings == []


def test_party_major_event_is_rewritten_as_one_integrated_summary():
    speech = WebCandidate(
        url="https://www.gov.cn/yaowen/liebiao/202607/speech.htm",
        canonical_url="https://www.gov.cn/yaowen/liebiao/202607/speech.htm",
        title="习近平出席2026世界人工智能大会开幕式并发表主旨讲话",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-17",
        body="习近平在2026世界人工智能大会发表主旨讲话，提出完善全球治理。",
    )
    plan = WebCandidate(
        url="https://www.gov.cn/yaowen/liebiao/202607/plan.htm",
        canonical_url="https://www.gov.cn/yaowen/liebiao/202607/plan.htm",
        title="《人工智能合作发展行动计划》发布",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-17",
        body="有关部门在2026世界人工智能大会发布行动计划，提出数据和算力合作。",
    )
    assessments = ContentAssessmentBatch(
        items=[
            ContentCandidateAssessment(
                source_url=speech.canonical_url,
                include=True,
                section="党政要闻",
                title=speech.title,
                summary="习近平提出推动人工智能创新发展并完善全球治理。",
                evidence_excerpt="提出完善全球治理",
                score=10,
                reason="中央层面人工智能重大活动",
            ),
            ContentCandidateAssessment(
                source_url=plan.canonical_url,
                include=True,
                section="党政要闻",
                title=plan.title,
                summary="大会发布人工智能合作发展行动计划，部署数据、算力和人才合作。",
                evidence_excerpt="提出数据和算力合作",
                score=9,
                reason="中央层面人工智能重大活动",
            ),
        ]
    )
    integrated = (
        "2026世界人工智能大会围绕创新发展与全球治理形成系列部署。"
        "习近平在主旨讲话中提出推动人工智能创新发展、完善全球治理；"
        "大会同期发布合作发展行动计划，部署数据、算力和人才合作。"
    )
    calls: list[str] = []

    def llm_writer(payload):
        task = str(payload["task"])
        calls.append(task)
        if task == "internal_weekly_content_assessment":
            return assessments
        if task == "internal_weekly_party_event_synthesis":
            return PartyEventSynthesis(
                title="2026世界人工智能大会形成创新发展与全球治理系列部署",
                summary=integrated,
            )
        raise AssertionError(f"unexpected task: {task}")

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        [speech, plan],
        gateway,
        expected_section="党政要闻",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert calls == [
        "internal_weekly_content_assessment",
        "internal_weekly_party_event_synthesis",
    ]
    assert len(items) == 1
    assert items[0].title == "2026世界人工智能大会形成创新发展与全球治理系列部署"
    assert items[0].body == f"近日，{integrated}"
    assert "\n\n" not in items[0].body
    assert len(items[0].source_ids) == 2
    assert len(records) == 2
    assert warnings == []


def test_party_major_event_key_supports_lujiazui_forum():
    speech_keys = _party_major_event_keys(
        "有关负责人出席2026陆家嘴论坛并发表演讲",
        "2026陆家嘴论坛聚焦全球经济和金融治理。",
    )
    outcome_keys = _party_major_event_keys(
        "多项金融开放举措发布",
        "有关机构在陆家嘴论坛发布多项金融开放举措。",
    )

    assert "陆家嘴论坛" in speech_keys & outcome_keys


def test_regulatory_item_uses_regulator_as_subject_for_state_council_briefing():
    page = WebCandidate(
        url="https://www.pbc.gov.cn/goutongjiaoliu/news.htm",
        canonical_url="https://www.pbc.gov.cn/goutongjiaoliu/news.htm",
        title="国新办举行新闻发布会 介绍上半年货币政策执行情况",
        site="pbc.gov.cn",
        publisher="中国人民银行",
        publish_date="2026-07-15",
        body=(
            "7月14日，国新办举行新闻发布会，"
            "中国人民银行副行长介绍上半年货币政策执行情况。"
        ),
    )
    assessment = ContentCandidateAssessment(
        source_url=page.canonical_url,
        include=True,
        section="监管动态",
        title=page.title,
        summary="国新办举行新闻发布会，央行副行长介绍货币政策执行情况。",
        occurrence_date="2026-07-14",
        evidence_block_ids=["E001"],
        score=9,
        reason="人民银行政策发布",
    )
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: ContentAssessmentBatch(items=[assessment])
        },
    )

    items, records, warnings = _ordinary_items(
        [page],
        gateway,
        expected_section="监管动态",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert items[0].title == "中国人民银行介绍上半年货币政策执行情况"
    assert items[0].body.startswith("7月14日，中国人民银行副行长")
    assert "国新办" not in items[0].body
    assert records[0].publisher == "中国人民银行"
    assert warnings == []


def test_ordinary_item_repairs_paraphrased_evidence_with_exact_source_sentence():
    page = WebCandidate(
        url="https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1264585",
        canonical_url="https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?docId=1264585",
        title="金融监管总局召开2026年两会重点建议提案座谈会",
        site="nfra.gov.cn",
        publisher="国家金融监督管理总局",
        publish_date="2026-07-15",
        body=(
            "金融监管总局党委委员、副局长丛林同志出席会议，与代表委员面对面交流，"
            "认真听取代表委员对推动普惠金融高质量发展的意见建议，"
            "研究进一步完善我国普惠金融体系。"
        ),
    )
    assessment = ContentCandidateAssessment(
        source_url=page.canonical_url,
        include=True,
        section="监管动态",
        title=page.title,
        summary="金融监管总局研究完善普惠金融体系，提升小微企业金融服务水平。",
        evidence_excerpt="金融监管总局副局长丛林与代表委员交流，研究完善普惠金融体系。",
        score=9,
        reason="普惠金融监管部署",
    )
    exact = (
        "金融监管总局党委委员、副局长丛林同志出席会议，与代表委员面对面交流，"
        "认真听取代表委员对推动普惠金融高质量发展的意见建议，"
        "研究进一步完善我国普惠金融体系。"
    )
    calls: list[str] = []

    def llm_writer(payload):
        task = str(payload["task"])
        calls.append(task)
        if task == "internal_weekly_content_assessment":
            return ContentAssessmentBatch(items=[assessment])
        if task == "internal_weekly_grounding_repair":
            return GroundingRepairBatch(
                items=[
                    GroundingRepairItem(
                        source_url=page.canonical_url,
                        summary=assessment.summary,
                        evidence_block_ids=["E001"],
                    )
                ]
            )
        raise AssertionError(f"unexpected task: {task}")

    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={"llm_writer": llm_writer},
    )

    items, records, warnings = _ordinary_items(
        [page],
        gateway,
        expected_section="监管动态",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert calls == [
        "internal_weekly_content_assessment",
        "internal_weekly_grounding_repair",
    ]
    assert [item.title for item in items] == [page.title]
    assert records[0].evidence_excerpts == [exact]
    assert warnings == []


def test_ordinary_summary_can_compress_source_when_core_facts_stay_grounded():
    page = WebCandidate(
        url="https://www.gov.cn/zhengce/content/202607/content_7075216.htm",
        canonical_url="https://www.gov.cn/zhengce/content/202607/content_7075216.htm",
        title="中共中央 国务院部署扩大消费重点工作",
        site="gov.cn",
        publisher="中国政府网",
        publish_date="2026-07-13",
        body=(
            "7月12日，中共中央、国务院部署扩大消费重点工作，提出到2030年社会消费品零售"
            "总额达到60万亿元左右，并要求深入实施提振消费专项行动。"
        ),
    )
    compressed = (
        "中共中央、国务院提出到2030年社会消费品零售总额达到60万亿元左右，"
        "并部署提振消费专项行动。"
    )
    assessment = ContentCandidateAssessment(
        source_url=page.canonical_url,
        include=True,
        section="党政要闻",
        title=page.title,
        summary=compressed,
        occurrence_date="2026-07-12",
        evidence_excerpt="",
        evidence_block_ids=["E001"],
        score=9,
        reason="中央扩大内需和促进消费部署",
    )
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: ContentAssessmentBatch(items=[assessment])
        },
    )

    items, records, warnings = _ordinary_items(
        [page],
        gateway,
        expected_section="党政要闻",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert warnings == []
    assert items[0].body == f"7月12日，{compressed}"
    assert records[0].occurrence_date == "2026-07-12"
    assert records[0].publish_date == "2026-07-13"
    assert records[0].evidence_excerpts == [page.body]


def test_ordinary_item_regenerates_summary_when_core_number_is_wrong():
    page = WebCandidate(
        url="https://www.csrc.gov.cn/csrc/c100028/c7646105/content.shtml",
        canonical_url="https://www.csrc.gov.cn/csrc/c100028/c7646105/content.shtml",
        title="中国证监会组织财务造假综合惩防跨部门培训",
        site="csrc.gov.cn",
        publisher="中国证券监督管理委员会",
        publish_date="2026-07-17",
        body=(
            "中国证监会启动专项行动，目前已办理47起典型案件，"
            "持续打击和防范上市公司财务造假。"
        ),
    )
    assessment = ContentCandidateAssessment(
        source_url=page.canonical_url,
        include=True,
        section="监管动态",
        title=page.title,
        summary="中国证监会专项行动已办理247起典型案件。",
        evidence_excerpt="证监会专项行动已办理247起典型案件。",
        score=9,
        reason="资本市场监管执法",
    )

    repair_calls = 0

    def llm_writer(payload):
        nonlocal repair_calls
        if payload["task"] == "internal_weekly_content_assessment":
            return ContentAssessmentBatch(items=[assessment])
        repair_calls += 1
        repaired_summary = (
            assessment.summary
            if repair_calls == 1
            else "中国证监会专项行动已办理47起典型案件，持续防范财务造假。"
        )
        return GroundingRepairBatch(
            items=[
                GroundingRepairItem(
                    source_url=page.canonical_url,
                    summary=repaired_summary,
                    evidence_block_ids=["E001"],
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
        expected_section="监管动态",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert repair_calls == 2
    assert len(items) == 1
    assert items[0].body.startswith("近日，")
    assert "47起" in items[0].body
    assert "247起" not in items[0].body
    assert len(records) == 1
    assert records[0].url == page.canonical_url
    assert records[0].evidence_excerpts == [page.body]
    assert warnings == []


def test_ordinary_item_uses_verified_source_extract_when_regeneration_keeps_failing():
    page = WebCandidate(
        url="https://www.stats.gov.cn/sj/xwfbh/fbhwd/202607/content.htm",
        canonical_url="https://www.stats.gov.cn/sj/xwfbh/fbhwd/202607/content.htm",
        title="上半年经济运行情况",
        site="stats.gov.cn",
        publisher="国家统计局",
        publish_date="2026-07-15",
        body="二季度国内生产总值同比增长4.3%，增速较一季度回落0.7个百分点。",
    )
    assessment = ContentCandidateAssessment(
        source_url=page.canonical_url,
        include=True,
        section="市场观察",
        title=page.title,
        summary="二季度国内生产总值同比增长4.3%，增速较一季度提高0.7个百分点。",
        evidence_excerpt="",
        evidence_block_ids=["E001"],
        score=9,
        reason="宏观经济增速变化影响市场判断",
    )

    def llm_writer(payload):
        if payload["task"] == "internal_weekly_content_assessment":
            return ContentAssessmentBatch(items=[assessment])
        return GroundingRepairBatch(
            items=[
                GroundingRepairItem(
                    source_url=page.canonical_url,
                    summary=assessment.summary,
                    evidence_block_ids=["E001"],
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
        expected_section="市场观察",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert len(items) == 1
    assert items[0].body == f"近日，{page.body}"
    assert "回落0.7个百分点" in items[0].body
    assert "提高0.7个百分点" not in items[0].body
    assert len(records) == 1
    assert warnings == []


def test_ordinary_item_uses_event_date_from_evidence_instead_of_publish_date():
    page = WebCandidate(
        url="https://www.pbc.gov.cn/goutongjiaoliu/event.htm",
        canonical_url="https://www.pbc.gov.cn/goutongjiaoliu/event.htm",
        title="中国人民银行召开金融支持小微企业座谈会",
        site="pbc.gov.cn",
        publisher="中国人民银行",
        publish_date="2026-07-16",
        body=(
            "7月14日，中国人民银行召开金融支持小微企业座谈会，"
            "研究提升小微企业金融服务质效。"
        ),
    )
    assessment = ContentCandidateAssessment(
        source_url=page.canonical_url,
        include=True,
        section="监管动态",
        title=page.title,
        summary="中国人民银行召开座谈会，研究提升小微企业金融服务质效。",
        evidence_block_ids=["E001"],
        score=9,
        reason="金融监管部门支持小微企业的重要部署",
    )
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: ContentAssessmentBatch(items=[assessment])
        },
    )

    items, records, warnings = _ordinary_items(
        [page],
        gateway,
        expected_section="监管动态",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert warnings == []
    assert items[0].body.startswith("7月14日，")
    assert not items[0].body.startswith("7月16日，")
    assert records[0].occurrence_date == "2026-07-14"
    assert records[0].publish_date == "2026-07-16"


def test_ordinary_item_uses_recent_when_evidence_has_no_event_date():
    page = WebCandidate(
        url="https://www.nfra.gov.cn/cn/view/pages/recent.htm",
        canonical_url="https://www.nfra.gov.cn/cn/view/pages/recent.htm",
        title="金融监管总局部署提升银行业服务质效",
        site="nfra.gov.cn",
        publisher="国家金融监督管理总局",
        publish_date="2026-07-16",
        body="金融监管总局部署提升银行业服务质效，要求完善小微企业金融服务。",
    )
    assessment = ContentCandidateAssessment(
        source_url=page.canonical_url,
        include=True,
        section="监管动态",
        title=page.title,
        summary="金融监管总局部署提升银行业服务质效，完善小微企业金融服务。",
        occurrence_date="2026-07-16",
        evidence_block_ids=["E001"],
        score=9,
        reason="银行业经营管理相关监管部署",
    )
    gateway = ToolGateway(
        allowed_tools=("llm_writer",),
        tools={
            "llm_writer": lambda payload: ContentAssessmentBatch(items=[assessment])
        },
    )

    items, records, warnings = _ordinary_items(
        [page],
        gateway,
        expected_section="监管动态",
        retrieved_at="2026-07-20T10:00:00+08:00",
    )

    assert warnings == []
    assert items[0].body.startswith("近日，")
    assert not items[0].body.startswith("7月16日，")
    assert records[0].occurrence_date == ""


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
    assert warnings == [
        "党政要闻第1批候选评估连续返回空判断"
        "（候选：国务院部署促进消费重点工作）"
    ]


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


def test_collect_source_feed_pages_builds_nfra_article_from_official_api_record():
    feed_url = (
        "https://www.nfra.gov.cn/cn/static/data/DocInfo/SelectDocByItemIdAndChild/"
        "data_itemId=915,pageIndex=1,pageSize=18.json"
    )
    content_url = (
        "https://www.nfra.gov.cn/cn/static/data/DocInfo/SelectByDocId/"
        "data_docId=123.json"
    )
    article_url = (
        "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?"
        "docId=123&itemId=915&generaltype=0"
    )
    read_urls: list[str] = []

    def web_reader(url: str):
        read_urls.append(url)
        if url == feed_url:
            return {
                "url": feed_url,
                "canonical_url": feed_url,
                "links": [],
                "records": [
                    {
                        "docId": "123",
                        "docSubtitle": "金融监管总局部署银行业重点工作",
                        "publishDate": "2026-07-10 17:30:00",
                        "generaltype": "0",
                    }
                ],
            }
        assert url == content_url
        return {
            "url": content_url,
            "canonical_url": content_url,
            "site": "nfra.gov.cn",
            "publisher": "nfra.gov.cn",
            "title": "金融监管总局部署银行业重点工作",
            "publish_date": "2026-07-10",
            "date_extracted_from": "json:publishDate",
            "text": "金融监管总局召开专题会议，部署银行经营管理和风险防控重点工作。",
        }

    gateway = ToolGateway(
        allowed_tools=("web_reader",),
        tools={"web_reader": web_reader},
    )
    feed_spec = {
        "publisher": "国家金融监督管理总局",
        "feed_url": feed_url,
        "feed_adapter": "nfra_docinfo",
        "item_id": "915",
        "article_url_template": (
            "https://www.nfra.gov.cn/cn/view/pages/ItemDetail.html?"
            "docId={docId}&itemId={itemId}&generaltype={generaltype}"
        ),
        "content_url_template": (
            "https://www.nfra.gov.cn/cn/static/data/DocInfo/SelectByDocId/"
            "data_docId={docId}.json"
        ),
    }

    pages, warnings = _collect_source_feed_pages(
        [feed_spec],
        gateway,
        period_start=datetime(2026, 7, 6).date(),
        period_end=datetime(2026, 7, 12).date(),
        source_section="监管动态",
    )

    assert read_urls == [feed_url, content_url]
    assert [page.canonical_url for page in pages] == [article_url]
    assert pages[0].publisher == "国家金融监督管理总局"
    assert pages[0].body.startswith("金融监管总局召开专题会议")
    assert warnings == []


def test_collect_regulatory_pages_only_searches_missing_fixed_source_groups():
    pbc_feed = "https://www.pbc.gov.cn/news/index.html"
    pbc_article = "https://www.pbc.gov.cn/news/1.html"
    nfra_feed = "https://www.nfra.gov.cn/news.json"
    csrc_feed = "https://www.csrc.gov.cn/news.json"
    csrc_article = "https://www.csrc.gov.cn/news/1.html"
    search_calls: list[str] = []

    feed_specs = [
        {"feed_url": pbc_feed, "source_group": "pbc"},
        {"feed_url": nfra_feed, "source_group": "nfra"},
        {"feed_url": csrc_feed, "source_group": "csrc"},
    ]
    query_groups = [
        ("pbc", ("人行查询",)),
        ("nfra", ("总局查询",)),
        ("csrc", ("证监会查询",)),
        ("safe", ("外汇局查询",)),
    ]

    def search(query: str, max_results: int = 5):
        search_calls.append(query)
        return []

    def web_reader(url: str):
        if url == pbc_feed:
            return {
                "url": url,
                "links": [
                    {
                        "url": pbc_article,
                        "title": "中国人民银行部署货币政策重点工作",
                        "publish_date": "2026-07-10",
                    }
                ],
            }
        if url == csrc_feed:
            return {
                "url": url,
                "links": [
                    {
                        "url": csrc_article,
                        "title": "中国证监会部署资本市场监管重点工作",
                        "publish_date": "2026-07-09",
                    }
                ],
            }
        if url == nfra_feed:
            return {"url": url, "links": []}
        return {
            "url": url,
            "canonical_url": url,
            "site": url.split("/")[2],
            "publisher": "监管机构",
            "title": (
                "中国人民银行部署货币政策重点工作"
                if url == pbc_article
                else "中国证监会部署资本市场监管重点工作"
            ),
            "publish_date": "2026-07-10",
            "date_extracted_from": "meta:publishdate",
            "text": "监管机构召开专题会议，部署金融监管、经营管理和风险防控重点工作。",
        }

    gateway = ToolGateway(
        allowed_tools=("search", "web_reader"),
        tools={"search": search, "web_reader": web_reader},
    )

    pages, warnings = _collect_regulatory_pages(
        query_groups,
        feed_specs,
        gateway,
        period_start=datetime(2026, 7, 6).date(),
        period_end=datetime(2026, 7, 12).date(),
    )

    assert [page.canonical_url for page in pages] == [pbc_article, csrc_article]
    assert search_calls == ["总局查询", "外汇局查询"]
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


def test_frontier_chinese_summary_is_compacted_at_sentence_boundary():
    compacted = _compact_chinese_summary("第一句说明研究结论。" * 40)

    assert len(compacted) <= 260
    assert compacted.endswith("。")


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
    assert frontier.items[0].title == "利率传导对银行净息差的影响"
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
