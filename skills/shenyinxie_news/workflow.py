from __future__ import annotations

from datetime import date
from pathlib import Path

from app.platform.tools import ToolGateway
from skills.shenyinxie_news.docx_output import write_shenyinxie_docx
from skills.shenyinxie_news.schema import NewsCandidate, SelectedArticle, ShenyinxieNewsResult
from skills.shenyinxie_news.selection import (
    MediaWhitelist,
    apply_rule_relevance,
    calculate_issue_number,
    calculate_news_period,
    dedupe_same_article,
    filter_core_subject,
    generate_search_queries,
    hard_gate,
    normalize_url,
    score_candidates_rule_based,
    select_top_candidates,
)


MAX_CANDIDATES = 30


def _build_candidate(search_item: dict[str, str], page: dict[str, str]) -> NewsCandidate:
    """把搜索结果和网页读取结果合并为 NewsCandidate。"""
    url = str(page.get("url") or search_item.get("url", ""))
    canonical = str(page.get("canonical_url") or url)
    title = str(page.get("title") or search_item.get("title", ""))
    site = str(page.get("site", ""))
    return NewsCandidate(
        url=url,
        canonical_url=canonical,
        title=title,
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


def run(inputs: dict[str, object], tools: ToolGateway) -> ShenyinxieNewsResult:
    """深银协动态 Skill 入口。"""
    # 允许测试通过 inputs 注入固定日期
    today_override = inputs.get("today")
    if isinstance(today_override, date):
        today = today_override
    else:
        today = None

    period_start, period_end = calculate_news_period(today)
    issue_number = calculate_issue_number(today)

    whitelist = MediaWhitelist.from_yaml()

    # 1. 搜索
    queries = generate_search_queries(period_start, period_end)
    search_results: list[dict[str, str]] = []
    try:
        for query in queries:
            results = tools.call("search", query, max_results=5)
            if isinstance(results, list):
                search_results.extend(results)
    except Exception as exc:
        return ShenyinxieNewsResult(
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            issue_number=issue_number,
            needs_clarification=False,
            message=f"当前无法完成联网检索：{exc}",
        )

    if not search_results:
        return ShenyinxieNewsResult(
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            issue_number=issue_number,
            needs_clarification=False,
            message="本期未检索到符合当前权威媒体和日期条件的微众银行报道。",
        )

    # 2. URL 规范化去重，限制候选池大小
    unique_results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in search_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        key = normalize_url(url)
        if key in seen_urls:
            continue
        seen_urls.add(key)
        unique_results.append(item)
        if len(unique_results) >= MAX_CANDIDATES:
            break

    # 3. 读取网页
    candidates: list[NewsCandidate] = []
    read_errors: list[str] = []
    for item in unique_results:
        url = str(item.get("url", ""))
        try:
            page = tools.call("web_reader", url)
        except Exception as exc:
            read_errors.append(f"{url}: {exc}")
            continue
        if not isinstance(page, dict):
            continue
        candidate = _build_candidate(item, page)
        candidate = _attach_media_info(candidate, whitelist)
        candidates.append(candidate)

    # 4. 硬性准入
    gated: list[NewsCandidate] = []
    for candidate in candidates:
        ok, reason = hard_gate(candidate, period_start, period_end, whitelist)
        if ok:
            gated.append(candidate)
        else:
            candidate.select_reason = f"硬性准入失败：{reason}"

    # 5. 相关性判断（规则版）
    gated = apply_rule_relevance(gated)
    gated = filter_core_subject(gated)

    # 6. 去重
    gated = dedupe_same_article(gated)

    # 7. 评分与选稿
    gated = score_candidates_rule_based(gated)
    selected = select_top_candidates(gated, target=3)

    # 8. 构造输出
    if not selected:
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
        )
        articles.append(article)
        sources.append(article.original_url)
        body_lines.append(f"动态{['一', '二', '三'][idx - 1]}")
        body_lines.append(f"【标题】{article.title}")
        body_lines.append(f"【媒体】{article.media_name}")
        body_lines.append(f"【发布日期】{article.publish_date}")
        body_lines.append(f"【正文】{article.body}")
        body_lines.append(f"【原文链接】{article.original_url}")
        body_lines.append("")

    title = f"深圳银行业协会工作动态（{period_start.year}年{period_start.month}月第{int(issue_number.split('-')[1])}期）"
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
