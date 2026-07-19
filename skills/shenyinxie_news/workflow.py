from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import unescape
import re
from urllib.parse import urlsplit, urlunsplit

from app.platform.tools import ToolGateway
from skills.shenyinxie_news.docx_output import write_shenyinxie_docx
from skills.shenyinxie_news.schema import (
    ArticleAssessment,
    NewsCandidate,
    SelectedArticle,
    ShenyinxieNewsResult,
)
from skills.shenyinxie_news.selection import (
    MediaWhitelist,
    apply_editorial_assessment,
    apply_rule_relevance,
    calculate_issue_number,
    calculate_news_period,
    dedupe_same_article,
    extract_markdown_front_matter,
    extract_explicit_half_month,
    generate_expanded_search_queries,
    generate_fallback_search_queries,
    generate_primary_search_queries,
    hard_gate,
    normalize_url,
    score_candidates_rule_based,
    select_submission_candidates,
    strip_trailing_media_title_suffix,
)


MAX_CANDIDATES = 30
SEARCH_RESULTS_PER_QUERY = 10
SEARCH_PASSES = 2


@dataclass(frozen=True)
class StageCollectionStats:
    search_results: int = 0
    source_eligible_results: int = 0
    readable_pages: int = 0
    hard_gate_passes: int = 0
    web_read_failures: int = 0


def _assess_candidate(candidate: NewsCandidate, tools: ToolGateway) -> ArticleAssessment:
    result = tools.call(
        "llm_writer",
        {
            "task": "shenyinxie_news_selection",
            "skill_id": "shenyinxie_news",
            "output_type": ArticleAssessment,
            "prompt_path": "prompts/select.md",
            "candidate_url": candidate.canonical_url or candidate.url,
            "instruction": (
                "判断这篇报道能否作为向深圳市银行业协会报送的微众银行正面新闻或成果。"
                "只有标题和全文都适合正面报送的专题稿才可返回 full_text；"
                "综合稿，以及标题带有被骗、投诉、风险、疑问等负面叙事但其中含有可独立报送成果的专题稿，"
                "必须返回 extract，并逐字段落摘取微众银行相关正面内容、给出准确的新标题；"
                "其他银行为主、中性行业盘点、负面风险事件或名单式提及必须 reject。"
            ),
            "materials": [
                {
                    "type": "web_page",
                    "source": "uploaded_file",
                    "source_label": candidate.media_name or candidate.site,
                    "text": (
                        f"原报道标题：{candidate.title}\n"
                        f"媒体：{candidate.media_name or candidate.site}\n"
                        f"发布日期：{candidate.publish_date}\n"
                        f"原文链接：{candidate.canonical_url or candidate.url}\n\n"
                        f"原文正文：\n{candidate.body}"
                    ),
                }
            ],
        },
    )
    if isinstance(result, ArticleAssessment):
        return result
    return ArticleAssessment.model_validate(result)


def _assess_candidate_with_recheck(
    candidate: NewsCandidate,
    tools: ToolGateway,
) -> ArticleAssessment:
    """对规则识别出的强相关稿复核一次偶发拒绝或调用失败。"""
    attempts = 2 if candidate.is_core_subject is True else 1
    last_assessment: ArticleAssessment | None = None
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            assessment = _assess_candidate(candidate, tools)
        except Exception as exc:
            last_error = exc
            continue
        last_assessment = assessment
        if assessment.decision != "reject" and assessment.is_positive_achievement:
            return assessment

    if last_assessment is not None:
        return last_assessment
    if last_error is not None:
        raise last_error
    raise RuntimeError("候选内容判断未返回结果")


def _strip_markdown_proxy_suffix(url: str) -> str:
    """移除网页读取兜底链接末尾的 .md，不改动真实网页路径。"""
    parsed = urlsplit(url.strip())
    path = parsed.path
    if path.lower().endswith((".html.md", ".htm.md", ".shtml.md")):
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _metadata_publish_date(metadata: dict[str, str]) -> str:
    value = metadata.get("datetime") or metadata.get("date") or ""
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else ""


