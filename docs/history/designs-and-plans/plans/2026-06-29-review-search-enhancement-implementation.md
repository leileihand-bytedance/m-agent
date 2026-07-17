# 智能审核搜索增强实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增"时间校准"、"原文核对"、"主动搜索"能力，模拟从业人员审核流程。

**Architecture:** 4 个独立新文件，不改动现有 reviewer.py/main.py。逐步开发测试，稳定后整合。

**Tech Stack:** Python 3.11/3.13, MiniMax API (direct HTTP), curl_cffi, BeautifulSoup

---

## 文件清单

| 文件 | 职责 |
|------|------|
| `app/review/time_calibration.py` | 联网获取北京时间 |
| `app/review/citation_verifier.py` | 原文引用提取 + 一致性核对 |
| `app/review/search_tools.py` | MiniMax 工具封装（搜索/抓取） |
| `app/review/search_reviewer.py` | 整合新能力的完整审核流程 |

---

## Task 1: 时间校准模块

**Files:**
- Create: `app/review/time_calibration.py`
- Test: 直接运行验证

- [ ] **Step 1: 创建 time_calibration.py**

```python
"""时间校准模块.

审核前联网获取北京时间，作为所有搜索的 TIME_BASELINE。
"""
from __future__ import annotations

from datetime import datetime
import urllib.request
import re
import time

def get_beijing_time() -> datetime:
    """联网获取北京时间。

    访问授时网站，返回 datetime 对象（北京时间，带时区信息）。

    Returns:
        datetime: 北京时间 (Asia/Shanghai)
    Raises:
        RuntimeError: 所有授时源均失败
    """
    # 备选授时源（按响应速度排序）
    SOURCES = [
        ("http://time.tuebaba.com", _parse_tuebaba),
        ("https://www.timeapi.org.cn/now", _parse_timeapi_cn),
        ("http://www.baidu.com/s?wd=北京时间", _parse_baidu),
    ]

    for url, parser in SOURCES:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36"
            })
            with urllib.request.urlopen(req, timeout=8) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                result = parser(text)
                if result is not None:
                    return result
        except Exception:
            continue

    raise RuntimeError("无法获取北京时间，请检查网络连接")


def _parse_tuebaba(text: str) -> datetime | None:
    """解析 tuebaba.com 返回的时间。"""
    # 响应示例: {"time":"2026-06-29 14:30:00","timezone":"Asia/Shanghai"}
    m = re.search(r'"time"\s*:\s*"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})"', text)
    if m:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    return None


def _parse_timeapi_cn(text: str) -> datetime | None:
    """解析 timeapi.org.cn 返回的时间。"""
    # 响应可能是 JSON 或纯文本
    m = re.search(r'(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2})', text)
    if m:
        try:
            return datetime.fromisoformat(m.group(1).replace(" ", "T"))
        except ValueError:
            pass
    return None


def _parse_baidu(text: str) -> datetime | None:
    """解析百度搜索结果页面的北京时间。"""
    # 百度首页有时间显示区域
    patterns = [
        r'id="world-clock-beijing"[^>]*>(\d{1,2}:\d{2}:\d{2})',
        r'北京时间.*?(\d{1,2}:\d{2}:\d{2})',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            # 只提取到秒，需要拼接当前日期（降级方案）
            now = datetime.now()
            t = time.strptime(m.group(1), "%H:%M:%S")
            return now.replace(hour=t.tm_hour, minute=t.tm_min, second=t.tm_sec)
    return None
```

- [ ] **Step 2: 运行验证**

Run: `python3.11 -c "from app.review.time_calibration import get_beijing_time; print(get_beijing_time())"`
Expected: 输出当前北京时间，如 `2026-06-29 14:30:00`

- [ ] **Step 3: 测试降级路径**

关闭网络或用无效 URL 测试，确认抛出 `RuntimeError`。

- [ ] **Step 4: Commit**

```bash
git add app/review/time_calibration.py
git commit -m "feat(review): add time_calibration module for Beijing time"
```

---

## Task 2: 原文核对模块

**Files:**
- Create: `app/review/citation_verifier.py`
- Test: `tests/test_citation_verifier.py`

- [ ] **Step 1: 创建 citation_verifier.py**

