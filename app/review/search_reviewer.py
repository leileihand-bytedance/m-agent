"""搜索增强版审核模块.

整合时间校准、原文核对、主动搜索能力。
独立于现有 reviewer.py，不改动现有代码。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta

from app.review.time_calibration import get_beijing_time
from app.review.citation_verifier import extract_original_text, verify_citation
from app.review.search_tools import SEARCH_SOURCES, search_web, fetch_page, identify_content_source, SearchResult, get_client
from app.review.core.model_runtime import create_model_message


async def review_with_search(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
) -> "ReviewResult":
    """搜索增强版审核（独立运行）。

    完整流程：
    1. 时间校准
    2. Phase 1 格式检查（复用现有逻辑）
    3. Phase 2 搜索增强判断
       - 有原文引用 → 原文核对
       - 党政要闻/监管动态 → 主动搜索核实
       - 其他 → LLM 判断
    4. 合并结果

    Args:
        paragraphs: 文档段落列表
        rules_text: 规则文本
        filename: 文件名

    Returns:
        ReviewResult（含所有 findings）
    """
    # ① 时间校准
    try:
        time_baseline = get_beijing_time()
        print(f"  审核基准时间: {time_baseline.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）")
    except RuntimeError:
        time_baseline = datetime.now()
        print(f"  ⚠️ 时间校准失败，使用本地时间: {time_baseline}")

    # ② Phase 1（复用现有逻辑）
    from app.review.reviewer import review_phase1
    phase1_result = await review_phase1(paragraphs, rules_text, filename)
    print(f"  Phase 1 完成: {len(phase1_result.findings)} 条问题")

    # ③ Phase 2 搜索增强
    phase2_findings = await _review_phase2_with_search(
        paragraphs, rules_text, filename, time_baseline
    )
    print(f"  Phase 2 完成: {len(phase2_findings)} 条问题")

    # ④ 合并结果
    from app.review.core.models import ReviewResult
    all_findings = list(phase1_result.findings) + phase2_findings
    return ReviewResult(
        findings=all_findings,
        total_rules=phase1_result.total_rules + len(phase2_findings),
        passed_rules=phase1_result.passed_rules,
        filename=filename,
    )


async def _review_phase2_with_search(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
    time_baseline: datetime,
) -> list["Finding"]:
    """Phase 2 搜索增强判断。

    对正文段落逐段分析：
    1. 检测是否有原文引用 → 调用 verify_citation
    2. 识别内容主体（党政要闻/监管动态）→ 主动搜索
    3. 其他 → LLM 判断（复用现有）
    """
    from app.review.core.models import Finding

    findings: list[Finding] = []
    current_section = None

    SECTION_KEYWORDS = {"党政要闻", "监管动态", "同业动向", "同业动态", "市场观察", "前沿观点"}

    for idx, para in enumerate(paragraphs):
        stripped = para.strip()

        # 识别板块标题
        if stripped in SECTION_KEYWORDS:
            current_section = stripped
            continue

        if current_section is None or current_section == "前沿观点":
            continue

        # 跳过新闻标题（正文段落才需要搜索核实）
        if _is_news_title(para):
            continue

        # ===== 原文引用段落 =====
        original_text = extract_original_text(para)
        if original_text:
            try:
                result = verify_citation(para, original_text)
                if not result.accurate:
                    findings.append(Finding(
                        rule_id="content-citation-mismatch",
                        paragraph_index=idx,
                        line_number=idx + 1,
                        original_text=para,
                        description=f"摘要与原文有偏差: {'; '.join(result.deviations[:2])}",
                    ))
            except Exception as e:
                print(f"  ⚠️ 原文核对失败(段{idx}): {e}")
            continue

        # ===== 党政要闻/监管动态段落（无原文）=====
        if current_section in ("党政要闻", "监管动态"):
            source_key = identify_content_source(para[:180])
            if source_key:
                try:
                    # 提取更有区分度的关键词（不用前40字符，用实体+关键信息）
                    search_query = _build_search_query(para, source_key)

                    search_results = search_web(search_query, time_baseline, max_results=8)
                    if search_results:
                        # 按优先级排序：权威媒体 > 官方 > 其他
                        ranked = _rank_results(search_results)
                        # 取前2条最优结果
                        top2 = ranked[:2]

                        # 优先尝试抓取页面正文（snippet信息太少）
                        candidates = []
                        for i, r in enumerate(top2, 1):
                            # 先尝试抓取页面
                            page_content = fetch_page(r.url)
                            if page_content and len(page_content) > 200:
                                # 抓取成功，用页面内容
                                text = page_content[:1500]
                                fetch_label = "（页面内容）"
                            else:
                                # 抓取失败，用snippet
                                text = r.snippet[:500]
                                fetch_label = "（搜索摘要）"
                            rank_label = "官方" if r.source == "official" else "权威媒体"
                            candidates.append(f"【来源{i}({rank_label}{fetch_label})】{r.title}\n{text}")

                        candidates_text = "\n\n".join(candidates)

                        # LLM 对比分析（多候选）
                        comparison = _llm_compare_multiple(para, candidates_text)
                        if comparison:
                            findings.append(Finding(
                                rule_id="content-citation-mismatch",
                                paragraph_index=idx,
                                line_number=idx + 1,
                                original_text=para,
                                description=comparison,
                            ))
                except Exception as e:
                    print(f"  ⚠️ 搜索/核对失败(段{idx}): {e}")

    # ===== 调用 LLM 做最终综合判断（复用 review_phase2）=====
    try:
        from app.review.reviewer import review_phase2
        phase2_result = await review_phase2(paragraphs, rules_text, filename)
        findings.extend(phase2_result.findings)
    except Exception as e:
        print(f"  ⚠️ LLM 判断失败: {e}")

    # 去重
    seen = set()
    deduped = []
    for f in findings:
        key = (f.rule_id, f.paragraph_index)
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    return deduped


AUTHORITATIVE_MEDIA_DOMAINS = {
    "yicai.com",      # 第一财经
    "xinhuanet.com",  # 新华网
    "people.com.cn",  # 人民网
    "21jingji.com",   # 21世纪经济报道
    "cs.com.cn",      # 中国证券报
    "zqrb.cn",        # 证券日报
    "secutimes.com",  # 证券时报
    "shzq.com",       # 上海证券报
}


def _rank_results(results: list[SearchResult]) -> list[SearchResult]:
    """按优先级排序：权威媒体 > 官方域名 > 其他。"""
    def rank_key(r: SearchResult) -> tuple[int, int]:
        # 权威媒体优先（内容完整易抓取）
        for domain in AUTHORITATIVE_MEDIA_DOMAINS:
            if domain in r.url:
                return (0, 0)
        # 官方域名次之
        if r.source == "official":
            return (1, 0)
        # 其他
        return (2, 0)
    return sorted(results, key=rank_key)


def _build_search_query(para: str, source_key: str) -> str:
    """从段落中提取更有区分度的搜索关键词。

    策略：
    1. 跳过日期前缀（6月XX日、XX日等）
    2. 提取机构关键词
    3. 提取文件/会议标题（用《》或关键词）
    4. 组合后控制在25字符以内

    Args:
        para: 段落文本
        source_key: 内容主体类型（如 PBOC, STATE_COUNCIL）

    Returns:
        搜索关键词字符串
    """
    import re
    from app.review.search_tools import SEARCH_SOURCES

    source_config = SEARCH_SOURCES.get(source_key, {})
    source_keywords = source_config.get("keywords", [])

    # 去掉日期前缀（6月XX日、XX日、2026年等）
    text = re.sub(r'^[\d一二三四五六七八九十]+年?[一二三四五六七八九十]+月?[\d一二三四五六七八九十]+日?\s*', '', para)

    # 提取文件标题《》
    title_match = re.search(r'《([^》]{2,20})》', text)
    if title_match:
        title = title_match.group(1)
        # 找机构关键词
        for kw in source_keywords:
            if kw in text[:50]:
                return f"{kw} {title}"[:25]
        return title[:25]

    # 提取关键词 + 紧跟的内容
    for kw in source_keywords:
        pos = text.find(kw)
        if pos != -1:
            # 取关键词后面30字符
            end = min(pos + 30, len(text))
            context = text[pos:end]
            # 截取到标点
            for punct in '。，、；':
                p = context.find(punct)
                if p > 3:
                    context = context[:p]
                    break
            return context[:25] if len(context) > 25 else context

    return text[:25]


def _is_news_title(para: str) -> bool:
    """判断段落是否为新闻标题。"""
    stripped = para.strip()
    if not stripped or len(stripped) < 5 or len(stripped) > 60:
        return False
    return stripped[-1] not in "。！？；?!."


def _初步判断_需要_llm_复核(
    para: str,
    search_result: SearchResult,
    source_key: str,
) -> bool:
    """初步判断是否需要 LLM 复核。"""
    title = search_result.title
    source_keywords = SEARCH_SOURCES.get(source_key, {}).get("keywords", [])
    for keyword in source_keywords:
        if len(keyword) >= 4 and keyword in title and keyword in para:
            return False
    keywords = re.findall(r'[一-龥]{3,}', title)
    keywords = [k for k in keywords if len(k) >= 4][:3]
    if not keywords:
        return True
    matches = sum(1 for kw in keywords if kw in para)
    if matches < len(keywords) * 0.5:
        return True
    return False


def _llm_compare_multiple(summary: str, candidates_text: str) -> str | None:
    """让 LLM 在多个候选来源中判断哪个最相关，并核对偏差。

    Args:
        summary: 内参摘要
        candidates_text: 多个搜索候选的标题+内容拼接

    Returns:
        偏差描述，或 None（无明显偏差）
    """
    client, model = get_client()

    prompt = f"""你是政策文件审核专家。请核对【内参摘要】是否准确。

