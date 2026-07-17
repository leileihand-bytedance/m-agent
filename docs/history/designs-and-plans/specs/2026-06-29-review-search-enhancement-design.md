# 智能审核搜索增强方案

## Context

当前审核问题：内容是 AI 整理的，可能改变原意。现有 LLM 审核依赖模型"记忆"，不稳定漏报。

目标：让 Bot 具备"核对原文"和"主动搜索"能力，模拟从业人员审核流程。

**约束**：不改动现有 `reviewer.py` / `main.py`，新能力独立开发测试后再整合。

---

## 审核流程重构

```
用户发送 .docx
      ↓
① 时间校准（代码执行）
   → 联网获取北京时间，作为所有搜索的 TIME_BASELINE
      ↓
② Phase 1（格式 + 基础语义）
   → 格式检查（正则，稳定）
   → title-truncated、content-incomplete、toc-mismatch（LLM CoT）
      ↓
③ Phase 2（内容质量，搜索增强）
   ├─ 有"原文引用"段落：
   │   → 提取原文，与摘要/标题做一致性核对
   │
   └─ 党政要闻/监管动态段落（无原文引用）：
       → 识别内容主体（谁？哪个部门？）
       → 搜索该部门官网最新信息
       → 核对：会议名称、数据、人物职务、时间
       → 降级：官网不可达 → 权威媒体
      ↓
④ LLM 综合判断（基于搜索结果）
   → 输出最终审核结论
      ↓
⑤ 存档（原始文本、搜索记录、审核结论）
```

---

## 新建文件

### `app/review/time_calibration.py`

获取北京时间，作为审核的时间基准。

```python
import urllib.request
import re
from datetime import datetime

def get_beijing_time() -> datetime:
    """联网获取北京时间。

    访问授时网站，返回 datetime 对象（北京时间）。

    Returns:
        datetime: 北京时间
    Raises:
        RuntimeError: 无法获取时间
    """
    # 备选授时源
    SOURCES = [
        "http://time.tuebaba.com",
        "https://www.timeapi.org/cn/now",
    ]

    for url in SOURCES:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                text = resp.read().decode("utf-8")
                # 解析时间字符串，返回 datetime
                # ...
        except Exception:
            continue

    raise RuntimeError("无法获取北京时间")
```

**测试**：单独运行，确认能打印出当前北京时间。

---

### `app/review/citation_verifier.py`

对有"原文引用"的段落做一致性核对。

```python
from dataclasses import dataclass

@dataclass
class VerificationResult:
    accurate: bool          # 摘要是否准确
    deviations: list[str]   # 偏差描述列表（为何不准确）
    missing_key_points: list[str]  # 摘要遗漏的关键点

def extract_original_text(paragraph: str) -> str | None:
    """提取段落中的原文引用。

    段落格式："原文:xxxxx"
    返回原文部分，不含"原文:"前缀。

    Returns:
        原文文本，或 None（无原文引用）
    """
    if "原文:" not in paragraph:
        return None
    return paragraph.split("原文:", 1)[1].strip()

def verify_citation(summary: str, original: str) -> VerificationResult:
    """核对摘要是否准确反映原文。

    1. 提取原文关键信息（谁、何时、何事、关键数据）
    2. 检查摘要是否：
       - 遗漏原文关键点
       - 引入原文没有的信息
       - 改变原文语义
    3. 返回核对结果

    Args:
        summary: 摘要/正文文本
        original: 原文引用文本

    Returns:
        VerificationResult: 核对结果
    """
    # LLM 调用做细粒度核对
    # ...
```

**测试**：
- 有偏差的例子：摘要添加了原文没有的数据 → 检测出偏差
- 准确的例子：摘要准确概括原文 → accurate=True

---

### `app/review/search_tools.py`

封装 MiniMax 工具调用，支持搜索和网页抓取。

