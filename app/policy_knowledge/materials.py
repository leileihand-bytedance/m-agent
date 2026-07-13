from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from app.policy_knowledge.store import PolicyKnowledgeStore


@dataclass(frozen=True)
class PolicyTheme:
    id: str
    label: str
    terms: tuple[str, ...]
    query_terms: tuple[str, ...]


POLICY_THEMES: tuple[PolicyTheme, ...] = (
    PolicyTheme(
        id="foreign_trade",
        label="稳外贸和外贸金融支持",
        terms=("外贸", "外贸贷", "出口", "出口订单", "报关", "报关流水", "外汇营收", "信保", "跨境贸易"),
        query_terms=("稳外贸", "外贸企业", "融资支持", "贸易金融"),
    ),
    PolicyTheme(
        id="green_finance",
        label="绿色金融与绿色转型",
        terms=("绿色金融", "绿色信贷", "绿色转型", "双碳", "碳达峰", "碳中和", "漂绿"),
        query_terms=("绿色金融", "绿色信贷", "双碳", "绿色转型"),
    ),
    PolicyTheme(
        id="data_elements",
        label="数据要素与跨境数据流动",
        terms=("数据要素", "跨境数据", "数据验证", "数据流动", "数据跨境", "数字治理", "DDTP", "分布式数据传输"),
        query_terms=("数据要素", "跨境数据", "数据流动", "数字治理"),
    ),
    PolicyTheme(
        id="tech_innovation",
        label="科技创新和科技金融",
        terms=("科技金融", "科创", "科技创新", "硬科技", "专利", "科技初创通", "征信平台", "融资信用服务"),
        query_terms=("科技金融", "科技创新", "科创企业", "融资支持"),
    ),
    PolicyTheme(
        id="small_micro",
        label="小微企业金融服务",
        terms=("小微企业", "小微", "个体工商户", "银税互动", "首贷", "续贷", "信用贷款", "融资"),
        query_terms=("小微企业", "普惠金融", "金融服务", "融资"),
    ),
    PolicyTheme(
        id="inclusive_finance",
        label="普惠金融",
        terms=("普惠金融", "普惠信贷", "民营经济", "中小企业", "金融供给"),
        query_terms=("普惠金融", "民营经济", "中小企业", "金融服务"),
    ),
    PolicyTheme(
        id="digital_finance",
        label="数字金融",
        terms=("数字金融", "数字化", "线上", "移动端", "数据要素", "金融科技"),
        query_terms=("数字金融", "科技金融", "金融服务"),
    ),
    PolicyTheme(
        id="ai_finance",
        label="人工智能金融应用",
        terms=("人工智能", "AI", "大模型", "智能体", "算法", "模型治理"),
        query_terms=("人工智能", "银行业", "保险业", "金融服务"),
    ),
    PolicyTheme(
        id="consumer_protection",
        label="金融消费者权益保护",
        terms=("消费者权益", "金融消费者", "消费投诉", "适当性", "营销", "权益保护", "金融知识", "万里行", "宣教", "风险提示", "听障", "无障碍", "适老化"),
        query_terms=("消费者权益", "金融消费者", "金融产品网络营销"),
    ),
    PolicyTheme(
        id="risk_control",
        label="风险防控和强监管",
        terms=("风险防控", "严监管", "强监管", "合规", "反欺诈", "反洗钱", "数据安全", "黑灰产", "知识产权", "商标侵权", "不正当竞争"),
        query_terms=("风险防控", "严监管", "强监管", "黑灰产治理"),
    ),
    PolicyTheme(
        id="consumption",
        label="促进消费",
        terms=("促进消费", "服务消费", "扩大消费", "消费品以旧换新", "消费新增长点", "文旅消费"),
        query_terms=("促进消费", "服务消费", "扩大消费"),
    ),
    PolicyTheme(
        id="real_economy",
        label="服务实体经济",
        terms=("实体经济", "制造业", "产业链", "供应链", "科技创新", "营商环境"),
        query_terms=("实体经济", "制造业", "科技创新"),
    ),
    PolicyTheme(
        id="emerging_industries",
        label="战略性新兴产业和未来产业",
        terms=("战略性新兴产业", "未来产业", "新质生产力", "低空经济", "量子科技", "先进制造"),
        query_terms=("战略性新兴产业", "未来产业", "新质生产力"),
    ),
)


def build_policy_materials(
    *,
    user_instruction: str,
    materials: list[object],
    db_path: str | Path,
    limit: int = 3,
    min_relevance: int = 25,
) -> list[dict[str, object]]:
    source_text = build_source_text(user_instruction=user_instruction, materials=materials)
    intent = infer_policy_intent(source_text)
    if not intent["needs_policy"]:
        return []

    store = PolicyKnowledgeStore(db_path)
    query = str(intent["query"])
    candidates = store.search(query, limit=max(limit * 4, 8), category="policy_original")
    if len(candidates) < limit:
        seen = {(item.get("source"), item.get("doc_id")) for item in candidates}
        for item in store.search(query, limit=max(limit * 4, 8)):
            key = (item.get("source"), item.get("doc_id"))
            if key not in seen:
                candidates.append(item)
                seen.add(key)

    ranked = rank_policy_candidates(
        candidates=candidates,
        themes=list(intent["themes"]),
        keywords=list(intent["keywords"]),
        min_relevance=min_relevance,
    )
    return [format_policy_material(item, intent=intent) for item in ranked[:limit]]


