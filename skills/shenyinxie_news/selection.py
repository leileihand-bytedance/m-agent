from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import yaml

from skills.shenyinxie_news.schema import ArticleAssessment, NewsCandidate


EXCERPT_EDITOR_NOTE = "说明：本文根据原报道中微众银行相关内容摘编。"


_SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def calculate_news_period(
    today: date | None = None,
    instruction: str = "",
) -> tuple[date, date]:
    """根据执行日期计算本期新闻发布日期范围。

    规则：
    - 每月 1 日：上月 16 日至上月最后一日
    - 每月 2-15 日：当月 1 日至执行日
    - 每月 16-28 日：当月 1 日至 15 日
    - 每月 29 日至月末：当月 16 日至执行日
    - 用户明确指定“某月上半月/下半月”时优先采用指定范围
    """
    if today is None:
        today = datetime.now(_SHANGHAI_TZ).date()

    explicit_period = extract_explicit_half_month(instruction, today)
    if explicit_period is not None:
        return explicit_period

    day = today.day

    if day == 1:
        # 上月 16 日至上月最后一日
        first_of_current = today.replace(day=1)
        period_end = first_of_current - timedelta(days=1)
        period_start = period_end.replace(day=16)
    elif 2 <= day <= 15:
        # 当月 1 日至执行日
        period_start = today.replace(day=1)
        period_end = today
    elif 16 <= day <= 28:
        # 当月 1 日至 15 日
        period_start = today.replace(day=1)
        period_end = today.replace(day=15)
    else:
        # 当月 16 日至执行日
        period_start = today.replace(day=16)
        period_end = today

    return period_start, period_end


def extract_explicit_half_month(
    instruction: str,
    today: date | None = None,
) -> tuple[date, date] | None:
    """从“生成7月上半月”等指令中提取明确的半月范围。"""
    if today is None:
        today = datetime.now(_SHANGHAI_TZ).date()
    normalized = instruction.strip().replace(" ", "")
    half_match = re.search(r"(?P<half>上半月|下半月)", normalized)
    date_match = re.search(
        r"(?:(?P<year>\d{4})年)?(?P<month>\d{1,2})月",
        normalized,
    )
    if half_match is None or date_match is None:
        return None

    month = int(date_match.group("month"))
    if not 1 <= month <= 12:
        return None
    year = int(date_match.group("year") or today.year)
    if date_match.group("year") is None and month > today.month:
        year -= 1

    first_of_month = date(year, month, 1)
    if half_match.group("half") == "上半月":
        return first_of_month, first_of_month.replace(day=15)

    if month == 12:
        first_of_next_month = date(year + 1, 1, 1)
    else:
        first_of_next_month = date(year, month + 1, 1)
    month_end = first_of_next_month - timedelta(days=1)
    if year == today.year and month == today.month and today.day >= 16:
        month_end = today
    return first_of_month.replace(day=16), month_end


def calculate_issue_number(today: date | None = None) -> str:
    """按每年 24 期（每月两期）计算期次。"""
    if today is None:
        today = datetime.now(_SHANGHAI_TZ).date()

    half = 1 if today.day <= 15 else 2
    issue = (today.month - 1) * 2 + half
    return f"{today.year}-{issue:02d}"


class MediaSource:
    def __init__(self, name: str, category: str, domains: list[str], tier: int):
        self.name = name
        self.category = category
        self.domains = domains
        self.tier = tier

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "MediaSource":
        return cls(
            name=str(data.get("name", "")),
            category=str(data.get("category", "")),
            domains=[str(d).strip().lower().lstrip(".") for d in data.get("domains", []) if str(d).strip()],
            tier=int(data.get("tier", 99)),
        )