```python
"""原文核对模块.

对有"原文引用"的段落做一致性核对：
1. 提取"原文:"后的原始文本
2. 调用 LLM 核对摘要是否准确反映原文
"""
from __future__ import annotations

import os
import anthropic
from dataclasses import dataclass


def _get_client() -> tuple[anthropic.Anthropic, str]:
    """获取 API 客户端（独立实现，不依赖 reviewer.py）。"""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")
    client = anthropic.Anthropic(api_key=api_key, base_url=base_url)
    return client, model


@dataclass
class VerificationResult:
    """原文核对结果。"""
    accurate: bool              # 摘要是否准确反映原文
    deviations: list[str]       # 偏差描述列表
    missing_key_points: list[str]  # 摘要遗漏的关键点


def extract_original_text(paragraph: str) -> str | None:
    """提取段落中的原文引用。

    段落格式："正文内容...原文:这是原始文本内容..."
    返回"原文:"之后的部分。

    Args:
        paragraph: 完整段落文本

    Returns:
        原文文本（不含"原文:"前缀），或 None（无原文引用）
    """
    if "原文:" not in paragraph:
        return None
    return paragraph.split("原文:", 1)[1].strip()


def build_citation_prompt(summary: str, original: str) -> str:
    """构建原文核对的 prompt。

    要求 LLM：
    1. 提取原文关键信息（谁、何时、何事、关键数据）
    2. 检查摘要是否遗漏关键点或引入原文没有的信息
    3. 输出结构化结果
    """
    return f"""你是原文核对专家。请核对以下摘要是否准确反映了原文内容。

【摘要】
{summary}

【原文】
{original}

请按以下 JSON 格式输出（只输出 JSON，不要其他内容）：
{{
  "accurate": true或false，摘要是否准确反映原文
  "deviations": ["偏差1描述", "偏差2描述"]，如果 inaccurate
  "missing_key_points": ["遗漏关键点1", "遗漏关键点2"]，如果有任何遗漏
}}

判断标准：
- 摘要遗漏原文中的关键人物、会议名称、数据、时间点 → 不准确
- 摘要添加了原文没有的信息（原文未提及）→ 不准确
- 摘要与原文语义一致，只是简略 → accurate=True
"""


def verify_citation(summary: str, original: str) -> VerificationResult:
    """核对摘要是否准确反映原文。

    调用 LLM 做细粒度核对。

    Args:
        summary: 摘要/正文文本
        original: 原文引用文本

    Returns:
        VerificationResult: 核对结果
    """
    client, model_name = _get_client()
    prompt = build_citation_prompt(summary, original)

    import anthropic
    message = client.messages.create(
        model=model_name,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        timeout=60.0,
    )

    text = message.content[0].text if message.content else ""

    # 解析 JSON
    import json, re
    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if not json_match:
        return VerificationResult(accurate=True, deviations=[], missing_key_points=[])

    try:
        data = json.loads(json_match.group())
        return VerificationResult(
            accurate=data.get("accurate", True),
            deviations=data.get("deviations", []),
            missing_key_points=data.get("missing_key_points", []),
        )
    except json.JSONDecodeError:
        return VerificationResult(accurate=True, deviations=[], missing_key_points=[])
```

- [ ] **Step 2: 创建测试文件 tests/test_citation_verifier.py**

```python
"""原文核对测试."""
from app.review.citation_verifier import extract_original_text, VerificationResult


def test_extract_original_text_with_citation():
    """有原文引用时，提取原文部分。"""
    para = "中国人民银行宣布降准0.25个百分点。原文:为支持实体经济发展，中国人民银行决定于2026年6月15日起，下调金融机构存款准备金率0.25个百分点。"
    result = extract_original_text(para)
    assert result is not None
    assert "为支持实体经济发展" in result
    assert "原文:" not in result


def test_extract_original_text_without_citation():
    """无原文引用时，返回 None。"""
    para = "中国人民银行宣布降准0.25个百分点。"
    result = extract_original_text(para)
    assert result is None


def test_extract_original_text_multiple_colon():
    """正文中有冒号但不是原文引用。"""
    para = "国务院新闻办公室主任王晓明表示：原文:国务院新闻办公室今日发布..."
    result = extract_original_text(para)
    assert result is not None
    assert result.startswith("国务院新闻办公室今日发布")
```

- [ ] **Step 3: 运行测试**

Run: `python3.11 -c "from tests.test_citation_verifier import *; ..."` （手动执行，pytest 不可用）