【内参摘要】
{summary}

【搜索候选来源】（共2条，请逐条阅读）
{candidates_text}

审核步骤：
1. 逐条阅读候选来源，判断哪一条与【内参摘要】主题最相关
2. 如果所有候选来源与内参摘要完全不相关（主题、机构、时间都不对），标记为"无法核实，相关来源未找到"
3. 如果找到相关来源，对比内参摘要与该来源的原文，逐句检查：
   - 关键动词是否被替换（研究→聚焦、强调→明确建立等）
   - 并列要点是否被删除
   - 是否有添加原文没有的信息
   - 时间、机构、数据是否准确

请输出JSON格式（只输出JSON）：
{{
  "matched": true或false，是否找到相关来源
  "source_used": "使用的来源序号，如'来源1'"，
  "accurate": true或false，
  "deviations": ["具体偏差描述1", ...]，
  "cannot_verify_reason": "如果无法核实，说明原因"
}}"""

    try:
        response = create_model_message(
            client,
            metrics=None,
            stage="search_source_verification",
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            timeout=60.0,
        )
        text = ""
        for block in response.content:
            if hasattr(block, 'text') and block.text:
                text = block.text
                break
        if not text:
            return None

        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if not json_match:
            return None
        data = json.loads(json_match.group())

        if not data.get("matched", False):
            return "无法核实：搜索结果中未找到与内参内容相关的官方来源"

        if data.get("cannot_verify_reason"):
            return f"无法核实：{data.get('cannot_verify_reason')}"

        if data.get("accurate", True):
            return None

        deviations = data.get("deviations", [])
        if deviations:
            return "与官方原文有偏差: " + "; ".join(deviations[:3])
        return "与官方原文内容不符"
    except Exception as e:
        print(f"  ⚠️ LLM 多候选对比失败: {e}")
        return None


def _llm_compare_with_source(summary: str, original: str, source_title: str) -> str | None:
    """LLM 对比内参摘要与官方原文，返回偏差描述或 None（无偏差）。

    Args:
        summary: 内参中的摘要文本
        original: 抓取到的官方原文
        source_title: 官方来源标题

    Returns:
        偏差描述字符串，或 None（内容一致）
    """
    from app.review.search_tools import get_client
    client, model = get_client()

    prompt = f"""你是政策文件审核专家。请仔细核对【内参摘要】与【官方原文】的对应关系。