def _build_candidate(search_item: dict[str, object], page: dict[str, str]) -> NewsCandidate:
    """把搜索结果和网页读取结果合并为 NewsCandidate。"""
    raw_body = str(page.get("text", ""))
    metadata, clean_body = extract_markdown_front_matter(raw_body)
    url = str(page.get("url") or search_item.get("url", ""))
    canonical = str(
        page.get("canonical_url")
        or metadata.get("canonical_url")
        or _strip_markdown_proxy_suffix(url)
    )
    source_title = unescape(
        str(page.get("title") or metadata.get("title") or search_item.get("title", ""))
    )
    title = strip_trailing_media_title_suffix(source_title)
    site = str(page.get("site", "") or (urlsplit(url).hostname or ""))
    metadata_publish_date = _metadata_publish_date(metadata)
    return NewsCandidate(
        url=url,
        canonical_url=canonical,
        title=title,
        source_title=source_title,
        site=site,
        media_name=metadata.get("source", ""),
        publish_date=str(page.get("publish_date", "") or metadata_publish_date),
        date_extracted_from=str(
            page.get("date_extracted_from", "")
            or ("markdown_front_matter" if metadata_publish_date else "")
        ),
        body=clean_body,
    )


def _attach_media_info(candidate: NewsCandidate, whitelist: MediaWhitelist) -> NewsCandidate:
    domain_info = whitelist.media_info(candidate.url)
    if domain_info:
        reported_info = whitelist.media_info_by_name(candidate.media_name)
        if reported_info:
            candidate.media_name = str(reported_info.get("name", ""))
            candidate.media_tier = max(
                int(domain_info.get("tier", 99)),
                int(reported_info.get("tier", 99)),
            )
        else:
            candidate.media_name = str(domain_info.get("name", ""))
            candidate.media_tier = int(domain_info.get("tier", 99))
    return candidate


def _merge_search_item(
    merged: dict[str, dict[str, object]],
    item: dict[str, object],
) -> None:
    url = str(item.get("url", "")).strip()
    if not url:
        return
    key = normalize_url(url)
    existing = merged.get(key)
    hit_count = int(item.get("_hit_count", 1) or 1)
    if existing is None:
        merged[key] = {
            **item,
            "url": url,
            "_hit_count": hit_count,
        }
        return

    existing["_hit_count"] = int(existing.get("_hit_count", 1) or 1) + hit_count
    for field in ("title", "snippet", "source"):
        if not str(existing.get(field, "")).strip() and str(item.get(field, "")).strip():
            existing[field] = item[field]


def _rank_search_item(item: dict[str, object], whitelist: MediaWhitelist) -> tuple[object, ...]:
    title = str(item.get("title", ""))
    snippet = str(item.get("snippet", ""))
    info = whitelist.media_info(str(item.get("url", ""))) or {}
    direct_subject = "微众" in title or "webank" in title.lower()
    contextual_subject = "微众" in snippet or "webank" in snippet.lower()
    return (
        -int(item.get("_hit_count", 1) or 1),
        -int(direct_subject),
        -int(contextual_subject),
        int(info.get("tier", 99)),
        normalize_url(str(item.get("url", ""))),
    )