```python
from dataclasses import dataclass

@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str   # 搜索结果摘要
    source: str   # 来源（官网/权威媒体）

def search_web(query: str, time_baseline: datetime | None = None) -> list[SearchResult]:
    """搜索网页，获取相关结果。

    Args:
        query: 搜索关键词
        time_baseline: 时间基准（用于过滤过于久远的结果）

    Returns:
        搜索结果列表（最多 5 条）
    """
    # 调用 MiniMax 工具（web_search）
    # 解析结果，返回 SearchResult 列表
    # 按官网 > 权威媒体排序
    # ...

def fetch_page(url: str) -> str:
    """抓取网页正文。

    Args:
        url: 页面 URL

    Returns:
        网页正文文本
    """
    # 调用 MiniMax 工具（web_fetch）
    # 返回提取后的正文内容
    # ...
```

**搜索优先级**：
| 内容主体 | 首选来源 | 降级来源 |
|---------|---------|---------|
| 中国人民银行 | pbc.gov.cn | 新华网/人民日报 |
| 金融监管总局 | cbirc.gov.cn | 同上 |
| 证监会 | csrc.gov.cn | 同上 |
| 外汇管理局 | safe.gov.cn | 同上 |
| 国务院/国务院各部委 | gov.cn | 新华网 |
| 党和国家领导人 | gov.cn / 新华网 | 人民网 |

---

### `app/review/search_reviewer.py`

整合新能力，完整流程的搜索增强版审核。

```python
async def review_with_search(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
) -> ReviewResult:
    """搜索增强版审核。

    完整流程：
    1. 时间校准（get_beijing_time）
    2. Phase 1 格式检查（复用现有 check_all_format_rules）
    3. Phase 2 搜索增强判断：
       - 有原文引用 → citation_verifier 核对
       - 党政要闻/监管动态 → search_tools 搜索核实
       - 其他 → LLM 判断
    4. 合并结果，存档
    """
    # ① 时间校准
    time_baseline = get_beijing_time()

    # ② Phase 1（复用现有逻辑，不改动）
    from .reviewer import review_phase1, check_all_format_rules
    phase1_result = await review_phase1(paragraphs, rules_text, filename)

    # ③ Phase 2 搜索增强
    phase2_findings = await _review_phase2_with_search(
        paragraphs, rules_text, filename, time_baseline
    )

    # ④ 合并结果
    all_findings = list(phase1_result.findings) + phase2_findings
    return ReviewResult(findings=all_findings, ...)

async def _review_phase2_with_search(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
    time_baseline: datetime,
) -> list[Finding]:
    """Phase 2 搜索增强判断。"""
    # 遍历正文段落
    # 对每段：
    #   - 检测是否有原文引用
    #   - 识别内容主体（党政要闻/监管动态）
    #   - 调用搜索/核对
    #   - LLM 综合判断
    # ...
```

---

## 独立测试流程

新能力开发完成后，按顺序测试：

```bash
# 1. 时间校准
python3.11 -c "from app.review.time_calibration import get_beijing_time; print(get_beijing_time())"

# 2. 原文核对
python3.11 -c "from app.review.citation_verifier import verify_citation; print(...)"

# 3. 搜索能力
python3.11 -c "from app.review.search_tools import search_web; print(search_web('中国人民银行降准'))"

# 4. 完整流程测试（独立脚本）
python3.11 -c "
import asyncio
from app.review.search_reviewer import review_with_search
from app.review.parser import parse_docx

result = parse_docx('测试文档.docx')
review_result = asyncio.run(review_with_search(result.paragraphs, ...))
print(f'发现 {len(review_result.findings)} 条问题')
"
```

---

## 整合策略

确认稳定后，分三步整合：

1. **添加时间校准** → `review_phase1/2` 开头调用 `get_beijing_time()`
2. **添加原文核对** → `check_section_mismatch` 调用 `citation_verifier`
3. **添加主动搜索** → Phase 2 新增搜索调用

每次整合后跑现有测试，确认无回归。

---

## 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 搜索耗时长（每段 1-3 分钟） | 审核时间从 90s 升至 5-10 分钟 | Phase 2 仅对关键段落搜索，逐步优化 |
| 搜索失败（网站不可达） | 降级到权威媒体，可能不够准确 | 降级链路：官网 → 权威媒体 → 跳过该段 |
| LLM 工具调用不稳定 | 搜索结果时好时坏 | MiniMax 已有超时保护，失败重试 |
| 搜索结果噪音 | 权威媒体的解读性报道与官网原文有差异 | prompt 要求优先使用官网原文 |
