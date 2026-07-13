from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from app.bank_knowledge.ingest import THEME_TERMS
from app.bank_knowledge.store import BankKnowledgeStore


@dataclass(frozen=True)
class BankTheme:
    id: str
    label: str
    query_terms: tuple[str, ...]


BANK_THEMES: tuple[BankTheme, ...] = (
    BankTheme("small_micro", "小微企业金融服务", ("微业贷", "小微企业", "普惠金融", "融资服务")),
    BankTheme("inclusive_finance", "普惠金融", ("普惠金融", "微众银行", "金融服务")),
    BankTheme("digital_finance", "数字金融", ("数字银行", "数字化", "金融科技")),
    BankTheme("tech_finance", "科技金融", ("科技金融", "科创企业", "高新技术企业")),
    BankTheme("ai_finance", "人工智能金融应用", ("人工智能", "大模型", "智能体", "数字员工")),
    BankTheme("foreign_trade", "稳外贸金融服务", ("微贸贷", "外贸", "稳外贸")),
    BankTheme("consumption", "促进消费", ("国补商户", "促消费", "消费品以旧换新")),
    BankTheme("consumer_protection", "消费者权益保护", ("消费者权益", "消保", "金融消费者")),
    BankTheme("anti_fraud", "反诈风控", ("反诈", "电信网络诈骗", "账户风险")),
    BankTheme("accessibility", "适老和无障碍服务", ("无障碍", "适老", "听障", "视障")),
    BankTheme("green_finance", "绿色金融", ("绿色金融", "绿色贷款", "绿色低碳")),
)


def build_bank_materials(
    *,
    user_instruction: str,
    materials: list[object],
    db_path: str | Path,
    limit: int = 3,
    min_relevance: int = 12,
) -> list[dict[str, object]]:
    source_text = build_source_text(user_instruction=user_instruction, materials=materials)
    intent = infer_bank_intent(source_text)
    if not intent["needs_bank_knowledge"]:
        return []

    store = BankKnowledgeStore(db_path)
    candidates = store.search(
        str(intent["query"]),
        limit=max(limit * 4, 8),
        themes=list(intent["themes"]),
    )
    ranked = rank_bank_candidates(
        candidates=candidates,
        themes=list(intent["themes"]),
        keywords=list(intent["keywords"]),
        min_relevance=min_relevance,
    )
    return [format_bank_material(item, intent=intent) for item in ranked[:limit]]


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


def infer_bank_intent(source_text: str) -> dict[str, object]:
    normalized = " ".join(source_text.replace("\u3000", " ").split())
    hits: list[tuple[BankTheme, int, list[str]]] = []
    for theme in BANK_THEMES:
        terms = THEME_TERMS.get(theme.id, ())
        matched = [term for term in terms if term in normalized]
        matched.extend(term for term in theme.query_terms if term in normalized and term not in matched)
        if matched:
            hits.append((theme, sum(normalized.count(term) for term in matched), matched))

    has_bank_signal = any(term in normalized for term in ("微众银行", "微业贷", "微粒贷", "微贸贷", "深圳前海微众银行"))
    if has_bank_signal and not hits:
        profile_theme = BankTheme("profile", "微众银行基础背景", ("微众银行", "数字银行", "普惠大众"))
        hits.append((profile_theme, 1, ["微众银行"]))

    hits.sort(key=lambda item: item[1], reverse=True)
    selected = hits[:3]
    keywords: list[str] = []
    for theme, _, matched in selected:
        keywords.extend(matched)
        keywords.extend(theme.query_terms)
    if has_bank_signal:
        keywords.append("微众银行")
    keywords = _dedupe_terms(keywords)
    query = " ".join(keywords[:10])

    return {
        "needs_bank_knowledge": bool(selected),
        "themes": [theme.id for theme, _, _ in selected],
        "theme_labels": [theme.label for theme, _, _ in selected],
        "keywords": keywords[:12],
        "query": query,
    }


def rank_bank_candidates(
    *,
    candidates: list[dict[str, object]],
    themes: list[str],
    keywords: list[str],
    min_relevance: int,
) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    for item in candidates:
        score, matched_terms = score_bank_candidate(item=item, themes=themes, keywords=keywords)
        if score < min_relevance:
            continue
        enriched = dict(item)
        enriched["relevance_score"] = score
        enriched["matched_terms"] = matched_terms
        ranked.append(enriched)
    ranked.sort(
        key=lambda item: (
            int(item.get("relevance_score") or 0),
            1 if item.get("entity_type") in {"product", "capability", "metric"} else 0,
            str(item.get("source_file") or ""),
        ),
        reverse=True,
    )
    return ranked


def score_bank_candidate(
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
    item_themes = set(str(theme) for theme in item.get("themes") or [])
    score += 6 * len(item_themes & set(themes))
    if item.get("entity_type") in {"product", "capability", "metric", "standard_expression"}:
        score += 6
    return score, matched_terms[:8]


def format_bank_material(item: dict[str, object], *, intent: dict[str, object]) -> dict[str, object]:
    matched_terms = [str(term) for term in item.get("matched_terms") or []]
    theme_labels = [str(label) for label in intent.get("theme_labels") or []]
    reason_parts = []
    if theme_labels:
        reason_parts.append(f"命中微众主题：{'、'.join(theme_labels[:3])}")
    if matched_terms:
        reason_parts.append(f"匹配关键词：{'、'.join(matched_terms[:6])}")
    reason = "；".join(reason_parts) or "与用户材料主题相关"
    snippet = str(item.get("snippet") or item.get("text") or "").strip()
    source_file = str(item.get("source_file") or "")
    page = str(item.get("source_page") or "")
    source_note = f"来源文件：{source_file}" + (f"；页码：{page}" if page else "")
    return {
        "url": f"bank://{item.get('entry_id')}",
        "title": str(item.get("title") or ""),
        "text": f"相关性说明：{reason}\n{source_note}\n微众银行素材摘录：{snippet}",
        "source": "bank_knowledge",
        "category": ",".join(str(theme) for theme in item.get("themes") or []),
        "publish_date": "",
        "source_file": source_file,
        "source_page": page,
        "matched_terms": matched_terms,
        "relevance_score": int(item.get("relevance_score") or 0),
    }


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