def _collect_stage_candidates(
    *,
    queries: list[str],
    tools: ToolGateway,
    whitelist: MediaWhitelist,
    period_start: date,
    period_end: date,
    seen_urls: set[str],
    max_media_tier: int,
    initial_results: list[dict[str, object]] | None = None,
    deferred_results: list[dict[str, object]] | None = None,
) -> tuple[list[NewsCandidate], StageCollectionStats]:
    """完成一轮信源检索、去重、正文读取和硬性准入。"""
    search_results: list[dict[str, object]] = []
    for query in queries:
        for _ in range(SEARCH_PASSES):
            results = tools.call("search", query, max_results=SEARCH_RESULTS_PER_QUERY)
            if isinstance(results, list):
                search_results.extend(item for item in results if isinstance(item, dict))

    merged_results: dict[str, dict[str, object]] = {}
    for item in list(initial_results or []) + search_results:
        _merge_search_item(merged_results, item)

    deferred_merged: dict[str, dict[str, object]] = {}
    for item in deferred_results or []:
        _merge_search_item(deferred_merged, item)

    eligible_results: list[dict[str, object]] = []
    for item in merged_results.values():
        url = str(item.get("url", "")).strip()
        media_info = whitelist.media_info(url)
        if media_info is None:
            continue
        key = normalize_url(url)
        media_tier = int(media_info.get("tier", 99))
        if media_tier > max_media_tier:
            if deferred_results is not None and media_tier <= 3 and key not in seen_urls:
                _merge_search_item(deferred_merged, item)
            continue
        if key in seen_urls:
            continue
        eligible_results.append(item)

    if deferred_results is not None:
        deferred_results[:] = list(deferred_merged.values())

    eligible_results.sort(key=lambda item: _rank_search_item(item, whitelist))
    unique_results = eligible_results[:MAX_CANDIDATES]
    seen_urls.update(normalize_url(str(item.get("url", ""))) for item in unique_results)

    candidates: list[NewsCandidate] = []
    readable_pages = 0
    web_read_failures = 0
    for item in unique_results:
        url = str(item.get("url", ""))
        try:
            page = tools.call("web_reader", url)
        except Exception:
            web_read_failures += 1
            continue
        if not isinstance(page, dict):
            web_read_failures += 1
            continue
        readable_pages += 1
        candidate = _attach_media_info(_build_candidate(item, page), whitelist)
        ok, reason = hard_gate(candidate, period_start, period_end, whitelist)
        if ok:
            candidates.append(candidate)
        else:
            candidate.select_reason = f"硬性准入失败：{reason}"

    return apply_rule_relevance(candidates), StageCollectionStats(
        search_results=len(search_results),
        source_eligible_results=len(eligible_results),
        readable_pages=readable_pages,
        hard_gate_passes=len(candidates),
        web_read_failures=web_read_failures,
    )


def _assess_stage_candidates(
    candidates: list[NewsCandidate],
    tools: ToolGateway,
) -> tuple[list[NewsCandidate], list[NewsCandidate], int, int]:
    """用结构化模型判断一批候选，并由代码复核全文/摘编边界。"""
    full_text_candidates: list[NewsCandidate] = []
    excerpt_candidates: list[NewsCandidate] = []
    failures = 0
    successes = 0
    for candidate in candidates:
        try:
            assessment = _assess_candidate_with_recheck(candidate, tools)
        except Exception:
            failures += 1
            continue
        successes += 1
        assessed = apply_editorial_assessment(candidate, assessment)
        if assessed is None:
            continue
        if assessed.content_mode == "full_text":
            full_text_candidates.append(assessed)
        elif assessed.content_mode == "extract":
            excerpt_candidates.append(assessed)
    return full_text_candidates, excerpt_candidates, failures, successes


def _no_selection_message(
    *,
    total_search_results: int,
    source_eligible_results: int,
    readable_pages: int,
    hard_gate_passes: int,
) -> str:
    if total_search_results == 0:
        return "本期未检索到与微众银行相关的候选报道。"
    if source_eligible_results == 0:
        return "本期检索到相关页面，但没有来自当前已核验媒体范围的候选报道。"
    if readable_pages == 0:
        return "本期检索到候选链接，但原文均无法完整读取或核验。"
    if hard_gate_passes == 0:
        return "本期检索到候选报道，但均未通过发布日期或正文完整性核验。"
    return "本期检索并核验了候选报道，但均未达到微众银行正面新闻和成果的报送标准。"