class MediaWhitelist:
    """权威媒体白名单，支持域名精确匹配和子域名匹配。"""

    def __init__(self, sources: list[MediaSource]):
        self.sources = sources
        # 按 tier 升序排列，tier 相同按名称排序，确保高权威来源优先
        self.sources.sort(key=lambda s: (s.tier, s.name))
        self._domain_to_source: dict[str, MediaSource] = {}
        for source in sources:
            for domain in source.domains:
                existing = self._domain_to_source.get(domain)
                if existing is None or source.tier < existing.tier:
                    self._domain_to_source[domain] = source

    @classmethod
    def from_yaml(cls, path: Path | None = None) -> "MediaWhitelist":
        if path is None:
            path = Path(__file__).resolve().parent / "media_sources.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        sources = [MediaSource.from_dict(item) for item in data.get("sources", [])]
        return cls(sources)

    def _hostname(self, url: str) -> str:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().lstrip("www.")
        return hostname

    def is_allowed(self, url: str) -> bool:
        return self.media_info(url) is not None

    def media_info(self, url: str) -> dict[str, object] | None:
        hostname = self._hostname(url)
        if not hostname:
            return None

        # 精确匹配
        source = self._domain_to_source.get(hostname)
        if source is not None:
            return {
                "name": source.name,
                "category": source.category,
                "tier": source.tier,
            }

        # 子域名匹配：hostname 以 .domain 结尾
        for domain, source in self._domain_to_source.items():
            if hostname.endswith(f".{domain}"):
                return {
                    "name": source.name,
                    "category": source.category,
                    "tier": source.tier,
                }

        return None


def extract_publish_date(page: dict[str, str]) -> date | None:
    """从 web_reader 返回的页面字典中解析发布日期。"""
    date_str = str(page.get("publish_date", "") or "").strip()
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None


def is_date_in_period(publish_date: date, period_start: date, period_end: date) -> bool:
    return period_start <= publish_date <= period_end


def is_body_readable(body: str, min_length: int = 120) -> bool:
    """正文是否完整可读（非仅标题/摘要/登录提示）。"""
    text = body.strip()
    if len(text) < min_length:
        return False
    # 排除常见登录/付费提示
    login_hints = ("登录后查看", "订阅后阅读", "请登录", "付费阅读", "会员专享")
    if any(hint in text for hint in login_hints) and len(text) < 300:
        return False
    return True


def hard_gate(candidate: NewsCandidate, period_start: date, period_end: date, whitelist: MediaWhitelist) -> tuple[bool, str]:
    """候选报道硬性准入检查。

    返回 (是否通过, 失败原因)。
    """
    if not whitelist.is_allowed(candidate.url):
        return False, "域名不在权威媒体白名单"

    publish_date = extract_publish_date(candidate.model_dump())
    if publish_date is None:
        return False, "无法从原文提取发布日期"

    if not is_date_in_period(publish_date, period_start, period_end):
        return False, f"发布日期 {publish_date.isoformat()} 不在本期范围"

    if not is_body_readable(candidate.body):
        return False, "正文不完整或不可读"

    return True, ""


def is_likely_core_subject(title: str, body: str) -> bool:
    """只做保守的规则预判，最终报送价值必须由结构化模型判断。"""
    full_text = f"{title}\n{body}".lower()
    keywords = ("微众银行", "深圳前海微众银行", "webank", "微粒贷", "微业贷", "微众")
    if not any(kw in full_text for kw in keywords):
        return False

    roundup_hints = ("等", "多家", "民营银行", "银行业", "行业观察", "行业综述", "盘点")
    if any(hint in title for hint in roundup_hints):
        return False

    mentions = len(re.findall(r"深圳前海微众银行|微众银行|webank|微粒贷|微业贷", full_text, re.I))
    return "微众" in title.lower() and mentions >= 3


def validate_excerpt_paragraphs(source_body: str, paragraphs: list[str]) -> str | None:
    """校验摘编段落逐字存在、顺序一致且足以独立成篇。"""
    source = source_body.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = [paragraph.strip() for paragraph in paragraphs if paragraph.strip()]
    if not cleaned or len(cleaned) > 8:
        return None
    if any(len(paragraph) < 20 for paragraph in cleaned):
        return None

    cursor = 0
    for paragraph in cleaned:
        position = source.find(paragraph, cursor)
        if position < 0:
            return None
        cursor = position + len(paragraph)

    combined = "\n\n".join(cleaned)
    if "微众" not in combined:
        return None

    if len(cleaned) == 1:
        fact_markers = len(re.findall(r"\d|年|月|日|亿元|万元|%|成果|服务|发布|实施", combined))
        if len(combined) < 120 or fact_markers < 2:
            return None

    return combined


