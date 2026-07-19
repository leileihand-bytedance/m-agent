from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.platform.tools import ToolGateway
from skills.internal_weekly.dates import parse_flexible_date
from skills.internal_weekly.output import render_review_markdown, write_review_bundle
from skills.internal_weekly.schema import (
    ContentAssessmentBatch,
    ContentCandidateAssessment,
    FrontierSelection,
    InternalWeeklyResult,
    MarketEvidenceBundle,
    MarketSeriesEvidence,
    SourceRecord,
    WebCandidate,
    WeeklyItem,
    WeeklySection,
)
from skills.internal_weekly.selection import (
    CANONICAL_MARKET_NAMES,
    MARKET_NAME_ALIASES,
    SECTION_ORDER,
    build_monday_market_update,
    build_market_item,
    build_pending_market_update,
    calculate_weekly_window,
    classify_section,
    extract_requested_publication_date,
    is_allowed_party_building_content,
    is_self_bank_content,
    validate_frontier_selection,
)
from skills.internal_weekly.source_registry import peer_query_names
from skills.internal_weekly.source_policy import (
    candidate_allowed,
    date_in_period,
    domain_allowed,
    domain_allowed_for_section,
    hostname,
    is_research_source,
)


MAX_PAGES_PER_GROUP = 30
MAX_ITEMS_PER_ORDINARY_SECTION = 6
MAX_PAGES_PER_ASSESSMENT_BATCH = 5
MAX_ORDINARY_BODY_CHARS = 12000
WEB_READER_ATTEMPTS = 2
MARKET_CLOSE_TIME = time(15, 0)
_INVISIBLE_LAYOUT_MARKS = str.maketrans("", "", "\u00ad\u200b\u200c\u200d\u2060\ufeff")
_WEEKLY_MARKET_MARKERS = (
    "上周",
    "本周",
    "一周",
    "单周",
    "周涨",
    "周跌",
    "weekly",
    "week",
)
_ENGLISH_MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _evidence_in_body(excerpt: str, body: str) -> bool:
    """只忽略网页排版不可见字符和空白差异，保留文字、数字与标点校验。"""

    def normalize(value: str) -> str:
        return " ".join(
            value.translate(_INVISIBLE_LAYOUT_MARKS).replace("\xa0", " ").split()
        )

    normalized_excerpt = normalize(excerpt)
    return bool(normalized_excerpt) and normalized_excerpt in normalize(body)


def _resolve_market_evidence_excerpt(
    evidence: MarketSeriesEvidence,
    body: str,
) -> str | None:
    """优先保留模型原句；不匹配时按固定指数名和同值数字回源取原文分句。"""
    excerpt = evidence.evidence_excerpt.strip()
    if _evidence_in_body(excerpt, body):
        return excerpt

    index_code = evidence.index_code.upper()
    index_name = CANONICAL_MARKET_NAMES.get(index_code, evidence.index_name)
    index_aliases = tuple(
        dict.fromkeys(
            (
                index_name,
                evidence.index_name,
                *MARKET_NAME_ALIASES.get(index_code, ()),
            )
        )
    )
    if evidence.reported_change_pct is not None:
        targets = [abs(float(evidence.reported_change_pct))]
        require_percent = True
    elif evidence.start_close is not None and evidence.end_close is not None:
        targets = [float(evidence.start_close), float(evidence.end_close)]
        require_percent = False
    else:
        return None

    for match in re.finditer(r"[^。；;！？!?\n]+[。；;！？!?\n]?", body):
        clause = match.group(0).strip()
        clause_casefold = clause.casefold()
        if not any(alias.casefold() in clause_casefold for alias in index_aliases) or (
            require_percent and "%" not in clause
        ):
            continue
        numbers = [
            float(token.replace(",", ""))
            for token in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", clause)
        ]
        if all(any(abs(number - target) < 0.0005 for number in numbers) for target in targets):
            return clause
    return None


def _normalize_market_evidence_mode(
    evidence: MarketSeriesEvidence,
) -> MarketSeriesEvidence:
    """来源已直接披露涨跌幅时，清除模型多余返回的起止收盘值。"""
    if evidence.reported_change_pct is None:
        return evidence
    if evidence.start_close is None and evidence.end_close is None:
        return evidence
    return evidence.model_copy(update={"start_close": None, "end_close": None})


def _last_weekday(period_end: date) -> date:
    return period_end - timedelta(days=max(0, period_end.weekday() - 4))


