from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta

from skills.internal_weekly.dates import parse_flexible_date
from skills.internal_weekly.schema import (
    FrontierSelection,
    MarketEvidenceBundle,
    SourceRecord,
    WeeklyItem,
)


SECTION_ORDER = ("党政要闻", "监管动态", "同业动向", "市场观察", "前沿观点")
REGULATORY_ENTITIES = frozenset(
    {
        "中国人民银行",
        "人民银行",
        "央行",
        "PBOC",
        "国家金融监督管理总局",
        "金融监管总局",
        "中国证券监督管理委员会",
        "证监会",
        "CSRC",
        "国家外汇管理局",
        "外汇管理局",
        "外汇局",
    }
)
PARTY_GOV_ENTITIES = frozenset(
    {
        "习近平",
        "李强",
        "丁薛祥",
        "何立峰",
        "张国清",
        "国务院",
        "国务院党组",
        "国务院常务会议",
        "国务院办公厅",
        "商务部",
        "外交部",
        "国防部",
        "公安部",
        "民政部",
        "司法部",
        "财政部",
        "人力资源社会保障部",
        "自然资源部",
        "生态环境部",
        "住房城乡建设部",
        "交通运输部",
        "水利部",
        "农业农村部",
        "文化和旅游部",
        "国家卫生健康委",
        "应急管理部",
        "审计署",
        "退役军人事务部",
        "国家发改委",
        "国家能源局",
        "工信部",
        "科学技术部",
        "教育部",
        "科技部",
        "国家广电总局",
        "体育总局",
        "统计局",
        "国家市场监管总局",
        "中央纪委国家监委",
        "中央纪委",
        "中纪委",
    }
)
BANKING_ENTITIES = frozenset(
    {
        "微众银行",
        "网商银行",
        "富民银行",
        "金城银行",
        "蓝海银行",
        "振兴银行",
        "民营银行",
        "数字银行",
        # 生产 Skill 在复制审核基线后补充的同类明确主体。
        "新网银行",
        "众邦银行",
        "苏宁银行",
        "虚拟银行",
    }
)
MARKET_OBSERVATION_MARKERS = frozenset(
    {
        "A股综述",
        "A股市场",
        "A股主要指数",
        "A股主要指数集体走强",
        "港股综述",
        "港股市场",
        "港股主要指数",
        "美股综述",
        "美股市场",
        "美股三大指数",
        "沪深股市",
        "沪深两市",
        "沪深300",
        "上证指数",
        "深证成指",
        "创业板指",
        "科创50",
        "中证500",
        "中证1000",
        "债市评论",
        "汇市评论",
        "汇率市场",
        "大宗商品",
        "商品市场",
        "本周A股",
        "本周港股",
        "本周美股",
        "上周A股",
        "上周港股",
        "上周美股",
        "本月A股",
        "本月港股",
        "本月美股",
        "资本市场综述",
        "股市收评",
        "债市",
        "汇市",
        "黄金",
    }
)
REQUIRED_MARKET_CODES = {
    "weekly_a": ("000001", "399001", "399006"),
    "monday_a": ("000001", "399001", "399006"),
    "weekly_hk": ("HSI", "HSTECH", "HSCEI"),
    "weekly_us": ("DJIA", "COMP", "SPX"),
}
CANONICAL_MARKET_NAMES = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "HSI": "恒生指数",
    "HSTECH": "恒生科技指数",
    "HSCEI": "恒生中国企业指数",
    "DJIA": "道琼斯指数",
    "COMP": "纳斯达克指数",
    "SPX": "标普500指数",
}


def calculate_weekly_window(value: date | datetime) -> tuple[date, date, date]:
    """返回最近一个周一的出版日，以及它之前的完整自然周。"""
    current = value.date() if isinstance(value, datetime) else value
    publication_date = current - timedelta(days=current.weekday())
    period_end = publication_date - timedelta(days=1)
    period_start = period_end - timedelta(days=6)
    return publication_date, period_start, period_end


def classify_section(title: str, body: str) -> str:
    """按周报自身持有的分类规则给普通材料归类。"""
    text = f"{title}\n{body[:180]}"
    for markers, section in (
        (REGULATORY_ENTITIES, "监管动态"),
        (PARTY_GOV_ENTITIES, "党政要闻"),
        (BANKING_ENTITIES, "同业动向"),
        (MARKET_OBSERVATION_MARKERS, "市场观察"),
    ):
        for marker in markers:
            if marker not in text:
                continue
            if section != "市场观察" and _is_entity_in_citation_context(text, marker):
                continue
            return section
    return "市场观察"


def _is_entity_in_citation_context(text: str, entity: str) -> bool:
    """排除“据某机构数据”等只把机构当作信息来源的语境。"""
    start = 0
    while True:
        position = text.find(entity, start)
        if position == -1:
            return False
        prefix = text[max(0, position - 30) : position]
        suffix = text[position + len(entity) : position + len(entity) + 10]
        if any(prefix.endswith(item) for item in ("据", "根据", "来自", "依据")):
            return True
        if any(
            suffix.startswith(item)
            for item in ("数据显示", "公布的数据", "统计数据", "发布的数据")
        ):
            return True
        start = position + 1


