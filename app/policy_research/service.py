from __future__ import annotations

from pathlib import Path

from app.policy_knowledge.materials import (
    POLICY_THEMES,
    build_source_text,
    infer_policy_intent,
    rank_policy_candidates,
)
from app.policy_knowledge.store import PolicyKnowledgeStore
from app.policy_research.models import PolicyCandidate, PolicyResearchResult
from app.policy_research.profiles import get_policy_research_profile

_MATERIAL_TYPE_KEYWORDS = (
    ("lawsuit_or_case", ("法院", "判决", "裁判", "侵权", "不正当竞争", "黑灰产", "案件", "诉讼")),
    ("event_activity", ("活动", "直播", "宣教", "宣传", "万里行", "论坛", "发布会", "小课堂")),
    ("award_or_recognition", ("获奖", "荣获", "大奖", "典型案例", "入选", "评选", "蝉联")),
)

_THEME_LABELS = {theme.id: theme.label for theme in POLICY_THEMES}


def research_policy_attachment(
    *,
    user_instruction: str,
    materials: list[dict[str, object]],
    db_path: str | Path,
    usage_profile: str,
    limit: int = 3,
) -> PolicyResearchResult:
    profile = get_policy_research_profile(usage_profile)
    source_text = build_source_text(user_instruction=user_instruction, materials=materials)
    material_type = classify_material_type(source_text)
    if material_type in profile.reject_material_types:
        return PolicyResearchResult(
            should_attach_policy=False,
            decision_reason="unsupported_material_type",
            matched_themes=[],
            retrieval_query="",
            confidence=0.9,
        )

    intent = infer_policy_intent(source_text)
    if not intent["needs_policy"]:
        return PolicyResearchResult(
            should_attach_policy=False,
            decision_reason="unsupported_theme",
            matched_themes=[],
            retrieval_query="",
            confidence=0.2,
        )

    query = str(intent.get("query") or "").strip()
    if not query:
        return PolicyResearchResult(
            should_attach_policy=False,
            decision_reason="unsupported_theme",
            matched_themes=[],
            retrieval_query="",
            confidence=0.2,
        )

    store = PolicyKnowledgeStore(db_path)
    candidates = store.search(query, limit=max(limit * 4, 8), category="policy_original")
    ranked = rank_policy_candidates(
        candidates=_attach_theme_context(candidates, intent),
        themes=[str(item) for item in list(intent.get("themes") or [])],
        keywords=[str(item) for item in list(intent.get("keywords") or [])],
        min_relevance=profile.min_relevance,
    )
    if not ranked:
        return PolicyResearchResult(
            should_attach_policy=False,
            decision_reason="no_qualified_policy",
            matched_themes=[str(item) for item in list(intent.get("themes") or [])],
            retrieval_query=query,
            confidence=0.35,
        )

    chosen = ranked[: max(limit, profile.max_primary + profile.max_alternatives)]
    primary_policy = _to_candidate(chosen[0]) if chosen and profile.max_primary else None
    alternative_policies = [_to_candidate(item) for item in chosen[1 : 1 + profile.max_alternatives]]
    return PolicyResearchResult(
        should_attach_policy=primary_policy is not None,
        decision_reason="qualified_local_policy" if primary_policy else "no_qualified_policy",
        matched_themes=[str(item) for item in list(intent.get("themes") or [])],
        retrieval_query=query,
        confidence=_estimate_confidence(primary_policy, alternative_policies),
        primary_policy=primary_policy,
        alternative_policies=alternative_policies,
    )


def classify_material_type(source_text: str) -> str:
    normalized = source_text.replace(" ", "")
    for material_type, keywords in _MATERIAL_TYPE_KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return material_type
    return "general"


def candidate_to_material(candidate: PolicyCandidate | dict[str, object]) -> dict[str, object]:
    if isinstance(candidate, PolicyCandidate):
        item = candidate.model_dump()
    else:
        item = dict(candidate)

    reason = str(item.get("selection_reason") or "与当前素材主题相关")
    snippet = str(item.get("snippet") or "").strip()
    return {
        "title": str(item.get("title") or ""),
        "text": f"相关性说明：{reason}\n政策摘录：{snippet}",
        "url": str(item.get("url") or ""),
        "source": "policy_knowledge",
        "category": str(item.get("category") or ""),
        "publish_date": str(item.get("publish_date") or ""),
        "matched_terms": [str(term) for term in list(item.get("matched_terms") or [])],
        "relevance_score": int(item.get("relevance_score") or 0),
        "selection_reason": reason,
    }


def _attach_theme_context(candidates: list[dict[str, object]], intent: dict[str, object]) -> list[dict[str, object]]:
    enriched: list[dict[str, object]] = []
    theme_ids = [str(item) for item in list(intent.get("themes") or [])]
    for item in candidates:
        copied = dict(item)
        copied["matched_themes"] = theme_ids
        enriched.append(copied)
    return enriched


def _to_candidate(item: dict[str, object]) -> PolicyCandidate:
    matched_themes = [str(theme_id) for theme_id in list(item.get("matched_themes") or [])]
    theme_labels = [_THEME_LABELS.get(theme_id, theme_id) for theme_id in matched_themes]
    reason_parts = []
    if theme_labels:
        reason_parts.append(f"命中政策主题：{'、'.join(theme_labels[:3])}")
    matched_terms = [str(term) for term in list(item.get("matched_terms") or [])]
    if matched_terms:
        reason_parts.append(f"匹配关键词：{'、'.join(matched_terms[:6])}")
    return PolicyCandidate(
        title=str(item.get("title") or ""),
        source=str(item.get("source") or ""),
        category=str(item.get("category") or ""),
        publish_date=str(item.get("publish_date") or ""),
        url=str(item.get("url") or ""),
        snippet=str(item.get("snippet") or item.get("text") or ""),
        matched_terms=matched_terms,
        relevance_score=int(item.get("relevance_score") or 0),
        selection_reason="；".join(reason_parts) or "与当前素材主题相关",
    )


def _estimate_confidence(
    primary_policy: PolicyCandidate | None,
    alternative_policies: list[PolicyCandidate],
) -> float:
    if primary_policy is None:
        return 0.0
    base = 0.72 if primary_policy.relevance_score >= 28 else 0.6
    if alternative_policies:
        base += 0.08
    return min(base, 0.95)