def build_source_text(*, user_instruction: str, materials: list[object]) -> str:
    parts = [user_instruction.strip()]
    for item in materials:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        if title:
            parts.append(title)
        if text:
            parts.append(text[:1200])
    return "\n".join(part for part in parts if part)


def infer_policy_intent(source_text: str) -> dict[str, object]:
    normalized = _normalize_text(source_text)
    theme_hits: list[tuple[PolicyTheme, int, list[str]]] = []
    for theme in POLICY_THEMES:
        hits = [term for term in theme.terms if term and term in normalized]
        if hits:
            theme_hits.append((theme, sum(normalized.count(term) for term in hits), hits))

    theme_hits.sort(key=lambda item: item[1], reverse=True)
    selected = theme_hits[:3]
    keywords: list[str] = []
    for theme, _, hits in selected:
        keywords.extend(hits)
        keywords.extend(theme.query_terms)
    keywords.extend(_extract_named_keywords(normalized))
    keywords = _dedupe_terms(keywords)

    themes = [theme.id for theme, _, _ in selected]
    query_terms = _dedupe_terms(
        [
            term
            for theme, _, _ in selected
            for term in theme.query_terms
            if not _is_too_generic_query_term(term, keywords)
        ]
    )
    if len(query_terms) < 3:
        query_terms.extend(term for term in keywords if not _is_too_generic_query_term(term, keywords))
    query_terms = _dedupe_terms(query_terms)[:8]

    return {
        "needs_policy": bool(selected),
        "themes": themes,
        "theme_labels": [theme.label for theme, _, _ in selected],
        "keywords": keywords[:12],
        "query": " ".join(query_terms),
    }


def rank_policy_candidates(
    *,
    candidates: list[dict[str, object]],
    themes: list[str],
    keywords: list[str],
    min_relevance: int = 10,
) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    for item in candidates:
        score, matched_terms = score_policy_candidate(item=item, themes=themes, keywords=keywords)
        if score < min_relevance:
            continue
        enriched = dict(item)
        enriched["relevance_score"] = score
        enriched["matched_terms"] = matched_terms
        ranked.append(enriched)

    ranked.sort(
        key=lambda item: (
            int(item.get("relevance_score") or 0),
            1 if item.get("category") == "policy_original" else 0,
            str(item.get("publish_date") or ""),
        ),
        reverse=True,
    )
    return ranked


def score_policy_candidate(
    *,
    item: dict[str, object],
    themes: list[str],
    keywords: list[str],
) -> tuple[int, list[str]]:
    title = str(item.get("title") or "")
    text = str(item.get("text") or "")
    haystack = f"{title}\n{text[:1600]}"
    matched_terms = [term for term in keywords if term and term in haystack]
    if not matched_terms:
        return 0, []

    score = 0
    for term in matched_terms:
        score += title.count(term) * 8
        score += text[:1600].count(term) * 3
    if item.get("category") == "policy_original":
        score += 8
    if item.get("source") == "govcn" and any(theme in themes for theme in ("consumption", "real_economy", "emerging_industries")):
        score += 6
    if item.get("source") == "nfra" and any(
        theme in themes
        for theme in (
            "foreign_trade",
            "green_finance",
            "data_elements",
            "tech_innovation",
            "small_micro",
            "inclusive_finance",
            "digital_finance",
            "ai_finance",
            "consumer_protection",
            "risk_control",
        )
    ):
        score += 6
    return score, matched_terms[:8]


def format_policy_material(item: dict[str, object], *, intent: dict[str, object]) -> dict[str, object]:
    matched_terms = [str(term) for term in item.get("matched_terms") or []]
    theme_labels = [str(label) for label in intent.get("theme_labels") or []]
    reason_parts = []
    if theme_labels:
        reason_parts.append(f"命中政策主题：{'、'.join(theme_labels[:3])}")
    if matched_terms:
        reason_parts.append(f"匹配关键词：{'、'.join(matched_terms[:6])}")
    reason = "；".join(reason_parts) or "与用户材料主题相关"
    snippet = str(item.get("snippet") or item.get("text") or "").strip()
    return {
        "url": str(item.get("url") or ""),
        "title": str(item.get("title") or ""),
        "text": f"相关性说明：{reason}\n政策摘录：{snippet}",
        "source": "policy_knowledge",
        "category": str(item.get("category") or ""),
        "publish_date": str(item.get("publish_date") or ""),
        "policy_query": str(intent.get("query") or ""),
        "policy_themes": list(intent.get("theme_labels") or []),
        "matched_terms": matched_terms,
        "relevance_score": int(item.get("relevance_score") or 0),
    }


def _extract_named_keywords(text: str) -> list[str]:
    candidates = [
        "微众银行",
        "银行业",
        "保险业",
        "金融机构",
        "商业银行",
        "监管总局",
        "人民银行",
        "国务院",
        "广东",
        "深圳",
    ]
    return [term for term in candidates if term in text]


def _is_too_generic_query_term(term: str, keywords: list[str]) -> bool:
    if term == "消费" and any(keyword in keywords for keyword in ("促进消费", "扩大消费", "服务消费", "消费品以旧换新")):
        return True
    if term == "金融服务" and any(keyword in keywords for keyword in ("小微企业", "普惠金融", "科技金融", "数字金融")):
        return False
    return False


def _dedupe_terms(terms: list[str]) -> list[str]:
    cleaned = []
    for term in terms:
        value = term.strip()
        if not value:
            continue
        if re.fullmatch(r"https?://\\S+", value):
            continue
        cleaned.append(value)
    return list(dict.fromkeys(cleaned))


def _normalize_text(text: str) -> str:
    return " ".join(text.replace("\u3000", " ").split())
