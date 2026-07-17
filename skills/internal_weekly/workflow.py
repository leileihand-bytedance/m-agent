from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.platform.tools import ToolGateway
from skills.internal_weekly.output import render_review_markdown, write_review_bundle
from skills.internal_weekly.schema import (
    ContentAssessmentBatch,
    FrontierSelection,
    InternalWeeklyResult,
    MarketEvidenceBundle,
    SourceRecord,
    WebCandidate,
    WeeklyItem,
    WeeklySection,
)
from skills.internal_weekly.selection import (
    SECTION_ORDER,
    build_market_item,
    calculate_weekly_window,
    classify_section,
    extract_requested_publication_date,
    validate_frontier_selection,
)
from skills.internal_weekly.source_policy import (
    candidate_allowed,
    date_in_period,
    domain_allowed,
    hostname,
    is_research_source,
)


MAX_PAGES_PER_GROUP = 30


def _now(inputs: dict[str, object]) -> datetime:
    override = inputs.get("now")
    if isinstance(override, datetime):
        if override.tzinfo is None:
            return override.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return override.astimezone(ZoneInfo("Asia/Shanghai"))
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _source_id(url: str) -> str:
    return "src-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _build_candidate(search_item: dict[str, object], page: dict[str, object]) -> WebCandidate:
    url = str(page.get("url") or search_item.get("url") or "").strip()
    canonical_url = str(page.get("canonical_url") or url).strip()
    return WebCandidate(
        url=url,
        canonical_url=canonical_url,
        title=str(page.get("title") or search_item.get("title") or "").strip(),
        site=str(page.get("site") or "").strip(),
        publisher=str(page.get("publisher") or page.get("site") or "").strip(),
        publish_date=str(page.get("publish_date") or "").strip(),
        date_extracted_from=str(page.get("date_extracted_from") or "").strip(),
        body=str(page.get("text") or "").strip(),
    )