def _normalize_reported_market_period(
    evidence: MarketSeriesEvidence,
    page: WebCandidate,
    *,
    publication_date: date,
    period_start: date,
    period_end: date,
) -> MarketSeriesEvidence:
    """直接披露的周涨跌幅使用任务统计周，不能误用周评文章发布日期。"""
    if evidence.reported_change_pct is None or not evidence.scope.startswith("weekly_"):
        return evidence
    context = f"{page.title}\n{evidence.evidence_excerpt}".lower()
    if not any(marker in context for marker in _WEEKLY_MARKET_MARKERS):
        return evidence
    weekly_end = _last_weekday(period_end)
    if weekly_end >= publication_date:
        return evidence
    return evidence.model_copy(
        update={
            "start_date": period_start.isoformat(),
            "end_date": weekly_end.isoformat(),
        }
    )


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
    source_section: str | None = None,
    max_results_per_query: int = 5,
) -> tuple[list[WebCandidate], list[str]]:
    search_results: list[dict[str, object]] = []
    warnings: list[str] = []
    for query in queries:
        try:
            results = tools.call("search", query, max_results=max_results_per_query)
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
        if source_section and not domain_allowed_for_section(url, source_section):
            continue
        if require_research and not is_research_source(url):
            continue
        page: object | None = None
        for attempt in range(WEB_READER_ATTEMPTS):
            try:
                page = tools.call("web_reader", url)
                break
            except Exception:
                if attempt == WEB_READER_ATTEMPTS - 1:
                    warnings.append(f"网页读取失败：{hostname(url) or '未知来源'}")
        if page is None:
            continue
        if not isinstance(page, dict):
            warnings.append(f"网页读取结果格式无效：{url}")
            continue
        candidate = _build_candidate(item, page)
        if source_section and not domain_allowed_for_section(
            candidate.canonical_url or candidate.url,
            source_section,
        ):
            continue
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


def _ordinary_query_groups(
    period_start: date,
    period_end: date,
) -> list[tuple[str, tuple[str, ...]]]:
    date_range = _format_date_range(period_start, period_end)
    peer_queries: list[str] = []
    for category, suffix in (
        ("domestic_digital_banks", "经营 产品 科技 风险管理 合作"),
        (
            "international_digital_banks",
            "digital bank earnings product technology risk management",
        ),
        ("bank_technology_subsidiaries", "银行科技子公司 产品 技术 经营 合作"),
    ):
        names = peer_query_names(category)
        for offset in range(0, len(names), 5):
            peer_queries.append(
                f"{' '.join(names[offset:offset + 5])} {suffix} {date_range}"
            )
    return [
        (
            "党政要闻",
            (
                "中国政府网 国务院常务会议 宏观经济 金融 银行经营 "
                f"重要政策 原文 {date_range}",
                "新华社 中共中央 中央政治局 国务院 宏观经济 金融 "
                f"重要部署 原文 {date_range}",
                "人民网 中共中央 习近平 金融工作 党的建设 "
                f"重要部署 原文 {date_range}",
            ),
        ),
        (
            "监管动态",
            (
                f"中国人民银行 {date_range} 发布 货币政策 金融统计 风险提示 原文",
                f"国家金融监督管理总局 {date_range} 发布 监管 规定 风险 会议",
                f"中国证监会 {date_range} 发布 资本市场 监管 规定 风险提示",
                f"国家外汇管理局 {date_range} 发布 外汇政策 跨境资金 监管统计",
                "金融监管部门 党委 党组 党建 全面从严治党 "
                f"原文 {date_range}",
            ),
        ),
        (
            "同业动向",
            tuple(peer_queries),
        ),
        (
            "市场观察",
            (
                "中国证券报 证券时报 第一财经 宏观经济 金融市场 利率 汇率 "
                f"债券 理财 资管 银行业影响 {date_range}",
            ),
        ),
    ]


def _ordinary_queries(period_start: date, period_end: date) -> list[str]:
    return [
        query
        for _, queries in _ordinary_query_groups(period_start, period_end)
        for query in queries
    ]


def _market_queries(
    publication_date: date,
    period_start: date,
    period_end: date,
) -> list[str]:
    return [
        query
        for _, queries in _market_query_groups(publication_date, period_start, period_end)
        for query in queries
    ]


