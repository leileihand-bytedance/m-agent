"""半月报审核引擎.

与内参周报独立,复用格式类规则和部分 LLM 解析能力.
半月报特点:
  - 文档头含时间范围,如"(2026年4月1日-4月15日)"
  - 一级标题后直接跟正文,无"新闻标题+正文"结构
  - 内容是微众银行自身动态
  - 不需要 title-truncated / content-mismatch / 外部新闻板块相关规则
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from docx.oxml.ns import qn

from .core.model_output import parse_paragraph_findings
from .core.models import Finding, ReviewResult
from .format_checker import check_all_format_rules
from .model_config import build_anthropic_client


# 半月报标准一级标题顺序(可部分出现,但相对顺序不能乱)
HALFMONTHLY_SECTION_ORDER: tuple[str, ...] = (
    "业务动态及成果",
    "工作动态及成果",
    "行内重要会议",
    "获得资质与荣誉",
    "行外联络及交流",
)
HALFMONTHLY_SECTION_TITLES = frozenset(HALFMONTHLY_SECTION_ORDER)
HALFMONTHLY_SECTION_INDEX = {
    title: idx for idx, title in enumerate(HALFMONTHLY_SECTION_ORDER)
}

# 行内领导排序与规范职务(用于 halfmonthly-leader-title)
LEADER_ORDER: tuple[str, ...] = (
    "顾敏",
    "李南青",
    "黄黎明",
    "万军",
    "马智涛",
    "陈峭",
    "方震宇",
    "王立鹏",
    "公立",
    "万磊",
    "江旻",
    "陈婷",
)

LEADER_STANDARD_TITLES: dict[str, str] = {
    "顾敏": "董事长",
    "李南青": "党委书记",
    "黄黎明": "行长、党委副书记",
    "万军": "党委委员、监事会主席",
    "马智涛": "常务副行长、首席信息官",
    "陈峭": "副行长",
    "方震宇": "党委委员、副行长",
    "王立鹏": "党委委员、行长助理、首席财务官、董事会秘书",
    "公立": "党委委员、行长助理",
    "万磊": "企业及机构金融事业群副总裁",
    "江旻": "纪委书记、科技及智能事业群副总裁",
    "陈婷": "个人金融事业群副总裁",
}

LEADER_PARTY_TITLES: dict[str, str] = {
    "李南青": "党委书记",
    "黄黎明": "党委副书记",
    "万军": "党委委员",
    "方震宇": "党委委员",
    "王立鹏": "党委委员",
    "公立": "党委委员",
    "江旻": "纪委书记",
}

PARTY_TITLE_TRIGGER_PATTERN = re.compile(
    r"党委书记|党委副书记|党委委员|纪委书记|党组书记|党组副书记|党组成员|"
    r"党支部书记|党支部副书记|支部书记|支部副书记|党总支书记|党总支副书记"
)

# 默认公开场景中需要省略的职务片段
LEADER_SUPPRESSED_TITLES: dict[str, tuple[str, ...]] = {
    "李南青": ("首席合规官",),
}

# 半月报语义类规则 ID（含代码+LLM）
HALFMONTHLY_SEMANTIC_RULE_IDS = (
    "content-incomplete",
    "halfmonthly-date-mismatch",
    "halfmonthly-section-order",
    "halfmonthly-section-mismatch",
    "content-duplicate",
    "halfmonthly-leader-title",
    "halfmonthly-numbering",
    "halfmonthly-body-format",
)

# LLM 只查部分语义规则；halfmonthly-leader-title / halfmonthly-numbering 由代码预检
HALFMONTHLY_LLM_RULE_IDS = (
    "content-incomplete",
    "halfmonthly-date-mismatch",
    "halfmonthly-section-order",
    "halfmonthly-section-mismatch",
    "content-duplicate",
)


@dataclass(frozen=True)
class DateRange:
    """半月报时间范围."""

    start: date
    end: date

    def __contains__(self, d: date) -> bool:
        return self.start <= d <= self.end


def _chinese_month_day_to_date(
    year: int,
    text: str,
) -> date | None:
    """把'4月15日'或'4月1日'转成 date."""
    text = text.strip()
    m = re.match(r"^(\d{1,2})\s*月\s*(\d{1,2})\s*日?$", text)
    if not m:
        return None
    try:
        return date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def parse_halfmonthly_date_range(paragraphs: list[str]) -> DateRange | None:
    """从半月报文档头提取时间范围.

    支持格式:
      - (2026年4月1日-4月15日)
      - 2026年4月1日-4月15日
      - 2026年4月1日—4月15日
      - 2026年4月1日至4月15日
    """
    header = "\n".join(paragraphs[:5])

    # 统一连接符
    normalized = header.replace("—", "-").replace("至", "-")

    # 匹配: YYYY年M月D日-M月D日 或 YYYY年M月D日-YYYY年M月D日
    m = re.search(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?\s*[-]\s*(?:(\d{4})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?",
        normalized,
    )
    if not m:
        return None

    start_year = int(m.group(1))
    start_month = int(m.group(2))
    start_day = int(m.group(3))
    end_year = m.group(4)
    end_month = int(m.group(5))
    end_day = int(m.group(6))

    try:
        start = date(start_year, start_month, start_day)
        end = date(int(end_year) if end_year else start_year, end_month, end_day)
        if end < start:
            return None
        return DateRange(start=start, end=end)
    except ValueError:
        return None


def _extract_dates_from_paragraph(paragraph: str) -> list[tuple[date, str]]:
    """从段落中提取可能的日期,返回(日期, 原文片段)列表.

    目前只处理当年日期,如"4月3日"、"4月13日".
    不处理跨年的'2025年X月X日'(半月报不会出现).
    """
    dates: list[tuple[date, str]] = []
    current_year = datetime.now().year

    for m in re.finditer(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", paragraph):
        try:
            d = date(current_year, int(m.group(1)), int(m.group(2)))
            dates.append((d, m.group(0)))
        except ValueError:
            continue

    return dates


def _check_date_range(
    paragraphs: list[str],
    date_range: DateRange | None,
) -> list[Finding]:
    """代码化检查段落事件时间是否超出半月报范围."""
    if date_range is None:
        return []

    findings = []
    for idx, para in enumerate(paragraphs):
        stripped = para.strip()
        if stripped in HALFMONTHLY_SECTION_TITLES:
            continue

        for d, date_text in _extract_dates_from_paragraph(stripped):
            # 允许 2 天浮动
            extended_start = date_range.start
            extended_end = date.fromordinal(date_range.end.toordinal() + 2)
            if d < extended_start or d > extended_end:
                findings.append(Finding(
                    rule_id="halfmonthly-date-mismatch",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=para,
                    description=f"事件时间{d.month}月{d.day}日超出半月报范围"
                                f"{date_range.start.month}月{date_range.start.day}日"
                                f"-{date_range.end.month}月{date_range.end.day}日",
                    target_text=date_text,
                ))
                break  # 一段报一次即可

    return findings


def _check_leader_title(paragraphs: list[str]) -> list[Finding]:
    """代码化检查领导职务与排序规范.

    目前覆盖:
      - 李南青默认不写"首席合规官"
      - 黄黎明通常不写"党委副书记",除非同条信息中其他人员已采用党内职务口径
      - 除李南青外,同条信息里只要其他内部/第三方人员采用党内职务口径,相关领导应补齐党内职务
      - 同一段落内多位领导排序错误
    """
    findings = []

    for idx, para in enumerate(paragraphs):
        stripped = para.strip()
        if stripped in HALFMONTHLY_SECTION_TITLES:
            continue

        found_leaders: list[tuple[int, str]] = []
        for name in LEADER_ORDER:
            pos = stripped.find(name)
            if pos != -1:
                found_leaders.append((pos, name))
        found_leaders.sort(key=lambda x: x[0])

        leader_segments: dict[str, str] = {}
        for i, (start, name) in enumerate(found_leaders):
            seg_start = found_leaders[i - 1][0] if i > 0 else 0
            end = found_leaders[i + 1][0] if i + 1 < len(found_leaders) else len(stripped)
            leader_segments[name] = stripped[seg_start:end]

        # 1. 检测默认场景下不应出现的职务片段
        for name, suppressed in LEADER_SUPPRESSED_TITLES.items():
            segment = leader_segments.get(name)
            if not segment:
                continue
            for title in suppressed:
                if title in segment:
                    findings.append(Finding(
                        rule_id="halfmonthly-leader-title",
                        paragraph_index=idx,
                        line_number=idx + 1,
                        original_text=para,
                        description=f"{name}默认不应出现'{title}'(特殊场景除外)",
                        target_text=title,
                    ))
                    break

        # 2. 判断是否采用了"党内职务口径"
        party_title_written_names = {
            name
            for name, segment in leader_segments.items()
            if LEADER_PARTY_TITLES.get(name) and LEADER_PARTY_TITLES[name] in segment
        }
        remaining_text = stripped
        for name in party_title_written_names:
            remaining_text = remaining_text.replace(LEADER_PARTY_TITLES[name], "", 1)
        has_third_party_party_title = (
            PARTY_TITLE_TRIGGER_PATTERN.search(remaining_text) is not None
        )

        huang_segment = leader_segments.get("黄黎明")
        other_party_title_context_for_huang = bool(
            party_title_written_names - {"李南青", "黄黎明"}
        ) or has_third_party_party_title
        if (
            huang_segment
            and LEADER_PARTY_TITLES["黄黎明"] in huang_segment
            and not other_party_title_context_for_huang
        ):
            findings.append(Finding(
                rule_id="halfmonthly-leader-title",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description="黄黎明通常不写'党委副书记'(除非本条信息整体采用党内职务口径)",
                target_text="党委副书记",
            ))

        for name, party_title in LEADER_PARTY_TITLES.items():
            if name == "李南青":
                continue

            segment = leader_segments.get(name)
            if not segment or party_title in segment:
                continue

            other_party_title_context = bool(
                party_title_written_names - {"李南青", name}
            ) or has_third_party_party_title
            if not other_party_title_context:
                continue

            findings.append(Finding(
                rule_id="halfmonthly-leader-title",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"当前信息已采用党内职务口径，{name}应补充'{party_title}'",
                target_text=name,
            ))

        # 3. 同一段落内领导排序检查
        order_indices = [LEADER_ORDER.index(name) for _, name in found_leaders]
        for i in range(1, len(order_indices)):
            if order_indices[i] < order_indices[i - 1]:
                earlier_name = found_leaders[i - 1][1]
                later_name = found_leaders[i][1]
                findings.append(Finding(
                    rule_id="halfmonthly-leader-title",
                    paragraph_index=idx,
                    line_number=idx + 1,
                    original_text=para,
                    description=f"领导排序错误:{later_name}不应在{earlier_name}之前",
                    target_text=later_name,
                ))
                break  # 一段报一次即可

    return findings


def _check_section_order(paragraphs: list[str]) -> list[Finding]:
    """代码化检查半月报一级标题顺序."""
    findings: list[Finding] = []
    last_seen_title: str | None = None
    last_seen_index = -1

    for paragraph_index, title in _find_section_boundaries(paragraphs):
        current_index = HALFMONTHLY_SECTION_INDEX[title]
        if current_index < last_seen_index and last_seen_title is not None:
            findings.append(Finding(
                rule_id="halfmonthly-section-order",
                paragraph_index=paragraph_index,
                line_number=paragraph_index + 1,
                original_text=paragraphs[paragraph_index],
                description=f"一级标题顺序错误：'{title}'应排在'{last_seen_title}'之前",
                target_text=title,
            ))
            continue

        last_seen_title = title
        last_seen_index = current_index

    return findings


def _check_numbering_continuity(
    paragraphs: list[str],
    numbering: tuple[int | None, ...],
) -> list[Finding]:
    """检查编号连续性：编号不包含五大板块标题，跨板块编号应连续."""
    if not numbering:
        return []

    findings: list[Finding] = []
    last_number = 0

    for idx, (para, num) in enumerate(zip(paragraphs, numbering)):
        if para.strip() in HALFMONTHLY_SECTION_TITLES:
            continue
        if num is None:
            continue

        expected = last_number + 1
        if num != expected:
            findings.append(Finding(
                rule_id="halfmonthly-numbering",
                paragraph_index=idx,
                line_number=idx + 1,
                original_text=para,
                description=f"编号不连续，应为{expected}号，实际为{num}号",
                target_text=str(num),
            ))
        last_number = num

    return findings


def _resolve_font_at_run(run) -> tuple[str | None, int | None]:
    """从 run 层解析中文字体名和字号(EMU)，没有则返回 None. 字号来自 w:sz (half-pt)."""
    rpr = run._r.find(qn('w:rPr'))
    if rpr is None:
        return None, None
    ea = None
    sz_val = None
    rfonts = rpr.find(qn('w:rFonts'))
    if rfonts is not None:
        ea = rfonts.get(qn('w:eastAsia'))
    sz_el = rpr.find(qn('w:sz'))
    if sz_el is not None:
        sz_val = int(sz_el.get(qn('w:val'))) * 6350  # half-pt → EMU
    return ea, sz_val


def _resolve_font_at_style(style) -> tuple[str | None, int | None]:
    """从样式层解析中文字体名和字号(EMU)."""
    if style is None:
        return None, None
    srpr = style.element.find(qn('w:rPr'))
    if srpr is None:
        return None, None
    ea = None
    sz_val = None
    srfonts = srpr.find(qn('w:rFonts'))
    if srfonts is not None:
        ea = srfonts.get(qn('w:eastAsia'))
    ssz = srpr.find(qn('w:sz'))
    if ssz is not None:
        sz_val = int(ssz.get(qn('w:val'))) * 6350
    return ea, sz_val


def _resolve_font_at_defaults(doc) -> tuple[str | None, int | None]:
    """从文档默认样式解析中文字体名和字号(EMU)."""
    defaults = doc.element.find(qn('w:docDefaults'))
    if defaults is None:
        return None, None
    rprdefault = defaults.find(qn('w:rPrDefault'))
    if rprdefault is None:
        return None, None
    drpr = rprdefault.find(qn('w:rPr'))
    if drpr is None:
        return None, None
    ea = None
    sz_val = None
    drfonts = drpr.find(qn('w:rFonts'))
    if drfonts is not None:
        ea = drfonts.get(qn('w:eastAsia'))
    dsz = drpr.find(qn('w:sz'))
    if dsz is not None:
        sz_val = int(dsz.get(qn('w:val'))) * 6350
    return ea, sz_val


def _paragraph_has_numbering(dp) -> bool:
    """判断段落是否使用了 Word 自动编号."""
    ppr = dp._p.find(qn('w:pPr'))
    if ppr is None:
        return False
    return ppr.find(qn('w:numPr')) is not None


def _check_body_format(
    paragraphs: list[str],
    docx_path: Path,
) -> list[Finding]:
    """检查正文段落格式：中文字体、字号、行距、首行缩进."""
    from docx import Document as OpenDocx

    # 找到第一个一级标题索引（正文从第一个一级标题后开始）
    first_section_idx = -1
    for i, p in enumerate(paragraphs):
        if p.strip() in HALFMONTHLY_SECTION_TITLES:
            first_section_idx = i
            break

    if first_section_idx == -1:
        return []

    # 收集正文段落索引（第一个一级标题之后且非一级标题本身）
    body_indices: set[int] = set()
    for i, p in enumerate(paragraphs):
        if i >= first_section_idx and p.strip() not in HALFMONTHLY_SECTION_TITLES:
            body_indices.add(i)

    if not body_indices:
        return []

    doc = OpenDocx(str(docx_path))
    findings: list[Finding] = []
    para_idx = 0

    for dp in doc.paragraphs:
        text = dp.text.strip()
        if not text:
            continue  # 与 parser 同步：跳过空段

        if para_idx not in body_indices:
            para_idx += 1
            continue

        issues: list[str] = []

        # --- 字体检查（逐级解析：run → 段落样式 → 文档默认样式） ---
        run_fonts: set[str] = set()
        run_sizes: set[int] = set()
        for run in dp.runs:
            ea, sz = _resolve_font_at_run(run)
            if ea:
                run_fonts.add(ea)
            if sz:
                run_sizes.add(sz)

        if not run_fonts:
            ea, _ = _resolve_font_at_style(dp.style)
            if ea:
                run_fonts.add(ea)
        if not run_fonts:
            ea, _ = _resolve_font_at_defaults(doc)
            if ea:
                run_fonts.add(ea)

        if not run_sizes:
            _, sz = _resolve_font_at_style(dp.style)
            if sz:
                run_sizes.add(sz)
        if not run_sizes:
            _, sz = _resolve_font_at_defaults(doc)
            if sz:
                run_sizes.add(sz)

        # 中文字体
        if run_fonts and "仿宋" not in run_fonts:
            issues.append(f"中文字体应为仿宋，当前为{'/'.join(sorted(run_fonts))}")
        elif not run_fonts:
            issues.append("中文字体未设置，应为仿宋")

        # 字号
        expected_size = 203200  # 16pt in EMU
        if run_sizes and any(s != expected_size for s in run_sizes):
            sizes_detail = "/".join(f"{s/12700:.0f}pt" for s in sorted(run_sizes))
            issues.append(f"字号应为16pt，当前为{sizes_detail}")
        elif not run_sizes:
            issues.append("字号未设置，应为16pt")

        # --- 行距检查 ---
        pf = dp.paragraph_format
        ls = pf.line_spacing
        ls_rule = pf.line_spacing_rule
        if ls is not None:
            if ls_rule is not None and ls_rule.name == "MULTIPLE":
                if abs(ls - 1.0) > 0.05:
                    issues.append(f"行距应为单倍，当前为{ls:.2f}倍")
            else:
                issues.append(f"行距应为默认单倍，当前为固定{ls/12700:.0f}pt")

        # --- 首行缩进检查（编号段落跳过） ---
        has_num = _paragraph_has_numbering(dp)
        if not has_num:
            fi = pf.first_line_indent
            if fi is not None:
                fi_cm = fi / 360000  # EMU to cm
                if fi_cm < 0.3 or fi_cm > 0.6:
                    issues.append(f"首行缩进应在0.3-0.6cm之间，当前为{fi_cm:.2f}cm")
            else:
                issues.append("首行缩进缺失，正文段落应有约2汉字(0.45cm)首行缩进")

        if issues:
            findings.append(Finding(
                rule_id="halfmonthly-body-format",
                paragraph_index=para_idx,
                line_number=para_idx + 1,
                original_text=text,
                description="；".join(issues),
                target_text=issues[0][:30],
            ))

        para_idx += 1

    return findings


def _find_section_boundaries(paragraphs: list[str]) -> list[tuple[int, str]]:
    """返回每个一级标题出现的段号及标题文本."""
    return [
        (idx, para.strip())
        for idx, para in enumerate(paragraphs)
        if para.strip() in HALFMONTHLY_SECTION_TITLES
    ]


def _build_halfmonthly_prompt(
    rules_text: str,
    paragraphs: list[str],
    filename: str,
    date_range: DateRange | None,
) -> str:
    """构造半月报审核 prompt."""
    paras_text = "\n\n".join(
        f"[段 {i+1}]\n{p}" for i, p in enumerate(paragraphs)
    )

    date_range_text = "未识别"
    if date_range:
        date_range_text = (
            f"{date_range.start.year}年{date_range.start.month}月{date_range.start.day}日"
            f"-{date_range.end.month}月{date_range.end.day}日"
        )

    section_boundaries = _find_section_boundaries(paragraphs)
    sections_text = "\n".join(
        f"[段 {idx + 1}] {title}" for idx, title in section_boundaries
    ) or "(未识别到标准一级标题)"

    prompt = f"""你是一位严谨的半月报审核员。