def _collect_pages(
    queries: list[str],
    tools: ToolGateway,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    require_research: bool = False,
) -> tuple[list[WebCandidate], list[str]]:
    search_results: list[dict[str, object]] = []
    warnings: list[str] = []
    for query in queries:
        try:
            results = tools.call("search", query, max_results=5)
        except Exception:
            warnings.append(f"检索失败：{query}")
            continue
        if isinstance(results, list):
            search_results.extend(item for item in results if isinstance(item, dict))

    pages: list[WebCandidate] = []
    seen: set[str] = set()
    for item in search_results:
        url = str(item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        if not domain_allowed(url):
            continue
        if require_research and not is_research_source(url):
            continue
        try:
            page = tools.call("web_reader", url)
        except Exception:
            warnings.append(f"网页读取失败：{hostname(url) or '未知来源'}")
            continue
        if not isinstance(page, dict):
            warnings.append(f"网页读取结果格式无效：{url}")
            continue
        candidate = _build_candidate(item, page)
        allowed, _ = candidate_allowed(
            candidate,
            period_start=period_start,
            period_end=period_end,
            require_research=require_research,
        )
        if allowed:
            pages.append(candidate)
        if len(pages) >= MAX_PAGES_PER_GROUP:
            break
    return pages, warnings


def _format_date_range(period_start: date, period_end: date) -> str:
    return (
        f"{period_start.year}年{period_start.month}月{period_start.day}日"
        f"至{period_end.year}年{period_end.month}月{period_end.day}日"
    )


def _ordinary_queries(period_start: date, period_end: date) -> list[str]:
    date_range = _format_date_range(period_start, period_end)
    return [
        f"中国政府网 人民网 新华网 国务院 金融 重要会议 政策 原文 {date_range}",
        (
            f"中国人民银行 发布 金融政策 公开市场 金融统计 原文 {date_range}"
        ),
        (
            "国家金融监督管理总局 中国证监会 国家外汇管理局 "
            f"发布 金融政策 监管动态 原文 {date_range}"
        ),
        (
            "中国证券报 证券时报 第一财经 银行业 民营银行 数字银行 "
            f"同业动向 市场动态 {date_range}"
        ),
    ]


def _market_queries(
    publication_date: date,
    period_start: date,
    period_end: date,
) -> list[str]:
    return [
        query
        for _, query in _market_query_groups(publication_date, period_start, period_end)
    ]


def _market_query_groups(
    publication_date: date,
    period_start: date,
    period_end: date,
) -> list[tuple[tuple[str, ...], str]]:
    weekly_range = _format_date_range(period_start, period_end)
    monday = f"{publication_date.year}年{publication_date.month}月{publication_date.day}日"
    return [
        (
            ("weekly_a", "weekly_us"),
            "新华财经 一周要闻 全球市场 本周回顾 A股 美股 上证指数 "
            f"深证成指 创业板指 道琼斯 纳斯达克 标普500 {weekly_range}",
        ),
        (
            ("monday_a",),
            "证券时报 中国证券报 第一财经 A股收评 上证指数 深证成指 "
            f"创业板指 收盘 涨跌幅 {monday}",
        ),
        (
            ("weekly_hk",),
            "21世纪经济报道 上海证券报 南方财经 港股周评 恒生指数 "
            f"恒生科技指数 恒生中国企业指数 周涨跌幅 {weekly_range}",
        ),
    ]


def _frontier_queries(
    period_start: date,
    period_end: date,
    *,
    fallback: bool = False,
) -> list[str]:
    marker = "近30日补充" if fallback else "统计期优先"
    return [
        (
            "BIS working paper bulletin banking finance digital payments "
            f"研究报告 {marker} "
            f"{_format_date_range(period_start, period_end)}"
        ),
        (
            "IMF World Bank working paper banking finance financial market "
            f"研究报告 {marker} "
            f"{_format_date_range(period_start, period_end)}"
        ),
    ]


def _materials(pages: list[WebCandidate]) -> list[dict[str, str]]:
    return [
        {
            "type": "web_page",
            "source": "web_reader",
            "source_label": page.publisher or page.site,
            "title": page.title,
            "url": page.canonical_url,
            "publish_date": page.publish_date,
            "text": (
                f"标题：{page.title}\n发布日期：{page.publish_date}\n"
                f"原文链接：{page.canonical_url}\n\n正文：\n{page.body}"
            ),
        }
        for page in pages
    ]


def _record_from_page(
    page: WebCandidate,
    *,
    retrieved_at: str,
    source_type: str,
    evidence: list[str],
    source_location: str = "网页正文",
) -> SourceRecord:
    return SourceRecord(
        source_id=_source_id(page.canonical_url),
        title=page.title,
        publisher=page.publisher or page.site,
        publish_date=page.publish_date,
        url=page.canonical_url,
        retrieved_at=retrieved_at,
        source_type=source_type,
        source_location=source_location,
        evidence_excerpts=evidence,
        content_sha256=hashlib.sha256(page.body.encode("utf-8")).hexdigest(),
    )


def _ordinary_items(
    pages: list[WebCandidate],
    tools: ToolGateway,
    *,
    retrieved_at: str,
) -> tuple[list[WeeklyItem], list[SourceRecord], list[str]]:
    if not pages:
        return [], [], ["未找到通过日期和来源校验的普通板块材料"]
    result = tools.call(
        "llm_writer",
        {
            "task": "internal_weekly_content_assessment",
            "skill_id": "internal_weekly",
            "output_type": ContentAssessmentBatch,
            "prompt_path": "prompts/assess.md",
            "instruction": "只评估材料是否入选并形成事实摘要；必须返回可在原文逐字核对的证据句。",
            "materials": _materials(pages),
        },
    )
    batch = result if isinstance(result, ContentAssessmentBatch) else ContentAssessmentBatch.model_validate(result)
    page_map = {page.canonical_url: page for page in pages}
    items: list[WeeklyItem] = []
    records: list[SourceRecord] = []
    warnings: list[str] = []
    for assessment in sorted(batch.items, key=lambda item: item.score, reverse=True):
        if not assessment.include:
            continue
        page = page_map.get(assessment.source_url)
        if page is None:
            warnings.append(f"模型返回了候选集外的来源：{assessment.source_url}")
            continue
        evidence = assessment.evidence_excerpt.strip()
        if not evidence or evidence not in page.body:
            warnings.append(f"《{assessment.title}》缺少可逐字核验的证据句，已排除")
            continue
        section = classify_section(assessment.title, page.body)
        if section == "前沿观点":
            warnings.append(f"《{assessment.title}》不能作为普通材料进入前沿观点")
            continue
        source_record = _record_from_page(
            page,
            retrieved_at=retrieved_at,
            source_type="news",
            evidence=[evidence],
        )
        item_id = "item-" + hashlib.sha256(
            f"{section}|{assessment.title}|{page.canonical_url}".encode("utf-8")
        ).hexdigest()[:12]
        items.append(
            WeeklyItem(
                item_id=item_id,
                section=section,
                title=assessment.title.strip() or page.title,
                body=assessment.summary.strip(),
                content_mode="summary",
                source_ids=[source_record.source_id],
            )
        )
        records.append(source_record)
    return items, records, warnings


def _market_item(
    page_groups: list[tuple[tuple[str, ...], list[WebCandidate]]],
    tools: ToolGateway,
    *,
    publication_date: date,
    period_start: date,
    period_end: date,
    retrieved_at: str,
) -> tuple[WeeklyItem | None, list[SourceRecord], list[str]]:
    missing_page_groups = ["/".join(scopes) for scopes, pages in page_groups if not pages]
    if missing_page_groups:
        return None, [], [
            f"资本市场综述缺少可读取的数据页：{', '.join(missing_page_groups)}"
        ]

    series = []
    contexts = []
    page_map: dict[str, WebCandidate] = {}
    for required_scopes, pages in page_groups:
        page_map.update({page.canonical_url: page for page in pages})
        required_label = "、".join(required_scopes)
        result = tools.call(
            "llm_writer",
            {
                "task": "internal_weekly_market_extraction",
                "skill_id": "internal_weekly",
                "output_type": MarketEvidenceBundle,
                "prompt_path": "prompts/market.md",
                "required_scopes": list(required_scopes),
                "instruction": (
                    f"本次只返回 {required_label}，不要返回其他 scope。"
                    "只提取页面明确列示的指数涨跌幅，或起止收盘值和背景原句。"
                    "页面已直接披露本期涨跌幅时填 reported_change_pct，禁止倒算收盘值；"
                    "页面只披露起止收盘值时填 start_close、end_close，禁止自行计算涨跌幅。"
                    f"weekly_a、weekly_hk、weekly_us 使用 {period_start.isoformat()} "
                    f"至 {period_end.isoformat()} 内实际交易日；"
                    f"monday_a 必须使用 {publication_date.isoformat()} 当日数据。"
                    "所有 start_date、end_date 必须输出 YYYY-MM-DD。"
                ),
                "materials": _materials(pages),
            },
        )
        group_bundle = (
            result
            if isinstance(result, MarketEvidenceBundle)
            else MarketEvidenceBundle.model_validate(result)
        )
        unexpected_scopes = {
            evidence.scope
            for evidence in [*group_bundle.series, *group_bundle.contexts]
            if evidence.scope not in required_scopes
        }
        if unexpected_scopes:
            raise ValueError(
                f"行情分组返回了未请求的 scope：{', '.join(sorted(unexpected_scopes))}"
            )
        group_page_map = {page.canonical_url: page for page in pages}
        for evidence in [*group_bundle.series, *group_bundle.contexts]:
            page = group_page_map.get(evidence.source_url)
            if page is None:
                raise ValueError(f"行情证据不在候选数据页中：{evidence.source_url}")
            excerpt = evidence.evidence_excerpt.strip()
            if not excerpt or excerpt not in page.body:
                label = (
                    evidence.index_name
                    if hasattr(evidence, "index_name")
                    else evidence.scope
                )
                raise ValueError(f"行情证据无法在原页面逐字核对：{label}")
        series.extend(group_bundle.series)
        contexts.extend(group_bundle.contexts)

    bundle = MarketEvidenceBundle(series=series, contexts=contexts)
    item, records = build_market_item(
        bundle,
        publication_date=publication_date,
        retrieved_at=retrieved_at,
    )
    for index, record in enumerate(records):
        page = page_map[record.url]
        records[index] = record.model_copy(
            update={
                "publisher": page.publisher or page.site,
                "publish_date": page.publish_date,
                "content_sha256": hashlib.sha256(page.body.encode("utf-8")).hexdigest(),
            }
        )
    return item, records, []


def _frontier_item(
    pages: list[WebCandidate],
    tools: ToolGateway,
    *,
    retrieved_at: str,
) -> tuple[WeeklyItem | None, list[SourceRecord], list[str]]:
    if not pages:
        return None, [], ["前沿观点未找到统计期内或近30日可核验的研究报告"]
    result = tools.call(
        "llm_writer",
        {
            "task": "internal_weekly_frontier_selection",
            "skill_id": "internal_weekly",
            "output_type": FrontierSelection,
            "prompt_path": "prompts/frontier.md",
            "instruction": "选择一份研究报告，并只返回页面中逐字存在的连续原文段落；禁止改写。",
            "materials": _materials(pages),
        },
    )
    selection = result if isinstance(result, FrontierSelection) else FrontierSelection.model_validate(result)
    page_map = {page.canonical_url: page for page in pages}
    page = page_map.get(selection.source_url)
    if page is None:
        raise ValueError("前沿观点返回了候选集外的研报来源")
    passages = validate_frontier_selection(selection, page.body)
    record = _record_from_page(
        page,
        retrieved_at=retrieved_at,
        source_type="research_report",
        evidence=passages,
        source_location=selection.source_location,
    ).model_copy(
        update={
            "title": selection.title,
            "publisher": selection.institution,
            "publish_date": selection.publish_date,
        }
    )
    item = WeeklyItem(
        item_id="frontier-" + hashlib.sha256(selection.source_url.encode("utf-8")).hexdigest()[:12],
        section="前沿观点",
        title=selection.title,
        body="\n\n".join(passages),
        content_mode="report_extract",
        source_ids=[record.source_id],
    )
    return item, [record], []


def _digest(result: InternalWeeklyResult) -> str:
    payload = {
        "publication_date": result.publication_date,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "sections": [section.model_dump(mode="json") for section in result.sections],
        "sources": [
            record.model_dump(mode="json", exclude={"retrieved_at"})
            for record in result.source_records
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _clarification(message: str, publication_date: date, period_start: date, period_end: date) -> InternalWeeklyResult:
    return InternalWeeklyResult(
        publication_date=publication_date.isoformat(),
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        needs_clarification=True,
        message=message,
    )


def run(inputs: dict[str, object], tools: ToolGateway) -> InternalWeeklyResult:
    """生成可人工核对的内参周报内容稿和溯源清单，不生成 Word。"""
    current = _now(inputs)
    instruction = str(inputs.get("text") or "").strip()
    requested = extract_requested_publication_date(instruction)
    if requested is not None:
        publication_date = requested
        period_end = publication_date - timedelta(days=1)
        period_start = period_end - timedelta(days=6)
        if publication_date.weekday() != 0:
            return _clarification(
                "内参周报出版日必须是周一，请改为相应周一日期。",
                publication_date,
                period_start,
                period_end,
            )
        if publication_date > current.date():
            return _clarification(
                "不能生成未来出版日的内参周报，请改为已经到达的周一日期。",
                publication_date,
                period_start,
                period_end,
            )
    else:
        publication_date, period_start, period_end = calculate_weekly_window(current)

    if current.date() == publication_date and current.time() < time(15, 30):
        return _clarification(
            "资本市场综述必须包含周一A股收盘数据，请在当日15:30后再生成。",
            publication_date,
            period_start,
            period_end,
        )

    retrieved_at = current.astimezone().isoformat()
    ordinary_pages, warnings = _collect_pages(
        _ordinary_queries(period_start, period_end),
        tools,
        period_start=period_start,
        period_end=period_end,
    )
    market_page_groups: list[tuple[tuple[str, ...], list[WebCandidate]]] = []
    market_search_warnings: list[str] = []
    for required_scopes, query in _market_query_groups(
        publication_date, period_start, period_end
    ):
        group_pages, group_warnings = _collect_pages([query], tools)
        market_page_groups.append((required_scopes, group_pages))
        market_search_warnings.extend(group_warnings)
    frontier_window_start = publication_date - timedelta(days=30)
    frontier_pages, frontier_search_warnings = _collect_pages(
        _frontier_queries(frontier_window_start, publication_date, fallback=True),
        tools,
        period_start=frontier_window_start,
        period_end=publication_date,
        require_research=True,
    )
    weekly_frontier_pages = [
        page
        for page in frontier_pages
        if date_in_period(page.publish_date, period_start, period_end)
    ]
    if weekly_frontier_pages:
        frontier_pages = weekly_frontier_pages
    warnings.extend(market_search_warnings)
    warnings.extend(frontier_search_warnings)

    ordinary_items: list[WeeklyItem] = []
    source_records: list[SourceRecord] = []
    try:
        items, records, item_warnings = _ordinary_items(
            ordinary_pages, tools, retrieved_at=retrieved_at
        )
        ordinary_items.extend(items)
        source_records.extend(records)
        warnings.extend(item_warnings)
    except Exception as exc:
        warnings.append(f"普通板块内容评估失败：{exc}")

    market_item: WeeklyItem | None = None
    try:
        market_item, records, item_warnings = _market_item(
            market_page_groups,
            tools,
            publication_date=publication_date,
            period_start=period_start,
            period_end=period_end,
            retrieved_at=retrieved_at,
        )
        source_records.extend(records)
        warnings.extend(item_warnings)
    except Exception as exc:
        warnings.append(f"资本市场综述未通过完整性校验：{exc}")

    frontier_item: WeeklyItem | None = None
    try:
        frontier_item, records, item_warnings = _frontier_item(
            frontier_pages, tools, retrieved_at=retrieved_at
        )
        source_records.extend(records)
        warnings.extend(item_warnings)
    except Exception as exc:
        warnings.append(f"前沿观点未通过原文校验：{exc}")

    items_by_section: dict[str, list[WeeklyItem]] = {name: [] for name in SECTION_ORDER}
    for item in ordinary_items:
        items_by_section[item.section].append(item)
    if market_item is not None:
        items_by_section["市场观察"].insert(0, market_item)
    if frontier_item is not None:
        items_by_section["前沿观点"].append(frontier_item)
    sections = [
        WeeklySection(name=name, items=items_by_section[name])
        for name in SECTION_ORDER
        if items_by_section[name]
    ]

    deduped_records: dict[str, SourceRecord] = {}
    for record in source_records:
        deduped_records[record.source_id] = record
    ready = bool(ordinary_items) and market_item is not None and frontier_item is not None
    result = InternalWeeklyResult(
        title=f"内参周报（{publication_date.isoformat()}）",
        publication_date=publication_date.isoformat(),
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        sections=sections,
        source_records=list(deduped_records.values()),
        sources=[record.url for record in deduped_records.values()],
        warnings=warnings,
        ready_for_approval=ready,
        message=(
            "已生成内容核对稿和溯源清单，请完成人工核对。"
            if ready
            else "资料或校验项不完整，已保留待核事项，暂不生成洁净版本。"
        ),
    )
    result.draft_version = _digest(result)
    result.body = render_review_markdown(result)
    output_dir = str(inputs.get("output_dir") or "").strip()
    if output_dir:
        result.output_file, result.manifest_file = write_review_bundle(result, output_dir)
    return result