def run(inputs: dict[str, object], tools: ToolGateway) -> ShenyinxieNewsResult:
    """深银协动态 Skill 入口。"""
    # 允许测试通过 inputs 注入固定日期
    today_override = inputs.get("today")
    if isinstance(today_override, date):
        today = today_override
    else:
        today = None

    issue_number = calculate_issue_number(today)
    instruction = str(inputs.get("text", "") or "").strip()
    if instruction:
        requested_period = extract_explicit_half_month(instruction, today)
        if requested_period is None:
            return ShenyinxieNewsResult(
                period_start="",
                period_end="",
                issue_number=issue_number,
                needs_clarification=True,
                message=(
                    "请明确要生成哪个月的上半月还是下半月，"
                    "例如“生成7月上半月的深银协动态”或“生成7月下半月的深银协动态”。"
                ),
            )
        period_start, period_end = requested_period
    else:
        # 仅供内部测试和兼容调用；真实入口始终传入用户指令。
        period_start, period_end = calculate_news_period(today)

    whitelist = MediaWhitelist.from_yaml()

    # 1. 先检索主流媒体；不足 3 篇时扩展行业/广东媒体；零专题稿时再启用补充媒体。
    seen_urls: set[str] = set()
    total_search_results = 0
    source_eligible_results = 0
    readable_pages = 0
    hard_gate_passes = 0
    full_text_candidates: list[NewsCandidate] = []
    excerpt_candidates: list[NewsCandidate] = []
    deferred_fallback_results: list[dict[str, object]] = []
    assessment_failures = 0
    assessment_successes = 0
    try:
        primary_candidates, primary_stats = _collect_stage_candidates(
            queries=generate_primary_search_queries(period_start, period_end),
            tools=tools,
            whitelist=whitelist,
            period_start=period_start,
            period_end=period_end,
            seen_urls=seen_urls,
            max_media_tier=2,
            deferred_results=deferred_fallback_results,
        )
        total_search_results += primary_stats.search_results
        source_eligible_results += primary_stats.source_eligible_results
        readable_pages += primary_stats.readable_pages
        hard_gate_passes += primary_stats.hard_gate_passes
        primary_full, primary_extract, failures, successes = _assess_stage_candidates(
            primary_candidates, tools
        )
        full_text_candidates.extend(primary_full)
        excerpt_candidates.extend(primary_extract)
        assessment_failures += failures
        assessment_successes += successes

        distinct_primary_full = dedupe_same_article(full_text_candidates)
        if len(distinct_primary_full) < 3:
            expanded_candidates, expanded_stats = _collect_stage_candidates(
                queries=generate_expanded_search_queries(period_start, period_end),
                tools=tools,
                whitelist=whitelist,
                period_start=period_start,
                period_end=period_end,
                seen_urls=seen_urls,
                max_media_tier=2,
                deferred_results=deferred_fallback_results,
            )
            total_search_results += expanded_stats.search_results
            source_eligible_results += expanded_stats.source_eligible_results
            readable_pages += expanded_stats.readable_pages
            hard_gate_passes += expanded_stats.hard_gate_passes
            expanded_full, expanded_extract, failures, successes = _assess_stage_candidates(
                expanded_candidates, tools
            )
            full_text_candidates.extend(expanded_full)
            excerpt_candidates.extend(expanded_extract)
            assessment_failures += failures
            assessment_successes += successes

        if not dedupe_same_article(full_text_candidates):
            fallback_candidates, fallback_stats = _collect_stage_candidates(
                queries=generate_fallback_search_queries(period_start, period_end),
                tools=tools,
                whitelist=whitelist,
                period_start=period_start,
                period_end=period_end,
                seen_urls=seen_urls,
                max_media_tier=3,
                initial_results=deferred_fallback_results,
            )
            total_search_results += fallback_stats.search_results
            source_eligible_results += fallback_stats.source_eligible_results
            readable_pages += fallback_stats.readable_pages
            hard_gate_passes += fallback_stats.hard_gate_passes
            fallback_full, fallback_extract, failures, successes = _assess_stage_candidates(
                fallback_candidates, tools
            )
            full_text_candidates.extend(fallback_full)
            excerpt_candidates.extend(fallback_extract)
            assessment_failures += failures
            assessment_successes += successes
    except Exception as exc:
        return ShenyinxieNewsResult(
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            issue_number=issue_number,
            needs_clarification=False,
            message=f"当前无法完成联网检索：{exc}",
        )

    if total_search_results == 0:
        return ShenyinxieNewsResult(
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            issue_number=issue_number,
            needs_clarification=False,
            message=_no_selection_message(
                total_search_results=total_search_results,
                source_eligible_results=source_eligible_results,
                readable_pages=readable_pages,
                hard_gate_passes=hard_gate_passes,
            ),
        )

    # 2. 专题全文最多选 3 篇；只有没有专题全文时才考虑摘编稿（最多 2 篇）。
    full_text_candidates = score_candidates_rule_based(dedupe_same_article(full_text_candidates))
    excerpt_candidates = score_candidates_rule_based(dedupe_same_article(excerpt_candidates))
    selected = select_submission_candidates(full_text_candidates, excerpt_candidates)

    # 8. 构造输出
    if not selected:
        if hard_gate_passes and assessment_successes == 0 and assessment_failures > 0:
            return ShenyinxieNewsResult(
                period_start=period_start.isoformat(),
                period_end=period_end.isoformat(),
                issue_number=issue_number,
                needs_clarification=False,
                message="当前无法完成候选内容判断，请稍后重试。",
            )
        return ShenyinxieNewsResult(
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            issue_number=issue_number,
            needs_clarification=False,
            message=_no_selection_message(
                total_search_results=total_search_results,
                source_eligible_results=source_eligible_results,
                readable_pages=readable_pages,
                hard_gate_passes=hard_gate_passes,
            ),
        )

    articles: list[SelectedArticle] = []
    sources: list[str] = []
    body_lines: list[str] = []
    for idx, candidate in enumerate(selected, start=1):
        article = SelectedArticle(
            title=candidate.title,
            media_name=candidate.media_name or candidate.site,
            publish_date=candidate.publish_date,
            body=candidate.body,
            original_url=candidate.canonical_url or candidate.url,
            content_mode=candidate.content_mode or "full_text",
            source_title=candidate.source_title or candidate.title,
            editor_note=candidate.editor_note,
        )
        articles.append(article)
        sources.append(article.original_url)
        body_lines.append(f"动态{['一', '二', '三'][idx - 1]}")
        body_lines.append(f"【标题】{article.title}")
        body_lines.append(f"【媒体】{article.media_name}")
        body_lines.append(f"【发布日期】{article.publish_date}")
        body_lines.append(f"【正文】{article.body}")
        if article.content_mode == "extract" and article.source_title:
            body_lines.append(f"【原报道标题】{article.source_title}")
        body_lines.append(f"【原文链接】{article.original_url}")
        if article.content_mode == "extract" and article.editor_note:
            body_lines.append(f"【摘编说明】{article.editor_note}")
        body_lines.append("")

    monthly_issue = 1 if period_start.day <= 15 else 2
    title = (
        f"微众银行{period_start.year}年{period_start.month}月"
        f"第{monthly_issue}期信息动态"
    )
    body = "\n".join(body_lines).strip()

    output_file = ""
    output_dir = inputs.get("output_dir")
    if output_dir:
        try:
            output_file = str(
                write_shenyinxie_docx(
                    title=title,
                    period_start=period_start,
                    period_end=period_end,
                    issue_number=issue_number,
                    articles=articles,
                    output_dir=str(output_dir),
                )
            )
        except Exception as exc:
            # v1 生成失败时仍返回文本结果，便于排查
            return ShenyinxieNewsResult(
                period_start=period_start.isoformat(),
                period_end=period_end.isoformat(),
                issue_number=issue_number,
                articles=articles,
                sources=sources,
                output_file="",
                needs_clarification=False,
                message=f"本期已整理 {len(articles)} 篇报道，但 Word 生成失败：{exc}",
                title=title,
                body=body,
            )

    return ShenyinxieNewsResult(
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        issue_number=issue_number,
        articles=articles,
        sources=sources,
        output_file=output_file,
        needs_clarification=False,
        message=f"本期已整理 {len(articles)} 篇报道。",
        title=title,
        body=body,
    )