# 审核规则清单

{rules_text}

# 待审半月报

文件名:{filename}

文档头时间范围:{date_range_text}

识别到的一级标题:
{sections_text}

标准一级标题顺序:
{" -> ".join(HALFMONTHLY_SECTION_ORDER)}

# 正文段落

{paras_text}

# 你的任务

按以下步骤思考并输出:

## 步骤 1:识别文档结构

- 文档头:哪几段
- 一级标题:分别在第几段
- 正文区:每个一级标题下的段落范围

## 步骤 2:按规则审核

重点检查:
- content-incomplete:正文是否戛然而止、缺宾语
- halfmonthly-date-mismatch:事件时间是否超出文档头时间范围(代码已做预检,你在此基础上复核)
- halfmonthly-section-order:一级标题顺序是否符合标准顺序(代码已做预检,你在此基础上复核)
- halfmonthly-section-mismatch:段落内容是否放错了一级标题
- content-duplicate:同一件事是否重复出现
- halfmonthly-leader-title:由代码预检,你**不要**再检查此项

## 步骤 3:输出 JSON

**严格按以下格式输出,只输出 JSON,不要任何其他文字:**

```json
{{
  "reasoning": "简要分析思路,100字以内",
  "issues": [
    {{"paragraph_index": 0, "rule_id": "xxx", "target_text": "错误片段", "original_text": "该段完整原文", "description": "问题描述"}}
  ]
}}
```