def validate_frontier_selection(selection: FrontierSelection, report_body: str) -> list[str]:
    """前沿观点只能逐字摘录已读取的研报内容。"""
    if not selection.selected_passages:
        raise ValueError("前沿观点没有可核验的研报摘录")
    for passage in selection.selected_passages:
        if not passage.strip() or passage.strip() not in report_body:
            raise ValueError("前沿观点摘录必须逐字存在于研报页面正文中")
    return [passage.strip() for passage in selection.selected_passages]


def _source_id(url: str) -> str:
    return "src-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _describe_change(change: float) -> str:
    if abs(change) < 0.005:
        return "持平0.00%"
    return f"{'上涨' if change > 0 else '下跌'}{abs(change):.2f}%"


def _render_group(label: str, series: list[object]) -> str:
    values = "，".join(
        f"{CANONICAL_MARKET_NAMES[item.index_code.upper()]}"
        f"{_describe_change(_series_change(item))}"
        for item in series
    )
    return f"{label}，{values}。"


def _series_change(item: object) -> float:
    reported = getattr(item, "reported_change_pct", None)
    if reported is not None:
        return float(reported)
    start = getattr(item, "start_close", None)
    end = getattr(item, "end_close", None)
    if start is None or end is None:
        raise ValueError(f"行情缺少涨跌幅或起止收盘值：{item.scope}/{item.index_code}")
    return (float(end) / float(start) - 1) * 100


def build_market_item(
    bundle: MarketEvidenceBundle,
    *,
    publication_date: date,
    retrieved_at: str | None = None,
) -> tuple[WeeklyItem, list[SourceRecord]]:
    """用结构化收盘价生成固定位置的资本市场综述。"""
    grouped: dict[str, dict[str, object]] = {}
    for item in bundle.series:
        grouped.setdefault(item.scope, {})[item.index_code.upper()] = item
        has_reported_change = item.reported_change_pct is not None
        has_any_close = item.start_close is not None or item.end_close is not None
        has_both_closes = item.start_close is not None and item.end_close is not None
        if has_reported_change and has_any_close:
            raise ValueError(f"行情不能同时返回涨跌幅和收盘值：{item.scope}/{item.index_code}")
        if not has_reported_change and not has_both_closes:
            raise ValueError(f"行情缺少涨跌幅或起止收盘值：{item.scope}/{item.index_code}")
        try:
            start_date = parse_flexible_date(
                item.start_date,
                default_year=publication_date.year,
            )
            end_date = parse_flexible_date(
                item.end_date,
                default_year=publication_date.year,
            )
        except ValueError as exc:
            raise ValueError(f"行情日期格式无效：{item.scope}/{item.index_code}") from exc
        if start_date > end_date or (start_date == end_date and not has_reported_change):
            raise ValueError(f"行情起止日期无效：{item.scope}/{item.index_code}")
        if item.scope == "monday_a" and end_date != publication_date:
            raise ValueError(f"monday_a 必须使用出版日 {publication_date.isoformat()} 的收盘值")
        if item.scope != "monday_a" and end_date >= publication_date:
            raise ValueError(f"{item.scope} 必须使用出版日前一周的收盘值")

    ordered: dict[str, list[object]] = {}
    for scope, required_codes in REQUIRED_MARKET_CODES.items():
        missing = [code for code in required_codes if code not in grouped.get(scope, {})]
        if missing:
            raise ValueError(f"资本市场综述缺少 {scope} 必填指数：{', '.join(missing)}")
        ordered[scope] = [grouped[scope][code] for code in required_codes]

    month_day = f"{publication_date.month}月{publication_date.day}日"
    paragraphs = [
        _render_group("上周A股", ordered["weekly_a"]),
        _render_group(f"截至{month_day}收盘，A股", ordered["monday_a"]),
        _render_group("上周港股", ordered["weekly_hk"]),
        _render_group("上周美股", ordered["weekly_us"]),
    ]
    paragraphs.extend(context.summary.rstrip("。") + "。" for context in bundle.contexts)

    evidence_by_url: dict[str, dict[str, object]] = {}
    for evidence in [*bundle.series, *bundle.contexts]:
        record = evidence_by_url.setdefault(
            evidence.source_url,
            {"title": evidence.source_title, "excerpts": []},
        )
        record["excerpts"].append(evidence.evidence_excerpt)

    timestamp = retrieved_at or datetime.now().astimezone().isoformat()
    sources = [
        SourceRecord(
            source_id=_source_id(url),
            title=str(value["title"]),
            url=url,
            retrieved_at=timestamp,
            source_type="market_data",
            evidence_excerpts=list(value["excerpts"]),
            content_sha256=hashlib.sha256(
                "\n".join(value["excerpts"]).encode("utf-8")
            ).hexdigest(),
        )
        for url, value in evidence_by_url.items()
    ]
    item = WeeklyItem(
        item_id="market-capital-summary",
        section="市场观察",
        title="资本市场综述",
        body="\n".join(paragraphs),
        content_mode="market_fixed",
        source_ids=[record.source_id for record in sources],
        fixed_position=1,
    )
    return item, sources


def extract_requested_publication_date(text: str) -> date | None:
    patterns = (
        r"(?P<year>20\d{2})[-/.年](?P<month>\d{1,2})[-/.月](?P<day>\d{1,2})日?",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return date(int(match["year"]), int(match["month"]), int(match["day"]))
    return None