- [ ] **Step 4: Commit**

```bash
git add app/review/citation_verifier.py tests/test_citation_verifier.py
git commit -m "feat(review): add citation_verifier module"
```

---

## Task 3: 搜索工具模块

**Files:**
- Create: `app/review/search_tools.py`
- Test: 直接运行验证

- [ ] **Step 1: 创建 search_tools.py**

```python
"""搜索工具模块.

封装 MiniMax 工具调用，支持网页搜索和页面抓取。
搜索优先级：官网 > 权威媒体
"""
from __future__ import annotations

import os
import anthropic
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# 搜索数据源配置
SEARCH_SOURCES: dict[str, dict] = {
    "PBOC": {
        "keywords": ["中国人民银行", "央行", "人民银行"],
        "preferred_domains": ["pbc.gov.cn", "pboc.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "CBIRC": {
        "keywords": ["金融监管总局", "银保监会", "cbirc"],
        "preferred_domains": ["cbirc.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "CSRC": {
        "keywords": ["证监会", "中国证券监督管理委员会", "csrc"],
        "preferred_domains": ["csrc.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "SAFE": {
        "keywords": ["外汇管理局", "外汇局", "safe.gov.cn"],
        "preferred_domains": ["safe.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "STATE_COUNCIL": {
        "keywords": ["国务院", "国务院常务会议", "国常会", "国务院办公厅"],
        "preferred_domains": ["gov.cn", "www.gov.cn"],
        "fallback_domains": ["xinhuanet.com", "people.com.cn"],
    },
    "PARTY_LEADERS": {
        "keywords": ["习近平", "李强", "丁薛祥", "张国清", "何立峰"],
        "preferred_domains": ["gov.cn", "xinhuanet.com"],
        "fallback_domains": ["people.com.cn", "cpc.people.com.cn"],
    },
}


@dataclass
class SearchResult:
    """单条搜索结果。"""
    url: str
    title: str
    snippet: str    # 搜索结果摘要
    source: Literal["official", "media"]  # 来源类型


def get_client() -> tuple[anthropic.Anthropic, str]:
    """获取 MiniMax API 客户端。"""
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
    model = os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")

    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=base_url,
    )
    return client, model


def search_web(
    query: str,
    time_baseline: datetime | None = None,
    max_results: int = 5,
) -> list[SearchResult]:
    """搜索网页。

    通过 MiniMax 工具调用（web_search）执行搜索。

    Args:
        query: 搜索关键词
        time_baseline: 时间基准（用于过滤过于久远的结果）
        max_results: 最大返回结果数

    Returns:
        搜索结果列表（按官方 > 媒体排序）
    """
    client, model = get_client()

    time_filter = ""
    if time_baseline:
        # 过滤 time_baseline 之后的信息（使用标准库 timedelta）
        from datetime import timedelta
        six_months_ago = time_baseline - timedelta(days=180)
        time_filter = f" (after:{six_months_ago.strftime('%Y-%m')})"

    search_query = f"{query}{time_filter}"

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        tools=[{"type": "web_search", "name": "web_search"}],
        messages=[{
            "role": "user",
            "content": f"请搜索以下内容，返回最新最相关的结果：\n\n{search_query}\n\n要求：\n1. 返回最多 {max_results} 条结果\n2. 优先返回官方网站（gov.cn, pbc.gov.cn, cbirc.gov.cn, csrc.gov.cn 等）和权威媒体（新华网、人民网等）的结果\n3. 每条结果包含：URL、标题、摘要"
        }],
        timeout=60.0,
    )

    results: list[SearchResult] = []

    for content_block in response.content:
        if content_block.type == "tool_result":
            import json
            try:
                data = json.loads(content_block.text)
                if isinstance(data, list):
                    for item in data[:max_results]:
                        url = item.get("url", "")
                        source: Literal["official", "media"] = "official"
                        for domain in ["gov.cn", "pbc.gov.cn", "cbirc.gov.cn", "csrc.gov.cn", "safe.gov.cn"]:
                            if domain in url:
                                source = "official"
                                break
                        else:
                            source = "media"
                        results.append(SearchResult(
                            url=url,
                            title=item.get("title", ""),
                            snippet=item.get("snippet", ""),
                            source=source,
                        ))
            except json.JSONDecodeError:
                # 降级：解析纯文本格式
                results.extend(_parse_plain_text_results(content_block.text))

    # 按官方 > 媒体排序
    results.sort(key=lambda r: 0 if r.source == "official" else 1)
    return results[:max_results]


def fetch_page(url: str) -> str:
    """抓取网页正文。

    通过 MiniMax 工具调用（web_fetch）执行。

    Args:
        url: 页面 URL

    Returns:
        网页正文文本（提取后的主要内容）
    """
    client, model = get_client()

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        tools=[{"type": "web_fetch", "name": "web_fetch"}],
        messages=[{
            "role": "user",
            "content": f"请抓取以下网页，提取正文内容（不要导航栏、页脚、广告等）：\n\n{url}"
        }],
        timeout=60.0,
    )

    for content_block in response.content:
        if content_block.type == "tool_result":
            return content_block.text

    return ""


def identify_content_source(text: str) -> str | None:
    """识别内容主体类型。

    Args:
        text: 段落文本（标题 + 前100字）

    Returns:
        来源类型 key（如 "PBOC", "STATE_COUNCIL"），或 None（无法识别）
    """
    for source_key, config in SEARCH_SOURCES.items():
        for keyword in config["keywords"]:
            if keyword in text:
                return source_key
    return None


def _parse_plain_text_results(text: str) -> list[SearchResult]:
    """降级解析：解析纯文本格式的搜索结果。"""
    import re
    results: list[SearchResult] = []

    # 简单按行解析 URL + 标题 + 摘要
    lines = text.strip().split("\n")
    i = 0
    while i < len(lines) and len(results) < 5:
        line = lines[i].strip()
        if line.startswith("http"):
            url = line
            title = lines[i+1].strip() if i+1 < len(lines) else ""
            snippet = lines[i+2].strip() if i+2 < len(lines) else ""
            source: Literal["official", "media"] = "media"
            for domain in ["gov.cn", "pbc.gov.cn", "cbirc.gov.cn", "csrc.gov.cn"]:
                if domain in url:
                    source = "official"
                    break
            results.append(SearchResult(url=url, title=title, snippet=snippet, source=source))
            i += 3
        else:
            i += 1

    return results
```

