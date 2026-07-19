from __future__ import annotations

from datetime import date
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
    extract_explicit_half_month,
    generate_expanded_search_queries,
    generate_primary_search_queries,
    hard_gate,
    normalize_url,
    score_candidates_rule_based,
    select_submission_candidates,
    strip_trailing_media_title_suffix,
)


MAX_CANDIDATES = 30


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
                "专题稿可返回 full_text；综合稿只有在微众银行内容完整、正面且可独立成立时才返回 extract；"
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


def _build_candidate(search_item: dict[str, str], page: dict[str, str]) -> NewsCandidate:
    """把搜索结果和网页读取结果合并为 NewsCandidate。"""
    url = str(page.get("url") or search_item.get("url", ""))
    canonical = str(page.get("canonical_url") or url)
    source_title = str(page.get("title") or search_item.get("title", ""))
    title = strip_trailing_media_title_suffix(source_title)
    site = str(page.get("site", ""))
    return NewsCandidate(
        url=url,
        canonical_url=canonical,
        title=title,
        source_title=source_title,
        site=site,
        publish_date=str(page.get("publish_date", "")),
        date_extracted_from=str(page.get("date_extracted_from", "")),
        body=str(page.get("text", "")),
    )


def _attach_media_info(candidate: NewsCandidate, whitelist: MediaWhitelist) -> NewsCandidate:
    info = whitelist.media_info(candidate.url)
    if info:
        candidate.media_name = str(info.get("name", ""))
        candidate.media_tier = int(info.get("tier", 99))
    return candidate


def _collect_stage_candidates(
    *,
    queries: list[str],
    tools: ToolGateway,
    whitelist: MediaWhitelist,
    period_start: date,
    period_end: date,
    seen_urls: set[str],
) -> tuple[list[NewsCandidate], int]:
    """完成一轮信源检索、去重、正文读取和硬性准入。"""
    search_results: list[dict[str, str]] = []
    for query in queries:
        results = tools.call("search", query, max_results=5)
        if isinstance(results, list):
            search_results.extend(item for item in results if isinstance(item, dict))

    unique_results: list[dict[str, str]] = []
    for item in search_results:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        key = normalize_url(url)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        unique_results.append(item)
        if len(seen_urls) >= MAX_CANDIDATES:
            break

    candidates: list[NewsCandidate] = []
    for item in unique_results:
        url = str(item.get("url", ""))
        try:
            page = tools.call("web_reader", url)
        except Exception:
            continue
        if not isinstance(page, dict):
            continue
        candidate = _attach_media_info(_build_candidate(item, page), whitelist)
        ok, reason = hard_gate(candidate, period_start, period_end, whitelist)
        if ok:
            candidates.append(candidate)
        else:
            candidate.select_reason = f"硬性准入失败：{reason}"

    return apply_rule_relevance(candidates), len(search_results)


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
            assessment = _assess_candidate(candidate, tools)
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

    # 1. 首轮检索并完成内容判断；完整专题稿不足 3 篇时才扩展信源。
    seen_urls: set[str] = set()
    total_search_results = 0
    full_text_candidates: list[NewsCandidate] = []
    excerpt_candidates: list[NewsCandidate] = []
    assessment_failures = 0
    assessment_successes = 0
    try:
        primary_candidates, primary_result_count = _collect_stage_candidates(
            queries=generate_primary_search_queries(period_start, period_end),
            tools=tools,
            whitelist=whitelist,
            period_start=period_start,
            period_end=period_end,
            seen_urls=seen_urls,
        )
        total_search_results += primary_result_count
        primary_full, primary_extract, failures, successes = _assess_stage_candidates(
            primary_candidates, tools
        )
        full_text_candidates.extend(primary_full)
        excerpt_candidates.extend(primary_extract)
        assessment_failures += failures
        assessment_successes += successes

        distinct_primary_full = dedupe_same_article(full_text_candidates)
        if len(distinct_primary_full) < 3 and len(seen_urls) < MAX_CANDIDATES:
            expanded_candidates, expanded_result_count = _collect_stage_candidates(
                queries=generate_expanded_search_queries(period_start, period_end),
                tools=tools,
                whitelist=whitelist,
                period_start=period_start,
                period_end=period_end,
                seen_urls=seen_urls,
            )
            total_search_results += expanded_result_count
            expanded_full, expanded_extract, failures, successes = _assess_stage_candidates(
                expanded_candidates, tools
            )
            full_text_candidates.extend(expanded_full)
            excerpt_candidates.extend(expanded_extract)
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
            message="本期未检索到符合当前权威媒体和日期条件的微众银行报道。",
        )

    # 2. 专题全文最多选 3 篇；只有没有专题全文时才考虑摘编稿（最多 2 篇）。
    full_text_candidates = score_candidates_rule_based(dedupe_same_article(full_text_candidates))
    excerpt_candidates = score_candidates_rule_based(dedupe_same_article(excerpt_candidates))
    selected = select_submission_candidates(full_text_candidates, excerpt_candidates)

    # 8. 构造输出
    if not selected:
        if seen_urls and assessment_successes == 0 and assessment_failures > 0:
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
            message="本期未检索到符合当前权威媒体和日期条件的微众银行报道。",
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