【官方来源标题】
{source_title}

【内参摘要】
{summary}

【官方原文】
{original}

审核要求：必须逐句检查。每个句子都要能找到对应。

检查步骤：
1. 找出原文中的关键动作词（研究、通报、强调、推进、深化、建立等）
2. 找出原文中的所有并列要点
3. 对照摘要：
   - 关键动作词是否被替换？替换了就是偏差
   - 并列要点是否被删减？每少一个都是偏差
   - 是否有添加原文没有的内容？

请输出JSON格式（只输出JSON）：
{{
  "accurate": true或false，
  "deviations": ["具体偏差1", "具体偏差2", ...]，
  "missing_key_points": ["原文有但摘要遗漏的关键内容1", ...]
}}

以下情况任一出现即为 inaccurate：
- "研究"/"通报"/"强调" 等动词被替换为"聚焦"/"明确建立"/"推进"等
- 原文多个并列要点（如A、B、C）中任何一个被删除
- "持续加强"/"进一步深化"等修饰词被替换为"建立"等"""

    try:
        response = create_model_message(
            client,
            metrics=None,
            stage="search_summary_verification",
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            timeout=60.0,
        )
        # 提取文本
        text = ""
        for block in response.content:
            if hasattr(block, 'text') and block.text:
                text = block.text
                break
        if not text:
            return None

        # 解析 JSON
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if not json_match:
            return None
        data = json.loads(json_match.group())
        if data.get("accurate", True):
            return None
        deviations = data.get("deviations", [])
        missing = data.get("missing_key_points", [])
        parts = deviations + missing
        if parts:
            return "与官方原文有偏差: " + "; ".join(parts[:3])
        return "与官方原文内容不符"
    except Exception as e:
        print(f"  ⚠️ LLM 对比失败: {e}")
        return None