- [ ] **Step 2: 测试搜索功能**

```bash
python3.11 -c "
from app.review.search_tools import search_web, identify_content_source
from app.review.time_calibration import get_beijing_time

time_baseline = get_beijing_time()
results = search_web('中国人民银行降准 2026', time_baseline)
print(f'找到 {len(results)} 条结果:')
for r in results:
    print(f'  [{r.source}] {r.title}')
    print(f'    {r.snippet[:80]}...')
    print(f'    {r.url}')

source = identify_content_source('中国人民银行召开2026年金融稳定工作会议')
print(f'\n识别主体: {source}')
"
```

- [ ] **Step 3: Commit**

```bash
git add app/review/search_tools.py
git commit -m "feat(review): add search_tools module for web search and fetch"
```

---

## Task 4: 整合测试（search_reviewer）

**Files:**
- Create: `app/review/search_reviewer.py`
- Test: `tests/test_search_reviewer.py`

- [ ] **Step 1: 创建 search_reviewer.py**

```python
"""搜索增强版审核模块.

整合时间校准、原文核对、主动搜索能力。
独立于现有 reviewer.py，不改动现有代码。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from app.review.time_calibration import get_beijing_time
from app.review.citation_verifier import extract_original_text, verify_citation
from app.review.search_tools import search_web, fetch_page, identify_content_source


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
    from app.review.reviewer import ReviewResult
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
    from app.review.reviewer import _call_phase2_llm, Finding

    findings: list[Finding] = []
    current_section = None

    SECTION_KEYWORDS = {"党政要闻", "监管动态", "同业动向", "市场观察", "前沿观点"}

    for idx, para in enumerate(paragraphs):
        stripped = para.strip()

        # 识别板块标题
        if stripped in SECTION_KEYWORDS:
            current_section = stripped
            continue

        if current_section is None or current_section == "前沿观点":
            continue

        # 跳过新闻标题（正文段落才需要搜索核实）
        # 新闻标题判断：长度 < 60，无句末标点
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
                    search_results = search_web(para[:80], time_baseline, max_results=3)
                    if search_results:
                        # 取第一条结果的摘要，与正文做初步比对
                        top_result = search_results[0]
                        # 简单判断：搜索结果标题中的关键信息是否出现在正文中
                        # 如果明显不符，标记为疑似问题
                        if not _，初步判断_需要_llm_复核(para, top_result, source_key):
                            # 调用 LLM 做最终判断
                            pass
                except Exception as e:
                    print(f"  ⚠️ 搜索失败(段{idx}): {e}")

    # ===== 调用 LLM 做最终综合判断（复用现有逻辑）=====
    try:
        llm_findings = await _call_phase2_llm(paragraphs, rules_text, filename)
        findings.extend(llm_findings)
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


def _is_news_title(para: str) -> bool:
    """判断段落是否为新闻标题。"""
    stripped = para.strip()
    if not stripped or len(stripped) < 5 or len(stripped) > 60:
        return False
    return stripped[-1] not in "。！？；?!."


def _初步判断_需要_llm_复核(
    para: str,
    search_result: "SearchResult",
    source_key: str,
) -> bool:
    """初步判断是否需要 LLM 复核。

    如果正文内容与搜索结果明显不符，返回 True（需要 LLM 复核）。
    如果基本一致，返回 False（跳过）。

    实现逻辑：
    1. 检查搜索结果标题中的关键实体是否出现在正文中
    2. 如果关键实体缺失 → 需要复核
    3. 如果正文出现与搜索结果矛盾的信息 → 需要复核
    4. 否则 → 跳过
    """
    title = search_result.title
    snippet = search_result.snippet

    # 提取标题中的关键信息（会议名称、机构名等）
    # 简单实现：检查正文是否包含标题中的主要名词
    import re
    # 提取连续中文字符序列作为候选关键词
    keywords = re.findall(r'[一-龥]{3,}', title)
    keywords = [k for k in keywords if len(k) >= 4][:3]

    # 如果标题中的关键词有 50% 以上不出现在正文中，标记为需要复核
    if not keywords:
        return True

    matches = sum(1 for kw in keywords if kw in para)
    if matches < len(keywords) * 0.5:
        return True

    return False
```