def apply_editorial_assessment(
    candidate: NewsCandidate,
    assessment: ArticleAssessment,
) -> NewsCandidate | None:
    """把模型决定转成受代码约束的全文或摘编候选。"""
    candidate.source_title = candidate.source_title or candidate.title
    candidate.select_reason = assessment.reason.strip()
    candidate.achievement_types = list(dict.fromkeys(assessment.achievement_types))

    if not assessment.is_positive_achievement:
        return None

    if assessment.decision == "full_text" and assessment.subject_strength == "primary":
        candidate.content_mode = "full_text"
        candidate.editor_note = ""
        candidate.is_core_subject = True
        return candidate

    if assessment.decision != "extract" or assessment.subject_strength != "substantial":
        return None

    title = assessment.suggested_title.strip()
    if not title or "微众" not in title or len(title) > 60:
        return None
    excerpt = validate_excerpt_paragraphs(candidate.body, assessment.excerpt_paragraphs)
    if excerpt is None:
        return None

    candidate.title = title
    candidate.body = excerpt
    candidate.content_mode = "extract"
    candidate.editor_note = EXCERPT_EDITOR_NOTE
    candidate.is_core_subject = True
    return candidate


def is_likely_repost(body: str) -> bool:
    """确定性规则识别简单转载。"""
    indicators = (
        "转载自",
        "本文转自",
        "来源：",
        "转载自：",
        "原文链接",
        "稿件来源",
        "编辑：",
    )
    return any(ind in body for ind in indicators)


def apply_rule_relevance(candidates: list[NewsCandidate]) -> list[NewsCandidate]:
    """用确定性规则填充核心主体和转载判断。"""
    for candidate in candidates:
        candidate.is_core_subject = is_likely_core_subject(candidate.title, candidate.body)
        candidate.is_repost = is_likely_repost(candidate.body)
    return candidates


def score_candidates_rule_based(candidates: list[NewsCandidate]) -> list[NewsCandidate]:
    """基于规则的评分。"""
    for candidate in candidates:
        # 权威性：tier 越低越好，映射到 0-10
        candidate.authority_score = max(0.0, 10.0 - (candidate.media_tier or 99))

        # 相关度：标题命中微众 + 正文前部命中
        if candidate.is_core_subject:
            candidate.relevance_score = 9.0 if "微众" in candidate.title.lower() else 7.0
        else:
            candidate.relevance_score = 3.0

        # 完整度：正文长度
        body_len = len(candidate.body)
        if body_len >= 800:
            candidate.completeness_score = 9.0
        elif body_len >= 400:
            candidate.completeness_score = 7.0
        else:
            candidate.completeness_score = 5.0

        # 原创性
        candidate.originality_score = 8.0 if not candidate.is_repost else 3.0

        # 新闻价值：简单用数字、时间、地点等可核验事实数量
        facts = sum(1 for token in ("年", "月", "日", "%", "亿元", "万元", "人", "家") if token in candidate.body)
        candidate.news_value_score = min(10.0, facts / 3.0)

        # 加权总分
        candidate.total_score = (
            candidate.relevance_score * 0.35
            + candidate.authority_score * 0.25
            + candidate.completeness_score * 0.15
            + candidate.originality_score * 0.10
            + candidate.news_value_score * 0.15
        )
    return candidates


def filter_core_subject(candidates: list[NewsCandidate]) -> list[NewsCandidate]:
    """过滤掉微众银行非核心主体的候选。"""
    return [c for c in candidates if c.is_core_subject]


def select_top_candidates(candidates: list[NewsCandidate], target: int = 3) -> list[NewsCandidate]:
    """按总分排序，优先选择 target 篇，同时检查题材差异。"""
    if not candidates:
        return []

    # 按总分降序
    sorted_candidates = sorted(candidates, key=lambda c: c.total_score, reverse=True)

    selected: list[NewsCandidate] = []
    for candidate in sorted_candidates:
        if len(selected) >= target:
            break
        # 简单题材差异：已选报道中若有标题相似度过高，则跳过
        too_similar = False
        for existing in selected:
            if _text_similarity(candidate.title, existing.title) >= 0.7:
                too_similar = True
                break
        if not too_similar:
            selected.append(candidate)

    return selected


def finalize_selected_articles(candidates: list[NewsCandidate]) -> list[dict[str, str]]:
    """将候选转为最终输出结构。"""
    articles: list[dict[str, str]] = []
    for candidate in candidates:
        articles.append(
            {
                "title": candidate.title,
                "media_name": candidate.media_name or candidate.site,
                "publish_date": candidate.publish_date,
                "body": candidate.body,
                "original_url": candidate.canonical_url or candidate.url,
            }
        )
    return articles



