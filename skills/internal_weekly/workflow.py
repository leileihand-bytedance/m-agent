from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import re
from datetime import date, datetime, time, timedelta
from typing import TypeVar
from zoneinfo import ZoneInfo

from app.platform.tools import ToolGateway
from skills.internal_weekly.dates import parse_flexible_date
from skills.internal_weekly.docx_output import (
    generate_internal_weekly_docx,
    is_explicit_word_approval,
    parse_approved_review,
    requests_clean_word,
)
from skills.internal_weekly.output import render_review_markdown, write_review_bundle
from skills.internal_weekly.schema import (
    ContentAssessmentBatch,
    ContentCandidateAssessment,
    FrontierSelection,
    GroundingRepairBatch,
    InternalWeeklyResult,
    MarketEvidenceBundle,
    MarketSeriesEvidence,
    PartyEventSynthesis,
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
from skills.internal_weekly.source_registry import (
    market_observation_topic_specs,
    peer_activity_topic_specs,
    peer_query_names,
    peer_source_tier,
    section_source_feed_urls,
    section_source_feed_specs,
    section_source_tier,
)
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
MAX_MARKET_OBSERVATION_ITEMS = 5
MIN_MARKET_OBSERVATION_SCORE = 7.0
MAX_PEER_ACTIVITY_ITEMS = 5
MIN_PEER_ACTIVITY_SCORE = 7.0
MAX_PAGES_PER_ASSESSMENT_BATCH = 5
MAX_EMPTY_ASSESSMENT_ATTEMPTS = 2
MAX_ORDINARY_BODY_CHARS = 12000
MAX_PARTY_EVENT_SOURCES = 3
MAX_PARTY_EVENT_BODY_CHARS = 600
MAX_PARTY_EVENT_SYNTHESIS_CHARS = 420
WEB_READER_ATTEMPTS = 2
MAX_PARALLEL_SECTION_TASKS = 5
MARKET_CLOSE_TIME = time(15, 0)
PARTY_FEED_PRIORITY_MARKERS = (
    "人工智能",
    "科学技术",
    "科技创新",
    "科技强国",
    "数字经济",
    "数据要素",
    "新质生产力",
    "扩大内需",
    "促进消费",
    "小微企业",
    "民营经济",
    "营商环境",
)
PARTY_FEED_RELEVANCE_MARKERS = (
    "宏观经济",
    "高质量发展",
    "经济",
    "发展",
    "科技",
    "创新",
    "数字",
    "数据",
    "内需",
    "消费",
    "小微",
    "民营",
    "就业",
    "营商",
    "金融",
    "改革",
    "开放",
    "外贸",
    "产业",
    "投资",
    "企业",
    "市场",
    "风险",
    "党建",
    "党的建设",
    "全面从严治党",
)
PARTY_FEED_CENTRAL_MARKERS = (
    "习近平",
    "中共中央",
    "中央政治局",
    "国务院",
    "李强",
    "全国人大",
    "全国政协",
)
PARTY_FEED_SECONDARY_MARKERS = ("评论员", "述评", "学习快评", "解读")
PARTY_EVENT_LOW_VALUE_MARKERS = (
    "贡献智慧力量",
    "综合评论",
    "回顾大会",
)
PARTY_MAJOR_EVENT_SUFFIXES = ("大会", "论坛", "峰会", "年会", "博览会")
PARTY_EVENT_TITLE_NOISE = (
    "人民日报评论员",
    "学习贯彻",
    "习近平总书记",
    "习近平",
    "重要讲话",
    "讲话",
    "侧记",
    "纪实",
    "反响",
    "评论员",
    "第十一次",
    "十一大",
)
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
_REGULATOR_DOMAIN_SUBJECTS = (
    ("pbc.gov.cn", "中国人民银行"),
    ("nfra.gov.cn", "国家金融监督管理总局"),
    ("csrc.gov.cn", "中国证监会"),
    ("safe.gov.cn", "国家外汇管理局"),
)
_REGULATOR_TEXT_SUBJECTS = (
    ("国家金融监督管理总局", "国家金融监督管理总局"),
    ("金融监管总局", "国家金融监督管理总局"),
    ("中国人民银行", "中国人民银行"),
    ("人民银行", "中国人民银行"),
    ("央行", "中国人民银行"),
    ("中国证监会", "中国证监会"),
    ("证监会", "中国证监会"),
    ("国家外汇管理局", "国家外汇管理局"),
    ("外汇局", "国家外汇管理局"),
)
_RESEARCH_INSTITUTION_NAMES = {
    "bis.org": "国际清算银行",
    "imf.org": "国际货币基金组织",
    "worldbank.org": "世界银行",
    "fsb.org": "金融稳定理事会",
    "federalreserve.gov": "美国联邦储备委员会",
    "ecb.europa.eu": "欧洲中央银行",
    "bankofengland.co.uk": "英格兰银行",
}

SectionTaskValue = TypeVar("SectionTaskValue")


@dataclass(frozen=True)
class _SectionTaskOutput:
    items: tuple[WeeklyItem, ...] = ()
    source_records: tuple[SourceRecord, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PartyEventPart:
    title: str
    summary: str
    source_url: str


@dataclass(frozen=True)
class _GroundedSummary:
    summary: str
    evidence_excerpts: tuple[str, ...]


def _run_parallel_section_tasks(
    tasks: Mapping[str, Callable[[], SectionTaskValue]],
) -> tuple[dict[str, SectionTaskValue], dict[str, Exception]]:
    """Run the five independent weekly sections concurrently, then restore key order."""
    if not tasks:
        return {}, {}
    worker_count = min(MAX_PARALLEL_SECTION_TASKS, len(tasks))
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="internal-weekly-section",
    ) as executor:
        futures = {
            section_name: executor.submit(task)
            for section_name, task in tasks.items()
        }
        results: dict[str, SectionTaskValue] = {}
        failures: dict[str, Exception] = {}
        for section_name in tasks:
            try:
                results[section_name] = futures[section_name].result()
            except Exception as exc:
                failures[section_name] = exc
    return results, failures


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
        publish_date=str(
            page.get("publish_date") or search_item.get("publish_date") or ""
        ).strip(),
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


def _party_feed_link_score(title: str) -> int:
    if not any(
        marker in title
        for marker in (*PARTY_FEED_PRIORITY_MARKERS, *PARTY_FEED_RELEVANCE_MARKERS)
    ):
        return 0
    score = 1
    score += 4 * sum(marker in title for marker in PARTY_FEED_PRIORITY_MARKERS)
    score += 2 * sum(marker in title for marker in PARTY_FEED_CENTRAL_MARKERS)
    score -= 3 * sum(marker in title for marker in PARTY_FEED_SECONDARY_MARKERS)
    return max(score, 1)


def _party_assessment_rank(assessment: ContentCandidateAssessment) -> float:
    """同一事件跨批次比较时，中央正式原文优先于评论、侧记和解读。"""
    title = assessment.title.strip()
    preference = 0
    if re.match(r"^(习近平[:：]|中共中央(?:\s+国务院)?|国务院(?:关于|办公厅))", title):
        preference += 3
    elif title.startswith("习近平"):
        preference += 2
    if any(marker in title for marker in PARTY_FEED_SECONDARY_MARKERS + ("侧记", "纪实", "反响")):
        preference -= 3
    if any(marker in f"{title}\n{assessment.summary}" for marker in PARTY_EVENT_LOW_VALUE_MARKERS):
        preference -= 4
    return assessment.score + preference


def _ordinary_assessment_rank(
    assessment: ContentCandidateAssessment,
    page_map: dict[str, WebCandidate],
    expected_section: str,
) -> float:
    if expected_section == "党政要闻":
        return _party_assessment_rank(assessment)
    if expected_section not in {"同业动向", "市场观察"}:
        return assessment.score
    page = page_map.get(assessment.source_url)
    if page is None:
        return assessment.score
    if expected_section == "同业动向":
        tier = peer_source_tier(page.canonical_url or page.url)
    else:
        tier = section_source_tier(page.canonical_url or page.url, "市场观察")
    return assessment.score + (0.5 if tier == "primary" else 0.0)


def _normalized_party_event_title(title: str) -> str:
    normalized = title
    for marker in PARTY_EVENT_TITLE_NOISE:
        normalized = normalized.replace(marker, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", normalized)


def _party_titles_describe_same_event(left: str, right: str) -> bool:
    """用标题二元片段识别同一中央事件的正式稿、评论稿和侧记稿。"""
    normalized_left = _normalized_party_event_title(left)
    normalized_right = _normalized_party_event_title(right)
    if min(len(normalized_left), len(normalized_right)) < 12:
        return False
    left_pairs = {normalized_left[index : index + 2] for index in range(len(normalized_left) - 1)}
    right_pairs = {
        normalized_right[index : index + 2] for index in range(len(normalized_right) - 1)
    }
    union = left_pairs | right_pairs
    if not union:
        return False
    return len(left_pairs & right_pairs) / len(union) >= 0.30


def _party_major_event_keys(title: str, body: str) -> set[str]:
    """提取有稳定专名的重大活动，用于把大会、论坛等多篇稿件合成一条。"""
    keys: set[str] = set()
    text = f"{title}\n{body[:800]}"
    suffix_pattern = "|".join(map(re.escape, PARTY_MAJOR_EVENT_SUFFIXES))
    for clause in re.split(r"[，。；：:！？!?、\n（）()]", text):
        clause = clause.strip()
        for match in re.finditer(suffix_pattern, clause):
            phrase = clause[max(0, match.start() - 28) : match.end()].strip()
            for marker in (
                "出席",
                "参加",
                "在",
                "于",
                "召开",
                "举办",
                "举行",
                "开幕",
                "期间",
                "回顾",
            ):
                position = phrase.rfind(marker)
                if position >= 0:
                    phrase = phrase[position + len(marker) :]
            phrase = re.sub(r"^(?:20\d{2}年?)", "", phrase)
            phrase = re.sub(r"^第[一二三四五六七八九十百千万0-9]+届", "", phrase)
            phrase = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", phrase)
            if 4 <= len(phrase) <= 20 and any(
                phrase.endswith(suffix) for suffix in PARTY_MAJOR_EVENT_SUFFIXES
            ):
                keys.add(phrase)
    return keys


def _merge_party_event_summary(existing: str, supplement: str) -> str | None:
    supplement = supplement.strip()
    if not supplement:
        return None
    normalized_existing = re.sub(r"\s+", "", existing)
    normalized_supplement = re.sub(r"\s+", "", supplement)
    if normalized_supplement in normalized_existing:
        return None
    combined = f"{existing.rstrip('。；; ')}；{supplement}"
    if len(combined) > MAX_PARTY_EVENT_BODY_CHARS:
        return None
    return combined


def _number_tokens(value: str) -> set[str]:
    return {
        token.replace(",", "")
        for token in re.findall(r"\d[\d,]*(?:\.\d+)?%?", value)
    }


def _synthesize_party_event(
    parts: list[_PartyEventPart],
    tools: ToolGateway,
) -> tuple[str, str] | None:
    if len(parts) < 2:
        return None
    grounded_text = "\n".join(
        f"{part.title}\n{part.summary}" for part in parts
    )
    try:
        result = tools.call(
            "llm_writer",
            {
                "task": "internal_weekly_party_event_synthesis",
                "skill_id": "internal_weekly",
                "output_type": PartyEventSynthesis,
                "prompt_path": "prompts/party_event_synthesis.md",
                "instruction": (
                    "把以下同一重大活动的互补正式稿综合为一个标题和一个连贯段落。"
                    "只使用输入事实，不按来源分段，不重复活动背景。"
                ),
                "materials": [
                    {
                        "source_url": part.source_url,
                        "title": part.title,
                        "summary": part.summary,
                    }
                    for part in parts
                ],
            },
        )
        synthesis = (
            result
            if isinstance(result, PartyEventSynthesis)
            else PartyEventSynthesis.model_validate(result)
        )
    except Exception:
        return None

    title = " ".join(synthesis.title.split()).strip()
    summary = re.sub(r"\s*\n+\s*", "", synthesis.summary).strip()
    if not title or not summary or len(summary) > MAX_PARTY_EVENT_SYNTHESIS_CHARS:
        return None
    grounded_numbers = _number_tokens(grounded_text)
    if not _number_tokens(f"{title}\n{summary}").issubset(grounded_numbers):
        return None
    return title, summary


def _regulatory_subject(page: WebCandidate, assessment: ContentCandidateAssessment) -> str:
    page_host = hostname(page.canonical_url or page.url)
    for domain, subject in _REGULATOR_DOMAIN_SUBJECTS:
        if page_host == domain or page_host.endswith(f".{domain}"):
            return subject
    text = f"{assessment.title}\n{assessment.summary}\n{page.title}\n{page.body[:1200]}"
    for marker, subject in _REGULATOR_TEXT_SUBJECTS:
        if marker in text:
            return subject
    return (page.publisher or page.site).strip()


def _normalize_regulatory_title(title: str, subject: str) -> str:
    normalized = title.strip()
    if not subject or not normalized.startswith("国新办"):
        return normalized
    topic = re.sub(
        r"^国新办(?:举行|召开)?(?:新闻发布会)?[：:\s，,]*",
        "",
        normalized,
    ).strip()
    return f"{subject}{topic}" if topic else subject


def _normalize_regulatory_summary(summary: str, subject: str) -> str:
    normalized = summary.strip()
    if not subject or "国新办" not in normalized:
        return normalized
    normalized = re.sub(
        r"国新办(?:举行|召开)?(?:新闻发布会)?[，,\s]*(?:中国人民银行|人民银行|央行|"
        r"国家金融监督管理总局|金融监管总局|中国证监会|证监会|"
        r"国家外汇管理局|外汇局)?",
        subject,
        normalized,
        count=1,
    )
    return normalized


def _collect_source_feed_pages(
    feed_sources: list[str | dict[str, str]],
    tools: ToolGateway,
    *,
    period_start: date,
    period_end: date,
    source_section: str,
) -> tuple[list[WebCandidate], list[str]]:
    """从登记的官方结构化列表发现文章，再按日期、主题和域名受限读取正文。"""
    warnings: list[str] = []
    link_candidates: list[tuple[int, dict[str, object]]] = []
    for feed_source in feed_sources:
        if isinstance(feed_source, dict):
            feed_spec = feed_source
            feed_url = str(feed_source.get("feed_url") or "").strip()
        else:
            feed_spec = {"feed_url": str(feed_source).strip()}
            feed_url = str(feed_source).strip()
        if not feed_url:
            continue
        if not domain_allowed_for_section(feed_url, source_section):
            continue
        feed_page: object | None = None
        for attempt in range(WEB_READER_ATTEMPTS):
            try:
                feed_page = tools.call("web_reader", feed_url)
                break
            except Exception:
                if attempt == WEB_READER_ATTEMPTS - 1:
                    warnings.append(f"固定信源列表读取失败：{hostname(feed_url) or '未知来源'}")
        if not isinstance(feed_page, dict):
            if feed_page is not None:
                warnings.append(f"固定信源列表格式无效：{hostname(feed_url) or '未知来源'}")
            continue
        links: object = feed_page.get("links")
        if not isinstance(links, list):
            warnings.append(f"固定信源列表缺少链接：{hostname(feed_url) or '未知来源'}")
            continue
        if feed_spec.get("feed_adapter") == "nfra_docinfo":
            records = feed_page.get("records")
            if not isinstance(records, list):
                warnings.append(f"固定信源列表缺少记录：{hostname(feed_url) or '未知来源'}")
                continue
            links = _nfra_feed_links(records, feed_spec)
        for link in links:
            if not isinstance(link, dict):
                continue
            url = str(link.get("url") or "").strip()
            title = str(link.get("title") or "").strip()
            publish_date = str(link.get("publish_date") or "").strip()
            if not url or not title:
                continue
            if not domain_allowed_for_section(url, source_section):
                continue
            if not date_in_period(publish_date, period_start, period_end):
                continue
            score = _party_feed_link_score(title) if source_section == "党政要闻" else 1
            if score <= 0:
                continue
            normalized_link = dict(link)
            normalized_link["_feed_url"] = feed_url
            normalized_link["_publisher"] = str(
                feed_spec.get("publisher") or feed_spec.get("name") or ""
            ).strip()
            link_candidates.append((score, normalized_link))

    pages: list[WebCandidate] = []
    seen: set[str] = set()
    for _, link in sorted(
        link_candidates,
        key=lambda pair: (
            pair[0],
            str(pair[1].get("publish_date") or ""),
            str(pair[1].get("title") or ""),
        ),
        reverse=True,
    ):
        url = str(link.get("url") or "").strip()
        fetch_url = str(link.get("fetch_url") or url).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        if not domain_allowed_for_section(fetch_url, source_section):
            continue
        page: object | None = None
        for attempt in range(WEB_READER_ATTEMPTS):
            try:
                page = tools.call("web_reader", fetch_url)
                break
            except Exception:
                if attempt == WEB_READER_ATTEMPTS - 1:
                    warnings.append(f"网页读取失败：{hostname(url) or '未知来源'}")
        if not isinstance(page, dict):
            continue
        normalized_page = dict(page)
        normalized_page["title"] = str(
            link.get("title") or normalized_page.get("title") or ""
        ).strip()
        normalized_page["publish_date"] = str(
            link.get("publish_date") or normalized_page.get("publish_date") or ""
        ).strip()
        source_feed_url = str(link.get("_feed_url") or "").strip()
        normalized_page["date_extracted_from"] = (
            f"official-feed:{hostname(source_feed_url) or 'unknown'}"
        )
        if link.get("_publisher"):
            normalized_page["publisher"] = str(link["_publisher"]).strip()
        if fetch_url != url:
            normalized_page["url"] = url
            normalized_page["canonical_url"] = url
        candidate = _build_candidate(link, normalized_page)
        if not domain_allowed_for_section(
            candidate.canonical_url or candidate.url,
            source_section,
        ):
            continue
        allowed, _ = candidate_allowed(
            candidate,
            period_start=period_start,
            period_end=period_end,
        )
        if allowed:
            pages.append(candidate)
        if len(pages) >= MAX_PAGES_PER_GROUP:
            break
    return pages, warnings


def _nfra_feed_links(
    records: list[object],
    feed_spec: dict[str, str],
) -> list[dict[str, str]]:
    """把总局官方 DocInfo 记录转换成人工可打开的原文链接与正文接口。"""
    article_template = str(feed_spec.get("article_url_template") or "").strip()
    content_template = str(feed_spec.get("content_url_template") or "").strip()
    item_id = str(feed_spec.get("item_id") or "").strip()
    if not article_template or not content_template or not item_id:
        return []

    links: list[dict[str, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        doc_id = str(record.get("docId") or "").strip()
        title = str(
            record.get("docSubtitle") or record.get("docTitle") or ""
        ).strip()
        publish_date = str(record.get("publishDate") or "").strip()
        generaltype = str(record.get("generaltype") or "0").strip() or "0"
        if not doc_id or not title or not publish_date:
            continue
        values = {
            "docId": doc_id,
            "itemId": item_id,
            "generaltype": generaltype,
        }
        try:
            article_url = article_template.format_map(values)
            content_url = content_template.format_map(values)
        except (KeyError, ValueError):
            continue
        links.append(
            {
                "title": title,
                "url": article_url,
                "fetch_url": content_url,
                "publish_date": publish_date,
            }
        )
    return links


def _collect_regulatory_pages(
    query_groups: list[tuple[str, tuple[str, ...]]],
    feed_specs: list[dict[str, str]],
    tools: ToolGateway,
    *,
    period_start: date,
    period_end: date,
) -> tuple[list[WebCandidate], list[str]]:
    """按机构先读固定官方入口；该机构无合格正文时才执行公开检索。"""
    specs_by_group: dict[str, list[dict[str, str]]] = {}
    for spec in feed_specs:
        source_group = str(spec.get("source_group") or "").strip()
        if source_group:
            specs_by_group.setdefault(source_group, []).append(spec)

    page_groups: list[list[WebCandidate]] = []
    warnings: list[str] = []
    for source_group, queries in query_groups:
        group_pages: list[WebCandidate] = []
        group_specs = specs_by_group.get(source_group, [])
        if group_specs:
            group_pages, group_warnings = _collect_source_feed_pages(
                group_specs,
                tools,
                period_start=period_start,
                period_end=period_end,
                source_section="监管动态",
            )
            warnings.extend(group_warnings)
        if not group_pages:
            group_pages, group_warnings = _collect_pages(
                list(queries),
                tools,
                period_start=period_start,
                period_end=period_end,
                source_section="监管动态",
                max_results_per_query=10,
            )
            warnings.extend(group_warnings)
        page_groups.append(group_pages)
    return _merge_candidate_pages(*page_groups), warnings


def _merge_candidate_pages(*groups: list[WebCandidate]) -> list[WebCandidate]:
    merged: list[WebCandidate] = []
    seen: set[str] = set()
    for pages in groups:
        for page in pages:
            if page.canonical_url in seen:
                continue
            seen.add(page.canonical_url)
            merged.append(page)
            if len(merged) >= MAX_PAGES_PER_GROUP:
                return merged
    return merged


def _merge_candidate_pages_balanced(*groups: list[WebCandidate]) -> list[WebCandidate]:
    """轮询合并主题候选，避免前几个搜索组耗尽总页面预算。"""
    merged: list[WebCandidate] = []
    seen: set[str] = set()
    max_group_size = max((len(group) for group in groups), default=0)
    for page_index in range(max_group_size):
        for pages in groups:
            if page_index >= len(pages):
                continue
            page = pages[page_index]
            if page.canonical_url in seen:
                continue
            seen.add(page.canonical_url)
            merged.append(page)
            if len(merged) >= MAX_PAGES_PER_GROUP:
                return merged
    return merged


def _format_date_range(period_start: date, period_end: date) -> str:
    return (
        f"{period_start.year}年{period_start.month}月{period_start.day}日"
        f"至{period_end.year}年{period_end.month}月{period_end.day}日"
    )


def _regulatory_query_groups(
    period_start: date,
    period_end: date,
) -> list[tuple[str, tuple[str, ...]]]:
    date_range = _format_date_range(period_start, period_end)
    return [
        (
            "pbc",
            (
                f"中国人民银行 {date_range} 发布 货币政策 金融统计 风险提示 党委 党建 原文",
            ),
        ),
        (
            "nfra",
            (
                f"国家金融监督管理总局 {date_range} 发布 监管 规定 风险 会议 党委 党建 原文",
            ),
        ),
        (
            "csrc",
            (
                f"中国证监会 {date_range} 发布 资本市场 监管 规定 风险提示 党委 党建 原文",
            ),
        ),
        (
            "safe",
            (
                f"国家外汇管理局 {date_range} 发布 外汇政策 跨境资金 监管统计 党委 党建 原文",
            ),
        ),
    ]


def _market_observation_query_groups(
    period_start: date,
    period_end: date,
) -> list[tuple[str, tuple[str, ...]]]:
    """按案例归纳的市场影响主题生成独立查询组。"""
    date_range = _format_date_range(period_start, period_end)
    groups: list[tuple[str, tuple[str, ...]]] = []
    for spec in market_observation_topic_specs():
        topic_id = str(spec.get("id") or "").strip()
        templates = spec.get("query_templates", ())
        if not topic_id or not isinstance(templates, tuple):
            continue
        queries = tuple(
            template.replace("{date_range}", date_range)
            for template in templates
            if isinstance(template, str) and template.strip()
        )
        if queries:
            groups.append((topic_id, queries))
    return groups


def _peer_activity_query_groups(
    period_start: date,
    period_end: date,
) -> list[tuple[str, tuple[str, ...]]]:
    """按机构类型和名单分片生成同业动向查询组。"""
    date_range = _format_date_range(period_start, period_end)
    groups: list[tuple[str, tuple[str, ...]]] = []
    for spec in peer_activity_topic_specs():
        topic_id = str(spec.get("id") or "").strip()
        category = str(spec.get("category") or "").strip()
        chunk_size = spec.get("chunk_size")
        templates = spec.get("query_templates", ())
        if (
            not topic_id
            or not category
            or not isinstance(chunk_size, int)
            or chunk_size <= 0
            or not isinstance(templates, tuple)
        ):
            continue
        names = peer_query_names(category)
        for chunk_index, offset in enumerate(range(0, len(names), chunk_size), start=1):
            entity_names = " ".join(names[offset : offset + chunk_size])
            queries = tuple(
                template.replace("{entity_names}", entity_names).replace(
                    "{date_range}",
                    date_range,
                )
                for template in templates
                if isinstance(template, str) and template.strip()
            )
            if queries:
                groups.append((f"{topic_id}_{chunk_index}", queries))
    return groups


def _ordinary_query_groups(
    period_start: date,
    period_end: date,
) -> list[tuple[str, tuple[str, ...]]]:
    date_range = _format_date_range(period_start, period_end)
    return [
        (
            "党政要闻",
            (
                "中国政府网 要闻列表 中共中央 国务院 中央重要会议 重大政策 "
                f"宏观经济 高质量发展 原文 {date_range}",
                "中国政府网 要闻列表 科技创新 人工智能 数字经济 数据要素 "
                f"新质生产力 原文 {date_range}",
                "中国政府网 要闻列表 扩大内需 促进消费 小微企业 民营经济 "
                f"营商环境 就业 原文 {date_range}",
                "新华社 中共中央 中央政治局 国务院 宏观经济 高质量发展 "
                f"科技创新 人工智能 重要部署 原文 {date_range}",
                "人民网 中共中央 习近平 党的建设 宏观经济 科技创新 "
                f"人工智能 促进消费 小微企业 重要部署 原文 {date_range}",
            ),
        ),
        (
            "监管动态",
            tuple(
                query
                for _, queries in _regulatory_query_groups(period_start, period_end)
                for query in queries
            ),
        ),
        (
            "同业动向",
            tuple(
                query
                for _, queries in _peer_activity_query_groups(
                    period_start,
                    period_end,
                )
                for query in queries
            ),
        ),
        (
            "市场观察",
            tuple(
                query
                for _, queries in _market_observation_query_groups(
                    period_start,
                    period_end,
                )
                for query in queries
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


def _evidence_block_map(
    body: str,
    *,
    max_body_chars: int = MAX_ORDINARY_BODY_CHARS,
) -> dict[str, str]:
    """把原文切成由程序持有的证据块，模型只选择编号，不再重新抄原句。"""
    source = body[:max_body_chars]
    blocks: list[str] = []
    for paragraph in re.split(r"\n+", source):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= 600:
            blocks.append(paragraph)
            continue
        sentences = [
            match.group(0).strip()
            for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", paragraph)
            if match.group(0).strip()
        ]
        current = ""
        for sentence in sentences:
            if current and len(current) + len(sentence) > 600:
                blocks.append(current)
                current = sentence
            else:
                current += sentence
        if current:
            blocks.append(current)
    return {
        f"E{index:03d}": block
        for index, block in enumerate(blocks, start=1)
    }


def _ordinary_materials(pages: list[WebCandidate]) -> list[dict[str, str]]:
    materials: list[dict[str, str]] = []
    for page in pages:
        evidence_blocks = _evidence_block_map(page.body)
        block_text = "\n".join(
            f"[{block_id}] {text}"
            for block_id, text in evidence_blocks.items()
        )
        materials.append(
            {
                "type": "web_page",
                "source": "web_reader",
                "source_label": page.publisher or page.site,
                "title": page.title,
                "url": page.canonical_url,
                "publish_date": page.publish_date,
                "text": (
                    f"标题：{page.title}\n发布日期：{page.publish_date}\n"
                    f"原文链接：{page.canonical_url}\n\n"
                    f"原文证据块：\n{block_text}"
                ),
            }
        )
    return materials


def _selected_evidence_blocks(
    block_ids: list[str],
    page: WebCandidate,
) -> tuple[str, ...]:
    if not block_ids or len(block_ids) > 3:
        return ()
    block_map = _evidence_block_map(page.body)
    selected: list[str] = []
    for block_id in dict.fromkeys(block_ids):
        block = block_map.get(block_id)
        if block is None:
            return ()
        selected.append(block)
    return tuple(selected)


def _numeric_fact_tokens(value: str) -> set[str]:
    pattern = (
        r"\d[\d,]*(?:\.\d+)?"
        r"(?:个百分点|个基点|基点|万亿美元|亿美元|万亿元|亿元|万元|"
        r"%|年|月|日|万|亿|人|个|家|项|倍|点)?"
    )
    normalized: set[str] = set()
    for year, month, day in re.findall(
        r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})",
        value,
    ):
        normalized.update(
            {
                f"{int(year)}年",
                f"{int(month)}月",
                f"{int(day)}日",
            }
        )
    for token in re.findall(pattern, value):
        match = re.fullmatch(r"([\d,]+(?:\.\d+)?)(.*)", token)
        if match is None:
            continue
        number, unit = match.groups()
        number = number.replace(",", "")
        if "." not in number:
            number = str(int(number))
        normalized.add(f"{number}{unit}")
    return normalized


def _summary_core_supported(
    summary: str,
    evidence_excerpts: tuple[str, ...],
    page: WebCandidate,
) -> bool:
    """允许概括压缩，但拦截数字、政策题名和增减方向等核心事实变化。"""
    normalized_summary = summary.strip()
    if not normalized_summary or not evidence_excerpts:
        return False
    evidence_text = "\n".join(evidence_excerpts)
    source_facts = f"{page.title}\n{page.publish_date}\n{evidence_text}"
    if not _numeric_fact_tokens(normalized_summary).issubset(
        _numeric_fact_tokens(source_facts)
    ):
        return False
    for policy_title in re.findall(r"《[^》]{2,80}》", normalized_summary):
        if policy_title not in page.body and policy_title not in page.title:
            return False

    up_markers = ("增长", "上升", "上涨", "增加", "提高", "回升", "走强")
    down_markers = ("下降", "下跌", "减少", "降低", "回落", "收缩", "走弱")
    summary_has_up = any(marker in normalized_summary for marker in up_markers)
    summary_has_down = any(marker in normalized_summary for marker in down_markers)
    evidence_has_up = any(marker in evidence_text for marker in up_markers)
    evidence_has_down = any(marker in evidence_text for marker in down_markers)
    if summary_has_up and evidence_has_down and not evidence_has_up:
        return False
    if summary_has_down and evidence_has_up and not evidence_has_down:
        return False
    specific_up = ("上升", "上涨", "增加", "提高", "回升", "走强")
    specific_down = ("下降", "下跌", "减少", "降低", "回落", "收缩", "走弱")
    if (
        any(marker in normalized_summary for marker in specific_up)
        and any(marker in evidence_text for marker in specific_down)
        and not any(marker in evidence_text for marker in specific_up)
    ):
        return False
    if (
        any(marker in normalized_summary for marker in specific_down)
        and any(marker in evidence_text for marker in specific_up)
        and not any(marker in evidence_text for marker in specific_down)
    ):
        return False
    return True


def _grounding_from_assessment(
    assessment: ContentCandidateAssessment,
    page: WebCandidate,
) -> _GroundedSummary | None:
    evidence_excerpts = _selected_evidence_blocks(
        assessment.evidence_block_ids,
        page,
    )
    if not evidence_excerpts:
        evidence = assessment.evidence_excerpt.strip()
        if _evidence_in_body(evidence, page.body):
            evidence_excerpts = (evidence,)
    if not _summary_core_supported(
        assessment.summary,
        evidence_excerpts,
        page,
    ):
        return None
    return _GroundedSummary(
        summary=assessment.summary.strip(),
        evidence_excerpts=evidence_excerpts,
    )


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


def _repair_ordinary_grounding(
    candidates: list[tuple[ContentCandidateAssessment, WebCandidate]],
    tools: ToolGateway,
) -> dict[str, _GroundedSummary]:
    """只允许模型重写摘要并选择程序编号，代码复核核心事实后才接纳。"""
    unique: dict[str, tuple[ContentCandidateAssessment, WebCandidate]] = {}
    for assessment, page in candidates:
        unique.setdefault(page.canonical_url, (assessment, page))
    pending = list(unique.values())
    repaired: dict[str, _GroundedSummary] = {}
    for offset in range(0, len(pending), MAX_PAGES_PER_ASSESSMENT_BATCH):
        batch = pending[offset : offset + MAX_PAGES_PER_ASSESSMENT_BATCH]
        try:
            result = tools.call(
                "llm_writer",
                {
                    "task": "internal_weekly_grounding_repair",
                    "skill_id": "internal_weekly",
                    "output_type": GroundingRepairBatch,
                    "prompt_path": "prompts/grounding_repair.md",
                    "instruction": (
                        "允许概括压缩，但人物、机构、时间、数字、单位、政策动作、"
                        "因果关系和增减方向必须由所选原文证据块直接支持。"
                    ),
                    "requests": [
                        {
                            "source_url": page.canonical_url,
                            "title": assessment.title,
                            "summary": assessment.summary,
                            "rejected_evidence_excerpt": assessment.evidence_excerpt,
                            "rejected_evidence_block_ids": assessment.evidence_block_ids,
                        }
                        for assessment, page in batch
                    ],
                    "materials": _ordinary_materials(
                        [page for _, page in batch]
                    ),
                },
            )
            repair_batch = (
                result
                if isinstance(result, GroundingRepairBatch)
                else GroundingRepairBatch.model_validate(result)
            )
        except Exception:
            continue
        page_map = {page.canonical_url: page for _, page in batch}
        for item in repair_batch.items:
            page = page_map.get(item.source_url)
            if page is None:
                continue
            evidence_excerpts = _selected_evidence_blocks(
                item.evidence_block_ids,
                page,
            )
            if _summary_core_supported(item.summary, evidence_excerpts, page):
                repaired[page.canonical_url] = _GroundedSummary(
                    summary=item.summary.strip(),
                    evidence_excerpts=evidence_excerpts,
                )
    return repaired


def _batch_candidate_titles(pages: list[WebCandidate]) -> str:
    return "；".join(page.title.strip() or page.canonical_url for page in pages)


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
    party_scope_instruction = (
        "党政要闻不能以是否出现‘金融’二字作为入选门槛；除宏观经济金融外，"
        "中央层面的高质量发展、科技创新、人工智能、数字经济、数据要素、"
        "新质生产力、扩大内需、促进消费、支持小微企业和民营经济、营商环境、"
        "就业等内容，只要对银行经营管理、客户经营、风险判断或数字化发展有明确"
        "参考价值，都可以入选。同一大会、论坛等重大活动可以返回最多3篇互补的"
        "正式成果稿，代码会合并为一条；重复评论、侧记和泛泛回顾不要入选。"
        if expected_section == "党政要闻"
        else ""
    )
    market_scope_instruction = (
        "市场观察除固定资本市场综述外，要覆盖可能改变增长、通胀、利率、汇率、"
        "流动性、风险偏好或银行资产负债环境的国内外重大事件，包括国内宏观数据，"
        "货币信用、利率汇率，财富与资管，主要央行和海外宏观数据，全球市场异常"
        "波动，地缘贸易能源冲击，以及创纪录IPO、重大违约等资本市场事件。每条"
        "include=true候选都必须给出从事件到上述至少一个影响渠道的直接事实链，"
        "并按影响范围、变化幅度、银行相关性、证据质量、本周新颖性五项各0至2分"
        "评分，总分不得低于7分。官方原始发布优先于媒体，同等事件只留信息最完整的一条。"
        "不设最低凑数要求，最多选5条；普通日评、轻微波动、未证实传闻、单一机构"
        "宣传，以及应归党政要闻或监管动态的政策部署、监管处罚一律排除。"
        if expected_section == "市场观察"
        else ""
    )
    peer_scope_instruction = (
        "同业动向只跟踪登记名单中的境内民营/数字银行、国际及香港数字银行、"
        "国内银行科技子公司。重点覆盖经营业绩、产品业务、科技与风控、战略合作、"
        "组织治理五类实质变化。每条include=true候选按可比相关性、变化重要性、"
        "战略信号、证据质量、经营启示五项各0至2分评分，总分不得低于7分，并在"
        "reason中说明对微众银行经营管理或竞争判断的参考意义。机构官网、投资者"
        "关系、年报和监管/交易所披露优先于媒体，同一事件只留来源最权威、信息最"
        "完整的一条。最多选5条，不设最低凑数要求；普通营销活动、优惠促销、获奖、"
        "一般会议、招聘、公益和没有实质产品/经营/技术变化的宣传稿一律排除。"
        if expected_section == "同业动向"
        else ""
    )
    for batch_index, offset in enumerate(
        range(0, len(pages), MAX_PAGES_PER_ASSESSMENT_BATCH),
        start=1,
    ):
        page_batch = pages[offset : offset + MAX_PAGES_PER_ASSESSMENT_BATCH]
        page_map = {page.canonical_url: page for page in page_batch}
        try:
            batch = ContentAssessmentBatch()
            for assessment_attempt in range(MAX_EMPTY_ASSESSMENT_ATTEMPTS):
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
                            f"{party_scope_instruction}"
                            f"{market_scope_instruction}"
                            f"{peer_scope_instruction}"
                            "党建仅保留党中央层面或金融监管部门自身部署，其他部委党建排除。"
                            "每一条候选都必须返回判断；不入选也要返回 include=false，不能漏答或返回空列表。"
                            "摘要可以概括压缩，但不得改变人物、机构、时间、数字、单位、"
                            "政策动作、因果关系或增减方向；证据优先返回1至3个原文证据块编号，"
                            "不要自行重抄证据文字。"
                        ),
                        "materials": _ordinary_materials(page_batch),
                    },
                )
                batch = (
                    result
                    if isinstance(result, ContentAssessmentBatch)
                    else ContentAssessmentBatch.model_validate(result)
                )
                if batch.items or assessment_attempt == MAX_EMPTY_ASSESSMENT_ATTEMPTS - 1:
                    break
        except Exception:
            warnings.append(
                f"{expected_section}第{batch_index}批候选评估失败"
                f"（候选：{_batch_candidate_titles(page_batch)}）"
            )
            continue
        if not batch.items:
            warnings.append(
                f"{expected_section}第{batch_index}批候选评估连续返回空判断"
                f"（候选：{_batch_candidate_titles(page_batch)}）"
            )
            continue
        assessed.extend((assessment, page_map) for assessment in batch.items)
    grounding_repairs = _repair_ordinary_grounding(
        [
            (assessment, page)
            for assessment, page_map in assessed
            if assessment.include
            and assessment.section == expected_section
            and (page := page_map.get(assessment.source_url)) is not None
            and _grounding_from_assessment(assessment, page) is None
        ],
        tools,
    )
    items: list[WeeklyItem] = []
    records: list[SourceRecord] = []
    seen_urls: set[str] = set()
    selected_party_event_titles: list[str] = []
    party_event_item_indexes: dict[str, int] = {}
    party_event_parts: dict[int, list[_PartyEventPart]] = {}
    for assessment, page_map in sorted(
        assessed,
        key=lambda pair: _ordinary_assessment_rank(
            pair[0],
            pair[1],
            expected_section,
        ),
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
        if (
            expected_section == "市场观察"
            and assessment.score < MIN_MARKET_OBSERVATION_SCORE
        ):
            warnings.append(
                f"《{assessment.title}》市场影响评分低于7分，已排除"
            )
            continue
        if (
            expected_section == "同业动向"
            and assessment.score < MIN_PEER_ACTIVITY_SCORE
        ):
            warnings.append(
                f"《{assessment.title}》同业价值评分低于7分，已排除"
            )
            continue
        grounding = (
            _grounding_from_assessment(assessment, page)
            or grounding_repairs.get(page.canonical_url)
        )
        if grounding is None:
            warnings.append(
                f"《{assessment.title}》摘要核心事实无法由原文证据支持，已排除"
            )
            continue
        assessment = assessment.model_copy(
            update={"summary": grounding.summary}
        )
        party_event_keys = (
            _party_major_event_keys(assessment.title, page.body)
            if expected_section == "党政要闻"
            else set()
        )
        matching_party_event_indexes = {
            party_event_item_indexes[key]
            for key in party_event_keys
            if key in party_event_item_indexes
        }
        section = classify_section(assessment.title, page.body)
        if section != expected_section and not (
            expected_section == "党政要闻" and matching_party_event_indexes
        ):
            warnings.append(
                f"《{assessment.title}》无法通过{expected_section}确定性分类校验，已排除"
            )
            continue
        if section != expected_section:
            section = expected_section
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
        if expected_section == "党政要闻":
            if matching_party_event_indexes:
                item_index = min(matching_party_event_indexes)
                existing_item = items[item_index]
                if any(
                    marker in f"{assessment.title}\n{assessment.summary}"
                    for marker in (
                        *PARTY_EVENT_LOW_VALUE_MARKERS,
                        *PARTY_FEED_SECONDARY_MARKERS,
                        "侧记",
                        "纪实",
                        "反响",
                    )
                ):
                    continue
                if len(existing_item.source_ids) >= MAX_PARTY_EVENT_SOURCES:
                    continue
                merged_body = _merge_party_event_summary(
                    existing_item.body,
                    assessment.summary,
                )
                if merged_body is None:
                    continue
                source_record = _record_from_page(
                    page,
                    retrieved_at=retrieved_at,
                    source_type="news",
                    evidence=list(grounding.evidence_excerpts),
                )
                items[item_index] = existing_item.model_copy(
                    update={
                        "body": merged_body,
                        "source_ids": [*existing_item.source_ids, source_record.source_id],
                    }
                )
                records.append(source_record)
                seen_urls.add(page.canonical_url)
                party_event_parts.setdefault(item_index, []).append(
                    _PartyEventPart(
                        title=assessment.title,
                        summary=assessment.summary,
                        source_url=page.canonical_url,
                    )
                )
                for key in party_event_keys:
                    party_event_item_indexes[key] = item_index
                continue
            if any(
                _party_titles_describe_same_event(assessment.title, selected_title)
                for selected_title in selected_party_event_titles
            ):
                continue
            if len(items) >= MAX_ITEMS_PER_ORDINARY_SECTION:
                continue
        item_title = assessment.title.strip() or page.title
        item_body = assessment.summary.strip()
        regulatory_subject = ""
        if expected_section == "监管动态":
            regulatory_subject = _regulatory_subject(page, assessment)
            item_title = _normalize_regulatory_title(item_title, regulatory_subject)
            item_body = _normalize_regulatory_summary(item_body, regulatory_subject)
        source_record = _record_from_page(
            page,
            retrieved_at=retrieved_at,
            source_type="news",
            evidence=list(grounding.evidence_excerpts),
        )
        if regulatory_subject:
            source_record = source_record.model_copy(
                update={"publisher": regulatory_subject}
            )
        item_id = "item-" + hashlib.sha256(
            f"{section}|{item_title}|{page.canonical_url}".encode("utf-8")
        ).hexdigest()[:12]
        items.append(
            WeeklyItem(
                item_id=item_id,
                section=section,
                title=item_title,
                body=item_body,
                content_mode="summary",
                source_ids=[source_record.source_id],
            )
        )
        records.append(source_record)
        seen_urls.add(page.canonical_url)
        if expected_section == "党政要闻":
            selected_party_event_titles.append(assessment.title)
            item_index = len(items) - 1
            if party_event_keys:
                party_event_parts[item_index] = [
                    _PartyEventPart(
                        title=assessment.title,
                        summary=assessment.summary,
                        source_url=page.canonical_url,
                    )
                ]
            for key in party_event_keys:
                party_event_item_indexes[key] = item_index
        item_limit = (
            MAX_MARKET_OBSERVATION_ITEMS
            if expected_section == "市场观察"
            else (
                MAX_PEER_ACTIVITY_ITEMS
                if expected_section == "同业动向"
                else MAX_ITEMS_PER_ORDINARY_SECTION
            )
        )
        if expected_section != "党政要闻" and len(items) >= item_limit:
            break
    for item_index, parts in party_event_parts.items():
        synthesis = _synthesize_party_event(parts, tools)
        if synthesis is None:
            continue
        title, summary = synthesis
        items[item_index] = items[item_index].model_copy(
            update={"title": title, "body": summary}
        )
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


def _has_chinese_text(value: str, *, minimum_chars: int = 4) -> bool:
    return len(re.findall(r"[\u4e00-\u9fff]", value)) >= minimum_chars


def _compact_chinese_summary(value: str, *, max_chars: int = 260) -> str:
    normalized = re.sub(r"\s+", "", value).strip()
    if len(normalized) <= max_chars:
        return normalized
    sentences = re.findall(r"[^。！？；]+[。！？；]?", normalized)
    selected: list[str] = []
    length = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if selected and length + len(sentence) > max_chars:
            break
        if not selected and len(sentence) > max_chars:
            shortened = sentence[:max_chars].rstrip("，,；;：:")
            return f"{shortened}……"
        selected.append(sentence)
        length += len(sentence)
    return "".join(selected) or f"{normalized[:max_chars]}……"


def _localized_research_institution(institution: str, url: str) -> str:
    normalized = institution.strip()
    if _has_chinese_text(normalized, minimum_chars=2):
        return normalized
    page_host = hostname(url)
    for domain, chinese_name in _RESEARCH_INSTITUTION_NAMES.items():
        if page_host == domain or page_host.endswith(f".{domain}"):
            return chinese_name
    raise ValueError("前沿观点来源机构缺少可核验的中文名称")


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
            "instruction": (
                "选择一份研究报告；selected_passages只返回页面中逐字存在的连续原文段落，"
                "同时依据这些原文生成中文标题和120至220字的中文压缩摘要，不能增加原文"
                "没有的事实或判断。"
            ),
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
    chinese_title = selection.chinese_title.strip()
    if not chinese_title and _has_chinese_text(selection.title):
        chinese_title = selection.title.strip()
    if not _has_chinese_text(chinese_title):
        raise ValueError("前沿观点缺少中文标题")
    if not _has_chinese_text(selection.chinese_summary, minimum_chars=20):
        raise ValueError("前沿观点缺少可核对的中文摘要")
    chinese_summary = _compact_chinese_summary(selection.chinese_summary)
    institution = _localized_research_institution(
        selection.institution,
        selection.source_url,
    )
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
            "publisher": institution,
            "publish_date": selection.publish_date,
        }
    )
    item = WeeklyItem(
        item_id="frontier-" + hashlib.sha256(selection.source_url.encode("utf-8")).hexdigest()[:12],
        section="前沿观点",
        title=chinese_title,
        body=f"{chinese_summary}\n\n（来源：{institution}《{selection.title}》）",
        content_mode="report_summary",
        source_ids=[record.source_id],
    )
    return item, [record], warnings


def _assess_ordinary_section(
    section_name: str,
    pages: list[WebCandidate],
    tools: ToolGateway,
    *,
    retrieved_at: str,
    warnings: list[str],
) -> _SectionTaskOutput:
    items: list[WeeklyItem] = []
    records: list[SourceRecord] = []
    try:
        items, records, item_warnings = _ordinary_items(
            pages,
            tools,
            expected_section=section_name,
            retrieved_at=retrieved_at,
        )
        warnings.extend(item_warnings)
    except Exception as exc:
        warnings.append(f"{section_name}内容评估失败：{exc}")
    return _SectionTaskOutput(
        items=tuple(items),
        source_records=tuple(records),
        warnings=tuple(warnings),
    )


def _run_party_section_task(
    tools: ToolGateway,
    *,
    period_start: date,
    period_end: date,
    retrieved_at: str,
) -> _SectionTaskOutput:
    section_name = "党政要闻"
    queries = dict(_ordinary_query_groups(period_start, period_end))[section_name]
    pages, warnings = _collect_pages(
        list(queries),
        tools,
        period_start=period_start,
        period_end=period_end,
        source_section=section_name,
        max_results_per_query=10,
    )
    feed_pages, feed_warnings = _collect_source_feed_pages(
        list(section_source_feed_urls(section_name)),
        tools,
        period_start=period_start,
        period_end=period_end,
        source_section=section_name,
    )
    warnings.extend(feed_warnings)
    return _assess_ordinary_section(
        section_name,
        _merge_candidate_pages(feed_pages, pages),
        tools,
        retrieved_at=retrieved_at,
        warnings=warnings,
    )


def _run_regulatory_section_task(
    tools: ToolGateway,
    *,
    period_start: date,
    period_end: date,
    retrieved_at: str,
) -> _SectionTaskOutput:
    section_name = "监管动态"
    pages, warnings = _collect_regulatory_pages(
        _regulatory_query_groups(period_start, period_end),
        list(section_source_feed_specs(section_name)),
        tools,
        period_start=period_start,
        period_end=period_end,
    )
    return _assess_ordinary_section(
        section_name,
        pages,
        tools,
        retrieved_at=retrieved_at,
        warnings=warnings,
    )


def _run_peer_section_task(
    tools: ToolGateway,
    *,
    period_start: date,
    period_end: date,
    retrieved_at: str,
) -> _SectionTaskOutput:
    section_name = "同业动向"
    page_groups: list[list[WebCandidate]] = []
    warnings: list[str] = []
    for _, queries in _peer_activity_query_groups(period_start, period_end):
        pages, group_warnings = _collect_pages(
            list(queries),
            tools,
            period_start=period_start,
            period_end=period_end,
            source_section=section_name,
            max_results_per_query=10,
        )
        page_groups.append(pages)
        warnings.extend(group_warnings)
    return _assess_ordinary_section(
        section_name,
        _merge_candidate_pages_balanced(*page_groups),
        tools,
        retrieved_at=retrieved_at,
        warnings=warnings,
    )


def _run_market_section_task(
    tools: ToolGateway,
    *,
    publication_date: date,
    period_start: date,
    period_end: date,
    retrieved_at: str,
    monday_pending: bool,
) -> _SectionTaskOutput:
    section_name = "市场观察"
    warnings: list[str] = []
    topic_page_groups: list[list[WebCandidate]] = []
    for _, queries in _market_observation_query_groups(period_start, period_end):
        pages, group_warnings = _collect_pages(
            list(queries),
            tools,
            period_start=period_start,
            period_end=period_end,
            source_section=section_name,
            max_results_per_query=10,
        )
        topic_page_groups.append(pages)
        warnings.extend(group_warnings)
    ordinary_output = _assess_ordinary_section(
        section_name,
        _merge_candidate_pages_balanced(*topic_page_groups),
        tools,
        retrieved_at=retrieved_at,
        warnings=warnings,
    )

    items = list(ordinary_output.items)
    records = list(ordinary_output.source_records)
    warnings = list(ordinary_output.warnings)
    market_page_groups: list[tuple[tuple[str, ...], list[WebCandidate]]] = []
    for required_scopes, queries in _market_query_groups(
        publication_date,
        period_start,
        period_end,
    ):
        if monday_pending and required_scopes == ("monday_a",):
            continue
        pages, group_warnings = _collect_pages(
            list(queries),
            tools,
            source_section=section_name,
            max_results_per_query=10,
        )
        market_page_groups.append((required_scopes, pages))
        warnings.extend(group_warnings)
    try:
        market_item, market_records, item_warnings = _market_item(
            market_page_groups,
            tools,
            publication_date=publication_date,
            period_start=period_start,
            period_end=period_end,
            retrieved_at=retrieved_at,
            monday_pending=monday_pending,
        )
        if market_item is not None:
            items.insert(0, market_item)
        records.extend(market_records)
        warnings.extend(item_warnings)
    except Exception as exc:
        warnings.append(f"资本市场综述未通过完整性校验：{exc}")
    if monday_pending:
        warnings.append("今日A股收盘数据待15:00收盘后更新")
    return _SectionTaskOutput(
        items=tuple(items),
        source_records=tuple(records),
        warnings=tuple(warnings),
    )


def _run_frontier_section_task(
    tools: ToolGateway,
    *,
    period_start: date,
    period_end: date,
    retrieved_at: str,
) -> _SectionTaskOutput:
    frontier_window_start = period_end - timedelta(days=29)
    pages, warnings = _collect_pages(
        _frontier_queries(frontier_window_start, period_end, fallback=True),
        tools,
        period_start=frontier_window_start,
        period_end=period_end,
        require_research=True,
        source_section="前沿观点",
        max_results_per_query=10,
    )
    weekly_pages = [
        page
        for page in pages
        if date_in_period(page.publish_date, period_start, period_end)
    ]
    if weekly_pages:
        pages = weekly_pages
    elif pages:
        warnings.append("前沿观点使用近30日兜底报告，发布日期不在本期统计周")

    item: WeeklyItem | None = None
    records: list[SourceRecord] = []
    try:
        item, records, item_warnings = _frontier_item(
            pages,
            tools,
            retrieved_at=retrieved_at,
            period_start=frontier_window_start,
            period_end=period_end,
        )
        warnings.extend(item_warnings)
    except Exception as exc:
        warnings.append(f"前沿观点未通过原文校验：{exc}")
    return _SectionTaskOutput(
        items=(item,) if item is not None else (),
        source_records=tuple(records),
        warnings=tuple(warnings),
    )


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


def _document_metadata(result: InternalWeeklyResult) -> dict[str, str]:
    return {
        "generation_mode": result.generation_mode,
        "publication_date": result.publication_date,
        "period_start": result.period_start,
        "period_end": result.period_end,
        "draft_version": result.draft_version,
        "ready_for_approval": "true" if result.ready_for_approval else "false",
    }


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
    result.document_metadata = _document_metadata(result)
    result.body = render_review_markdown(result)
    output_dir = str(inputs.get("output_dir") or "").strip()
    if output_dir:
        result.output_file, result.manifest_file = write_review_bundle(result, output_dir)
    return result


def _run_approved_word_export(
    inputs: dict[str, object],
) -> InternalWeeklyResult:
    previous_title = str(inputs.get("previous_title", "") or "").strip()
    previous_body = str(inputs.get("previous_body", "") or "").strip()
    previous_sources = [
        str(item).strip()
        for item in list(inputs.get("previous_sources") or [])
        if str(item).strip()
    ]
    metadata = inputs.get("previous_document_metadata")
    request_text = str(inputs.get("text") or inputs.get("revision_request") or "").strip()
    common = {
        "title": previous_title,
        "body": previous_body,
        "sources": previous_sources,
        "document_metadata": (
            {
                str(key): str(value or "").strip()
                for key, value in metadata.items()
            }
            if isinstance(metadata, dict)
            else {}
        ),
    }
    if not requests_clean_word(request_text):
        return InternalWeeklyResult(
            **common,
            needs_clarification=True,
            message="当前续接的是内参周报核对稿。如需定稿，请明确回复“核对无误，生成 Word 洁净版”。",
        )
    if not is_explicit_word_approval(request_text):
        return InternalWeeklyResult(
            **common,
            needs_clarification=True,
            message=(
                "生成洁净版会锁定当前核对稿。请确认来源和内容均已人工核对后，"
                "回复“核对无误，生成 Word 洁净版”。"
            ),
        )
    try:
        draft = parse_approved_review(
            previous_body,
            metadata if isinstance(metadata, dict) else {},
        )
    except ValueError as exc:
        return InternalWeeklyResult(
            **common,
            needs_clarification=True,
            message=f"当前版本不能生成洁净版 Word：{exc}",
        )

    output_dir = str(inputs.get("output_dir", "") or "").strip()
    output_path = generate_internal_weekly_docx(
        draft=draft,
        request_text=request_text,
        output_dir=output_dir,
    )
    return InternalWeeklyResult(
        **common,
        publication_date=draft.publication_date.isoformat(),
        period_start=draft.period_start.isoformat(),
        period_end=draft.period_end.isoformat(),
        ready_for_approval=True,
        draft_version=draft.draft_version,
        output_file=str(output_path),
        message=(
            f"已按人工确认的草稿版本 {draft.draft_version} 生成洁净版 Word。"
            "目录项和页码已生成，打开文件即可查看，无需再手工更新目录。"
        ),
    )


def run(inputs: dict[str, object], tools: ToolGateway) -> InternalWeeklyResult:
    """先生成可追溯核对稿；人工明确批准后再从同一版本生成 Word。"""
    if inputs.get("revision"):
        return _run_approved_word_export(inputs)

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
    section_tasks: dict[str, Callable[[], _SectionTaskOutput]] = {
        "党政要闻": lambda: _run_party_section_task(
            tools,
            period_start=period_start,
            period_end=period_end,
            retrieved_at=retrieved_at,
        ),
        "监管动态": lambda: _run_regulatory_section_task(
            tools,
            period_start=period_start,
            period_end=period_end,
            retrieved_at=retrieved_at,
        ),
        "同业动向": lambda: _run_peer_section_task(
            tools,
            period_start=period_start,
            period_end=period_end,
            retrieved_at=retrieved_at,
        ),
        "市场观察": lambda: _run_market_section_task(
            tools,
            publication_date=publication_date,
            period_start=period_start,
            period_end=period_end,
            retrieved_at=retrieved_at,
            monday_pending=monday_pending,
        ),
        "前沿观点": lambda: _run_frontier_section_task(
            tools,
            period_start=period_start,
            period_end=period_end,
            retrieved_at=retrieved_at,
        ),
    }
    section_outputs, section_failures = _run_parallel_section_tasks(section_tasks)
    warnings: list[str] = []
    source_records: list[SourceRecord] = []
    items_by_section: dict[str, list[WeeklyItem]] = {name: [] for name in SECTION_ORDER}
    for section_name in SECTION_ORDER:
        output = section_outputs.get(section_name)
        if output is not None:
            items_by_section[section_name].extend(output.items)
            source_records.extend(output.source_records)
            warnings.extend(output.warnings)
        if section_name in section_failures:
            warnings.append(
                f"{section_name}模块执行失败"
                f"（{type(section_failures[section_name]).__name__}），已保留待核事项"
            )
    sections = [
        WeeklySection(name=name, items=items_by_section[name]) for name in SECTION_ORDER
    ]

    for section_name in ("党政要闻", "监管动态", "同业动向"):
        if not items_by_section[section_name]:
            warnings.append(f"{section_name}暂无通过筛选和溯源校验的条目")

    deduped_records: dict[str, SourceRecord] = {}
    for record in source_records:
        deduped_records[record.source_id] = record
    market_item_present = any(
        item.content_mode == "market_fixed"
        for item in items_by_section["市场观察"]
    )
    frontier_item_present = bool(items_by_section["前沿观点"])
    ready = (
        all(items_by_section[name] for name in ("党政要闻", "监管动态", "同业动向"))
        and market_item_present
        and frontier_item_present
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
    result.document_metadata = _document_metadata(result)
    result.body = render_review_markdown(result)
    output_dir = str(inputs.get("output_dir") or "").strip()
    if output_dir:
        result.output_file, result.manifest_file = write_review_bundle(result, output_dir)
    return result