def _market_query_groups(
    publication_date: date,
    period_start: date,
    period_end: date,
) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    weekly_range = _format_date_range(period_start, period_end)
    monday = f"{publication_date.year}年{publication_date.month}月{publication_date.day}日"
    last_trading_day = period_end - timedelta(days=max(0, period_end.weekday() - 4))
    market_close = (
        f"{last_trading_day.year}年{last_trading_day.month}月{last_trading_day.day}日"
    )
    return [
        (
            ("weekly_a",),
            (
                "新华财经 A股一周回顾 上证指数 深证成指 创业板指 "
                f"周涨跌幅 {market_close} {weekly_range}",
                "中国证券报 证券时报 A股一周 上证指数 深证成指 创业板指 "
                f"收盘 周涨跌幅 {market_close}",
            ),
        ),
        (
            ("monday_a",),
            (
                f"{monday} A股收盘 沪指 深成指 创业板指 涨跌幅",
                f"{monday} A股收评 三大指数 收盘",
            ),
        ),
        (
            ("weekly_hk",),
            (
                f"港股 {market_close} 收盘 本周 恒生指数 恒生科技指数 "
                "恒生中国企业指数 周涨跌幅",
                "港股一周复盘 恒生指数 恒生科技指数 恒生中国企业指数 "
                f"{market_close} 周涨幅",
            ),
        ),
        (
            ("weekly_us",),
            (
                f"Wall Street week ended July {last_trading_day.day} "
                f"{last_trading_day.year} Dow Jones Nasdaq S&P 500 weekly percent change",
                "新华财经 一周要闻 全球市场 本周回顾 美股 道琼斯 纳斯达克 "
                f"标普500 周涨跌幅 {market_close} {weekly_range}",
            ),
        ),
    ]


def _frontier_queries(
    period_start: date,
    period_end: date,
    *,
    fallback: bool = False,
) -> list[str]:
    marker = "近30日补充" if fallback else "统计期优先"
    report_date = f"{period_end.year}年{period_end.month}月{period_end.day}日"
    month_window = (
        f"{_ENGLISH_MONTH_NAMES[period_start.month]} "
        f"{_ENGLISH_MONTH_NAMES[period_end.month]} {period_end.year}"
    )
    return [
        (
            "BIS working paper bulletin banking finance digital payments "
            f"研究报告 {marker} "
            f"{_format_date_range(period_start, period_end)}"
        ),
        (
            "BIS latest bulletin working paper banking digital payments "
            f"研究报告 {report_date}"
        ),
        (
            f"BIS Annual Economic Report {period_end.year} banking digital money "
            f"financial system 研究报告 {month_window}"
        ),
        f"BIS report banking digital payments 研究报告 {month_window}",
        (
            "IMF World Bank working paper banking finance financial market "
            f"研究报告 {marker} "
            f"{_format_date_range(period_start, period_end)}"
        ),
    ]