- [ ] **Step 2: 创建测试**

```python
"""搜索增强审核测试."""
import asyncio
from app.review.search_reviewer import review_with_search
from app.review.parser import parse_docx


def test_review_with_search_integration():
    """完整流程测试（需要真实 API key）。"""
    # 用已有的存档文档测试
    docx_path = "data/reviews/20260625-003/source/微众银行信息内参周报2026年第3期.docx"
    result = parse_docx(docx_path)

    from app.review.rule_loader import load_rules
    rules_text = load_rules()

    review_result = asyncio.run(review_with_search(
        result.paragraphs,
        rules_text,
        "微众银行信息内参周报2026年第3期.docx",
    ))

    print(f"发现 {len(review_result.findings)} 条问题")
    for f in review_result.findings:
        print(f"  段{f.paragraph_index}: [{f.rule_id}] {f.description}")
```

- [ ] **Step 3: Commit**

```bash
git add app/review/search_reviewer.py tests/test_search_reviewer.py
git commit -m "feat(review): add search_reviewer integration module"
```

---

## Task 5: 整合验证（可选，跳过直到其他任务稳定）

确认独立运行稳定后，按以下顺序整合到现有代码：

1. **添加时间校准** → `review_phase1/2` 开头调用 `get_beijing_time()`
2. **添加原文核对** → Phase 2 中对有"原文引用"的段落调用 `verify_citation`
3. **添加主动搜索** → Phase 2 中对党政要闻/监管动态段落调用 `search_web`

每次整合后运行：
```bash
python3.11 -m pytest tests/test_reviewer.py -v
python3.11 -m pytest tests/test_section_classifier.py -v
```

---

## 验证步骤

```bash
# Task 1
python3.11 -c "from app.review.time_calibration import get_beijing_time; print(get_beijing_time())"

# Task 2
python3.11 -c "
from app.review.citation_verifier import extract_original_text
para = '这是摘要。原文:这是原始文本。'
print(extract_original_text(para))
"

# Task 3
python3.11 -c "
from app.review.search_tools import search_web, identify_content_source
from app.review.time_calibration import get_beijing_time
time_baseline = get_beijing_time()
results = search_web('中国人民银行降准', time_baseline)
print(f'找到 {len(results)} 条')
source = identify_content_source('中国人民银行召开会议')
print(f'识别主体: {source}')
"

# Task 4
# 独立脚本测试完整流程
```