**关键规则:**
- paragraph_index 从 0 开始
- rule_id 必须是以下之一:{", ".join(HALFMONTHLY_LLM_RULE_IDS)}
- target_text 必须是原文里真实出现的短片段(如日期、人名、职务、短语),用于精确定位标红位置
- original_text 必须是该段的**完整原文**,不要截断
- **不确定的问题不要写,宁可漏报不要误报**
- 文档完全没问题 → `{{"issues": []}}`
- 每条 issue 的 description 要简洁,不超过50字
"""
    return prompt


def _call_halfmonthly_llm(
    prompt: str,
    paragraphs: list[str],
) -> list[Finding]:
    """调用 LLM 做半月报语义审核."""
    client, model_name = build_anthropic_client()
    message = client.messages.create(
        model=model_name,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
        timeout=180.0,
    )

    text_parts = []
    for block in message.content:
        if hasattr(block, "text") and block.text:
            text_parts.append(block.text)
    output = "\n".join(text_parts)

    findings, _ = parse_paragraph_findings(
        output,
        paragraphs,
        HALFMONTHLY_LLM_RULE_IDS,
    )
    return findings


async def review_halfmonthly(
    paragraphs: list[str],
    rules_text: str,
    filename: str,
    numbering: tuple[int | None, ...] = (),
    file_path: Path | None = None,
) -> ReviewResult:
    """半月报单阶段审核入口.

    返回 ReviewResult,包含格式类 + 语义类 findings.
    """
    if not paragraphs:
        return ReviewResult(
            findings=[],
            total_rules=len(HALFMONTHLY_SEMANTIC_RULE_IDS) + 6,
            passed_rules=len(HALFMONTHLY_SEMANTIC_RULE_IDS) + 6,
            filename=filename,
        )

    date_range = parse_halfmonthly_date_range(paragraphs)

    # 1. 格式类规则(复用)
    format_findings = check_all_format_rules(paragraphs)

    # 2. 时间范围代码预检
    date_findings = _check_date_range(paragraphs, date_range)

    # 3. 领导职务与排序代码预检
    leader_findings = _check_leader_title(paragraphs)

    # 4. 一级标题顺序代码预检
    section_order_findings = _check_section_order(paragraphs)

    # 5. 编号连续性代码预检
    numbering_findings = _check_numbering_continuity(paragraphs, numbering)

    # 6. 正文格式代码预检（字体、字号、行距、首行缩进）
    body_format_findings: list[Finding] = []
    if file_path is not None and file_path.exists():
        body_format_findings = _check_body_format(paragraphs, file_path)

    # 7. LLM 语义审核
    prompt = _build_halfmonthly_prompt(rules_text, paragraphs, filename, date_range)
    print(f"  半月报 prompt_chars={len(prompt)}", flush=True)

    semantic_findings: list[Finding] = []
    llm_errors: list[str] = []

    for attempt in range(2):
        try:
            loop = asyncio.get_running_loop()
            llm_findings = await loop.run_in_executor(
                None, _call_halfmonthly_llm, prompt, paragraphs
            )
            semantic_findings.extend(llm_findings)
            print(f"  半月报 LLM 第 {attempt + 1} 次: {len(llm_findings)} 条", flush=True)
            if llm_findings:
                break
        except Exception as exc:
            llm_errors.append(str(exc))
            print(f"  半月报 LLM 第 {attempt + 1} 次失败: {exc}", flush=True)

    # 8. 合并:所有代码预检结果加入语义结果
    semantic_findings.extend(date_findings)
    semantic_findings.extend(leader_findings)
    semantic_findings.extend(section_order_findings)
    semantic_findings.extend(numbering_findings)
    semantic_findings.extend(body_format_findings)

    # 如果 LLM 全部失败且没有代码预检结果
    if not semantic_findings and llm_errors:
        return ReviewResult(
            findings=[Finding(
                rule_id="__llm_error__",
                paragraph_index=0,
                line_number=1,
                original_text="(LLM 调用失败)",
                description=f"半月报 LLM 调用失败:{'; '.join(llm_errors)}",
            )],
            total_rules=len(HALFMONTHLY_SEMANTIC_RULE_IDS) + 6,
            passed_rules=0,
            filename=filename,
        )

    # 9. 去重（含 target_text 以区分同段落不同人物/问题）
    merged: dict[tuple[str, int, str], Finding] = {}
    for f in semantic_findings:
        key = (f.rule_id, f.paragraph_index, f.target_text)
        if key not in merged or len(f.description) > len(merged[key].description):
            merged[key] = f
    semantic_findings = list(merged.values())

    # 10. 合并格式类 + 语义类
    all_findings = list(semantic_findings)
    all_findings.extend(format_findings)
    all_findings.sort(key=lambda f: f.paragraph_index)

    # 11. 计算通过规则数
    hit_rule_ids = {f.rule_id for f in all_findings if not f.rule_id.startswith("__")}
    total_rules = len(HALFMONTHLY_SEMANTIC_RULE_IDS) + 6  # N条语义 + 6条全局格式
    passed_rules = max(0, total_rules - len(hit_rule_ids))

    return ReviewResult(
        findings=all_findings,
        total_rules=total_rules,
        passed_rules=passed_rules,
        filename=filename,
    )