def _materials(
    pages: list[WebCandidate],
    *,
    max_body_chars: int | None = None,
) -> list[dict[str, str]]:
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
                f"原文链接：{page.canonical_url}\n\n正文：\n"
                f"{page.body[:max_body_chars] if max_body_chars else page.body}"
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
    expected_section: str,
    retrieved_at: str,
) -> tuple[list[WeeklyItem], list[SourceRecord], list[str]]:
    if not pages:
        if expected_section == "市场观察":
            return [], [], ["市场观察（资本市场综述以外）未找到合格候选材料"]
        return [], [], [f"{expected_section}未找到通过日期和来源校验的候选材料"]
    assessed: list[
        tuple[ContentCandidateAssessment, dict[str, WebCandidate]]
    ] = []
    warnings: list[str] = []
    for batch_index, offset in enumerate(
        range(0, len(pages), MAX_PAGES_PER_ASSESSMENT_BATCH),
        start=1,
    ):
        page_batch = pages[offset : offset + MAX_PAGES_PER_ASSESSMENT_BATCH]
        page_map = {page.canonical_url: page for page in page_batch}
        try:
            result = tools.call(
                "llm_writer",
                {
                    "task": "internal_weekly_content_assessment",
                    "skill_id": "internal_weekly",
                    "output_type": ContentAssessmentBatch,
                    "prompt_path": "prompts/assess.md",
                    "target_section": expected_section,
                    "instruction": (
                        f"本次只筛选“{expected_section}”，其他板块材料必须排除。"
                        "周报服务于微众银行内部管理团队和部门，优先选择与银行经营管理、"
                        "宏观经济金融、风险管理、数字化经营直接相关的信息。"
                        "党建仅保留党中央层面或金融监管部门自身部署，其他部委党建排除。"
                        "只形成事实摘要，并返回可在原文逐字核对的证据句。"
                    ),
                    "materials": _materials(
                        page_batch,
                        max_body_chars=MAX_ORDINARY_BODY_CHARS,
                    ),
                },
            )
            batch = (
                result
                if isinstance(result, ContentAssessmentBatch)
                else ContentAssessmentBatch.model_validate(result)
            )
        except Exception:
            warnings.append(f"{expected_section}第{batch_index}批候选评估失败")
            continue
        assessed.extend((assessment, page_map) for assessment in batch.items)
    items: list[WeeklyItem] = []
    records: list[SourceRecord] = []
    seen_urls: set[str] = set()
    for assessment, page_map in sorted(
        assessed,
        key=lambda pair: pair[0].score,
        reverse=True,
    ):
        if not assessment.include:
            continue
        if assessment.section != expected_section:
            continue
        page = page_map.get(assessment.source_url)
        if page is None:
            warnings.append(f"模型返回了候选集外的来源：{assessment.source_url}")
            continue
        if page.canonical_url in seen_urls:
            continue
        evidence = assessment.evidence_excerpt.strip()
        if not _evidence_in_body(evidence, page.body):
            warnings.append(f"《{assessment.title}》缺少可逐字核验的证据句，已排除")
            continue
        section = classify_section(assessment.title, page.body)
        if section != expected_section:
            warnings.append(
                f"《{assessment.title}》无法通过{expected_section}确定性分类校验，已排除"
            )
            continue
        if not is_allowed_party_building_content(
            assessment.title, page.body, expected_section
        ):
            warnings.append(f"《{assessment.title}》不符合党建收录边界，已排除")
            continue
        if expected_section == "同业动向" and is_self_bank_content(
            assessment.title, page.body
        ):
            warnings.append(f"《{assessment.title}》属于微众银行自身动态，不作为同业收录")
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
        seen_urls.add(page.canonical_url)
        if len(items) >= MAX_ITEMS_PER_ORDINARY_SECTION:
            break
    return items, records, warnings