def _publication_period_hint(period_start: date, period_end: date) -> str:
    return (
        f"{period_start.year}年{period_start.month}月{period_start.day}日"
        f"至{period_end.year}年{period_end.month}月{period_end.day}日"
    )


def generate_primary_search_queries(period_start: date, period_end: date) -> list[str]:
    """生成首轮权威媒体检索词，并明确要求按发布日期限定区间。"""
    date_hint = _publication_period_hint(period_start, period_end)
    return [
        f"微众银行 科技创新助推数字化金融普惠发展 人民日报 {date_hint}",
        f"微众银行 党建 引领 金融高质量发展 {date_hint}",
        f"微众银行 {date_hint}",
        f"深圳前海微众银行 {date_hint}",
        f"微众银行 正面新闻 成果 {date_hint}",
        f"微众银行 site:gov.cn {date_hint}",
        f"微众银行 人民网 新华网 {date_hint}",
        f"微众银行 深圳特区报 深圳商报 {date_hint}",
    ]


def generate_expanded_search_queries(period_start: date, period_end: date) -> list[str]:
    """首轮信源不足时，检索已核验的行业媒体和广东主流媒体。"""
    date_hint = _publication_period_hint(period_start, period_end)
    return [
        f"微众银行 央广网 中国金融新闻网 电子银行网 {date_hint}",
        f"微众银行 南方日报 南方+ 南方网 {date_hint}",
        f"微众银行 羊城晚报 金羊网 {date_hint}",
    ]


def generate_search_queries(period_start: date, period_end: date) -> list[str]:
    """兼容调用方：返回首轮与扩展轮的全部检索词。"""
    return generate_primary_search_queries(period_start, period_end) + generate_expanded_search_queries(
        period_start, period_end
    )


def normalize_url(url: str) -> str:
    """URL 规范化：去除尾部斜杠、锚点和常见跟踪参数。"""
    parsed = urlparse(url)
    # 去除锚点
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    # 保留部分查询参数？搜索摘要通常不需要，先全部去除以简化去重
    return normalized


def dedupe_by_url(candidates: list[NewsCandidate]) -> list[NewsCandidate]:
    """根据规范化 URL 去重，保留先出现的。"""
    seen: set[str] = set()
    result: list[NewsCandidate] = []
    for candidate in candidates:
        key = normalize_url(candidate.canonical_url or candidate.url)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _text_similarity(a: str, b: str) -> float:
    """简单文本相似度（Jaccard）。"""
    set_a = set(a)
    set_b = set(b)
    if not set_a and not set_b:
        return 1.0
    intersection = set_a & set_b
    union = set_a | set_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


def strip_trailing_media_title_suffix(title: str) -> str:
    """移除网页标题末尾由媒体站点自动追加的媒体名。"""
    normalized = title.strip()
    for separator in (" - ", "-", "—", "_", "|"):
        head, found, tail = normalized.rpartition(separator)
        clean_tail = tail.strip()
        if (
            found
            and head.strip()
            and len(clean_tail) <= 20
            and clean_tail.endswith(("网", "报", "报道", "新闻", "客户端", "日报", "时报"))
        ):
            normalized = head.strip()
            break
    return normalized


def _normalize_article_title(title: str) -> str:
    """移除媒体站点追加的标题尾缀，再统一空白和标点。"""
    normalized = strip_trailing_media_title_suffix(title)
    return re.sub(r"[\W_]+", "", normalized.lower())


def dedupe_same_article(candidates: list[NewsCandidate], similarity_threshold: float = 0.85) -> list[NewsCandidate]:
    """同稿转载去重：标题/正文高度相似且发布时间接近视为同一篇。

    保留权威性更高、正文更完整的一篇。
    """
    if not candidates:
        return []

    # 按（tier 升序, 正文长度降序）排序，确保最优来源在前
    sorted_candidates = sorted(
        candidates,
        key=lambda c: (c.media_tier or 99, -len(c.body)),
    )

    kept: list[NewsCandidate] = []
    for candidate in sorted_candidates:
        is_duplicate = False
        for existing in kept:
            title_sim = _text_similarity(
                _normalize_article_title(candidate.title),
                _normalize_article_title(existing.title),
            )
            body_sim = _text_similarity(candidate.body[:300], existing.body[:300])
            if title_sim >= similarity_threshold or body_sim >= similarity_threshold:
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(candidate)

    # 恢复原始顺序
    kept_urls = {c.url for c in kept}
    return [c for c in candidates if c.url in kept_urls]