def _market_item(
    page_groups: list[tuple[tuple[str, ...], list[WebCandidate]]],
    tools: ToolGateway,
    *,
    publication_date: date,
    period_start: date,
    period_end: date,
    retrieved_at: str,
    monday_pending: bool = False,
    update_only: bool = False,
) -> tuple[WeeklyItem | None, list[SourceRecord], list[str]]:
    missing_page_groups = ["/".join(scopes) for scopes, pages in page_groups if not pages]
    if missing_page_groups:
        return None, [], [
            f"资本市场综述缺少可读取的数据页：{', '.join(missing_page_groups)}"
        ]

    series = []
    contexts = []
    warnings: list[str] = []
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
                    "网页发布日期不能当作行情结束日期；周评在周一发布时，"
                    "仍应填写它实际描述的上一周交易日期。"
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
        validated_series: list[MarketSeriesEvidence] = []
        for evidence in group_bundle.series:
            evidence = _normalize_market_evidence_mode(evidence)
            page = group_page_map.get(evidence.source_url)
            if page is None:
                raise ValueError(f"行情证据不在候选数据页中：{evidence.source_url}")
            excerpt = _resolve_market_evidence_excerpt(evidence, page.body)
            if excerpt is None:
                raise ValueError(f"行情证据无法在原页面逐字核对：{evidence.index_name}")
            evidence = evidence.model_copy(update={"evidence_excerpt": excerpt})
            validated_series.append(
                _normalize_reported_market_period(
                    evidence,
                    page,
                    publication_date=publication_date,
                    period_start=period_start,
                    period_end=period_end,
                )
            )
        for context in group_bundle.contexts:
            page = group_page_map.get(context.source_url)
            if page is None or not _evidence_in_body(context.evidence_excerpt, page.body):
                warnings.append(
                    f"行情背景句证据无法逐字核对，已排除：{context.scope}"
                )
                continue
            contexts.append(context)
        series.extend(validated_series)

    bundle = MarketEvidenceBundle(series=series, contexts=contexts)
    if update_only:
        item, records = build_monday_market_update(
            bundle,
            publication_date=publication_date,
            retrieved_at=retrieved_at,
        )
    else:
        item, records = build_market_item(
            bundle,
            publication_date=publication_date,
            retrieved_at=retrieved_at,
            monday_pending=monday_pending,
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
    return item, records, warnings


def _frontier_item(
    pages: list[WebCandidate],
    tools: ToolGateway,
    *,
    retrieved_at: str,
    period_start: date,
    period_end: date,
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
    try:
        selected_date = parse_flexible_date(
            selection.publish_date,
            default_year=period_end.year,
        )
        page_date = parse_flexible_date(
            page.publish_date,
            default_year=period_end.year,
        )
    except ValueError as exc:
        raise ValueError("前沿观点发布日期无法核验") from exc
    if selected_date != page_date:
        raise ValueError("前沿观点发布日期与来源页面不一致")
    if not period_start <= selected_date <= period_end:
        raise ValueError("前沿观点发布日期超出允许的报告窗口")
    passages = validate_frontier_selection(selection, page.body)
    warnings = []
    if len(passages) != len(selection.selected_passages):
        warnings.append("前沿观点已剔除网页截断导致的非完整段落")
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
    return item, [record], warnings


def _digest(result: InternalWeeklyResult) -> str:
    payload = {
        "generation_mode": result.generation_mode,
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


def is_market_update_request(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？,.!?；;:：]+", "", text)
    return (
        "资本市场综述" in normalized
        and any(marker in normalized for marker in ("今天", "今日", "当日"))
        and any(marker in normalized for marker in ("生成", "更新", "补充", "补一下"))
    )


def _run_market_update(
    inputs: dict[str, object],
    tools: ToolGateway,
    *,
    current: datetime,
    target_date: date,
) -> InternalWeeklyResult:
    period_end = target_date - timedelta(days=1)
    period_start = period_end - timedelta(days=6)
    retrieved_at = current.astimezone().isoformat()
    pending = target_date == current.date() and current.time() < MARKET_CLOSE_TIME
    warnings: list[str] = []
    source_records: list[SourceRecord] = []
    item: WeeklyItem | None
    if pending:
        item = build_pending_market_update(target_date)
        warnings.append("今日A股收盘数据待15:00收盘后更新")
    else:
        monday_queries = next(
            queries
            for scopes, queries in _market_query_groups(target_date, period_start, period_end)
            if scopes == ("monday_a",)
        )
        pages, search_warnings = _collect_pages(
            list(monday_queries),
            tools,
            source_section="市场观察",
            max_results_per_query=10,
        )
        warnings.extend(search_warnings)
        try:
            item, records, item_warnings = _market_item(
                [(("monday_a",), pages)],
                tools,
                publication_date=target_date,
                period_start=period_start,
                period_end=period_end,
                retrieved_at=retrieved_at,
                update_only=True,
            )
            source_records.extend(records)
            warnings.extend(item_warnings)
        except Exception as exc:
            item = None
            warnings.append(f"今日资本市场综述未通过完整性校验：{exc}")

    section_items = [item] if item is not None else []
    ready = item is not None and not pending
    result = InternalWeeklyResult(
        generation_mode="market_update",
        title=f"今日资本市场综述更新（{target_date.isoformat()}）",
        publication_date=target_date.isoformat(),
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        sections=[WeeklySection(name="市场观察", items=section_items)],
        source_records=source_records,
        sources=[record.url for record in source_records],
        warnings=warnings,
        ready_for_approval=ready,
        message=(
            "已生成今日资本市场综述更新块和溯源清单，可人工核对后替换原占位。"
            if ready
            else (
                "当前尚未收盘，已生成醒目待更新占位；请15:00收盘后再次生成。"
                if pending
                else "今日行情资料或校验项不完整，已保留待核事项。"
            )
        ),
    )
    result.draft_version = _digest(result)
    result.body = render_review_markdown(result)
    output_dir = str(inputs.get("output_dir") or "").strip()
    if output_dir:
        result.output_file, result.manifest_file = write_review_bundle(result, output_dir)
    return result


def run(inputs: dict[str, object], tools: ToolGateway) -> InternalWeeklyResult:
    """生成可人工核对的内参周报内容稿和溯源清单，不生成 Word。"""
    current = _now(inputs)
    instruction = str(inputs.get("text") or "").strip()
    requested = extract_requested_publication_date(instruction)
    if is_market_update_request(instruction):
        target_date = requested or current.date()
        period_end = target_date - timedelta(days=1)
        period_start = period_end - timedelta(days=6)
        if target_date > current.date():
            return _clarification(
                "不能生成未来日期的资本市场综述，请改为已经到达的日期。",
                target_date,
                period_start,
                period_end,
            )
        return _run_market_update(
            inputs,
            tools,
            current=current,
            target_date=target_date,
        )

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

    monday_pending = (
        current.date() == publication_date and current.time() < MARKET_CLOSE_TIME
    )

    retrieved_at = current.astimezone().isoformat()
    ordinary_page_groups: list[tuple[str, list[WebCandidate]]] = []
    warnings: list[str] = []
    for section_name, queries in _ordinary_query_groups(period_start, period_end):
        pages, group_warnings = _collect_pages(
            list(queries),
            tools,
            period_start=period_start,
            period_end=period_end,
            source_section=section_name,
            max_results_per_query=(
                10 if section_name in {"党政要闻", "监管动态"} else 5
            ),
        )
        ordinary_page_groups.append((section_name, pages))
        warnings.extend(group_warnings)
    market_page_groups: list[tuple[tuple[str, ...], list[WebCandidate]]] = []
    market_search_warnings: list[str] = []
    for required_scopes, queries in _market_query_groups(
        publication_date, period_start, period_end
    ):
        if monday_pending and required_scopes == ("monday_a",):
            continue
        group_pages, group_warnings = _collect_pages(
            queries,
            tools,
            source_section="市场观察",
            max_results_per_query=10,
        )
        market_page_groups.append((required_scopes, group_pages))
        market_search_warnings.extend(group_warnings)
    frontier_window_start = period_end - timedelta(days=29)
    frontier_pages, frontier_search_warnings = _collect_pages(
        _frontier_queries(frontier_window_start, period_end, fallback=True),
        tools,
        period_start=frontier_window_start,
        period_end=period_end,
        require_research=True,
        source_section="前沿观点",
        max_results_per_query=10,
    )
    weekly_frontier_pages = [
        page
        for page in frontier_pages
        if date_in_period(page.publish_date, period_start, period_end)
    ]
    if weekly_frontier_pages:
        frontier_pages = weekly_frontier_pages
    elif frontier_pages:
        warnings.append("前沿观点使用近30日兜底报告，发布日期不在本期统计周")
    warnings.extend(market_search_warnings)
    warnings.extend(frontier_search_warnings)
    if monday_pending:
        warnings.append("今日A股收盘数据待15:00收盘后更新")

    ordinary_items: list[WeeklyItem] = []
    source_records: list[SourceRecord] = []
    for section_name, pages in ordinary_page_groups:
        try:
            items, records, item_warnings = _ordinary_items(
                pages,
                tools,
                expected_section=section_name,
                retrieved_at=retrieved_at,
            )
            ordinary_items.extend(items)
            source_records.extend(records)
            warnings.extend(item_warnings)
        except Exception as exc:
            warnings.append(f"{section_name}内容评估失败：{exc}")

    market_item: WeeklyItem | None = None
    try:
        market_item, records, item_warnings = _market_item(
            market_page_groups,
            tools,
            publication_date=publication_date,
            period_start=period_start,
            period_end=period_end,
            retrieved_at=retrieved_at,
            monday_pending=monday_pending,
        )
        source_records.extend(records)
        warnings.extend(item_warnings)
    except Exception as exc:
        warnings.append(f"资本市场综述未通过完整性校验：{exc}")

    frontier_item: WeeklyItem | None = None
    try:
        frontier_item, records, item_warnings = _frontier_item(
            frontier_pages,
            tools,
            retrieved_at=retrieved_at,
            period_start=frontier_window_start,
            period_end=period_end,
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
        WeeklySection(name=name, items=items_by_section[name]) for name in SECTION_ORDER
    ]

    for section_name in ("党政要闻", "监管动态", "同业动向"):
        if not items_by_section[section_name]:
            warnings.append(f"{section_name}暂无通过筛选和溯源校验的条目")

    deduped_records: dict[str, SourceRecord] = {}
    for record in source_records:
        deduped_records[record.source_id] = record
    ready = (
        all(items_by_section[name] for name in ("党政要闻", "监管动态", "同业动向"))
        and market_item is not None
        and frontier_item is not None
        and not monday_pending
    )
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
            else (
                "已生成上周内容核对稿；今日A股收盘数据待15:00收盘后更新，"
                "暂不生成洁净版本。"
                if monday_pending
                else "资料或校验项不完整，已保留待核事项，暂不生成洁净版本。"
            )
        ),
    )
    result.draft_version = _digest(result)
    result.body = render_review_markdown(result)
    output_dir = str(inputs.get("output_dir") or "").strip()
    if output_dir:
        result.output_file, result.manifest_file = write_review_bundle(result, output_dir)
    return result
