from __future__ import annotations

from dataclasses import dataclass

from app.policy_research import candidate_to_material
from app.platform.tools import ToolGateway, ToolNotAllowedError


@dataclass(frozen=True)
class DirectReportPolicyResearch:
    theme_id: str | None
    theme_label: str | None
    use_policy: bool
    reason: str
    selected_policy: dict[str, object] | None
    lead_guidance: str
    bridge_guidance: str
    closing_guidance: str


_THEME_PROFILES: tuple[dict[str, object], ...] = (
    {
        "id": "small_micro",
        "label": "小微金融",
        "keywords": ("小微企业", "小微", "普惠金融", "民营经济", "中小企业", "微业贷", "融资服务", "信用贷款"),
        "queries": ("小微企业 金融服务", "普惠金融 小微企业 融资", "民营经济 中小企业 融资支持"),
        "required_terms": ("小微企业", "小微", "普惠金融", "中小企业", "民营经济"),
        "strong_required_terms": ("小微企业", "普惠金融", "中小企业", "民营经济"),
        "preferred_terms": ("普惠金融", "小微企业金融服务", "融资可得性", "金融服务质效"),
        "lead_guidance": "如需政策背景，可先用1句点出提升小微企业金融服务质效、增强融资可得性的政策导向，再迅速转入微众银行响应。",
        "closing_guidance": "结尾可落到持续提升小微企业融资可得性、服务实体经济和普惠金融高质量发展。",
    },
    {
        "id": "tech_innovation",
        "label": "科技创新",
        "keywords": ("科技创新", "科技金融", "科创企业", "科技型企业", "专精特新", "新质生产力", "创新链", "研发"),
        "queries": ("科技金融 科技创新", "科技型企业 融资 支持", "新质生产力 科创企业 金融服务"),
        "required_terms": ("科技金融", "科技创新", "科创企业", "科技型企业", "专精特新", "新质生产力"),
        "strong_required_terms": ("科技金融", "科创企业", "科技型企业", "专精特新"),
        "preferred_terms": ("科技金融", "科技型企业", "科创企业", "专精特新"),
        "reject_title_terms": ("批复", "开发区", "试验区", "总体方案", "行动计划"),
        "lead_guidance": "如需政策背景，可先用1句点出金融支持科技创新、服务科技型企业发展的政策导向，再转入微众银行具体做法。",
        "closing_guidance": "结尾可落到持续提升科技企业金融服务能力、服务科技创新和高质量发展。",
    },
)

_GENERIC_REJECT_TERMS = (
    "会见",
    "答记者问",
    "新闻发布",
    "发布会",
    "情况通报",
)

_SHARED_THEME_LABELS = {
    "small_micro": "小微金融",
    "tech_innovation": "科技创新",
    "foreign_trade": "稳外贸金融服务",
    "green_finance": "绿色金融",
    "inclusive_finance": "普惠金融",
    "digital_finance": "数字金融",
    "ai_finance": "人工智能金融应用",
    "consumer_protection": "消费者权益保护",
    "risk_control": "风险防控",
}


def research_direct_report_policy(
    *,
    instruction: str,
    materials: list[object],
    tools: ToolGateway,
) -> DirectReportPolicyResearch:
    shared_result = _research_via_shared_tool(instruction=instruction, materials=materials, tools=tools)
    if shared_result is not None:
        return shared_result

    source_text = _build_source_text(instruction=instruction, materials=materials)
    profile = _detect_theme(source_text)
    bridge_guidance = "如采用政策背景开头，下一句要明确用“在此背景下，微众银行……”或“微众银行积极响应相关部署……”承接。"
    if profile is None:
        return DirectReportPolicyResearch(
            theme_id=None,
            theme_label=None,
            use_policy=False,
            reason="unsupported_theme",
            selected_policy=None,
            lead_guidance="本稿直入主题，不挂具体政策背景。",
            bridge_guidance=bridge_guidance,
            closing_guidance="结尾直接落到微众银行下一步安排和与主题一致的更高层方向即可。",
        )

    candidates = _collect_candidates(profile=profile, tools=tools)
    ranked = [
        (score_policy_candidate(item=item, profile=profile), item)
        for item in candidates
    ]
    ranked = [item for item in ranked if item[0] > 0]
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return DirectReportPolicyResearch(
            theme_id=str(profile["id"]),
            theme_label=str(profile["label"]),
            use_policy=False,
            reason="no_qualified_policy",
            selected_policy=None,
            lead_guidance="本稿直入主题，不挂具体政策背景。",
            bridge_guidance=bridge_guidance,
            closing_guidance=str(profile["closing_guidance"]),
        )

    best = ranked[0][1]
    selected_policy = _format_selected_policy(best=best, profile=profile)
    return DirectReportPolicyResearch(
        theme_id=str(profile["id"]),
        theme_label=str(profile["label"]),
        use_policy=True,
        reason="qualified_local_policy",
        selected_policy=selected_policy,
        lead_guidance=str(profile["lead_guidance"]),
        bridge_guidance=bridge_guidance,
        closing_guidance=str(profile["closing_guidance"]),
    )


def _research_via_shared_tool(
    *,
    instruction: str,
    materials: list[object],
    tools: ToolGateway,
) -> DirectReportPolicyResearch | None:
    try:
        result = tools.call(
            "policy_research",
            user_instruction=instruction,
            materials=materials,
            usage_profile="direct_report",
            limit=2,
        )
    except (ToolNotAllowedError, KeyError):
        return None

    if not isinstance(result, dict):
        return None

    matched_themes = [str(item) for item in list(result.get("matched_themes") or [])]
    theme_id = matched_themes[0] if matched_themes else None
    selected_policy = result.get("primary_policy")
    use_policy = bool(result.get("should_attach_policy")) and isinstance(selected_policy, dict)
    lead_guidance, closing_guidance = _guidance_for_theme(theme_id)
    bridge_guidance = "如采用政策背景开头，下一句要明确用“在此背景下，微众银行……”或“微众银行积极响应相关部署……”承接。"
    return DirectReportPolicyResearch(
        theme_id=theme_id,
        theme_label=_theme_label_for(theme_id),
        use_policy=use_policy,
        reason=str(result.get("decision_reason") or ""),
        selected_policy=candidate_to_material(selected_policy) if use_policy else None,
        lead_guidance=lead_guidance,
        bridge_guidance=bridge_guidance,
        closing_guidance=closing_guidance,
    )


def score_policy_candidate(*, item: dict[str, object], profile: dict[str, object]) -> int:
    title = str(item.get("title") or "")
    snippet = str(item.get("snippet") or item.get("text") or "")
    haystack = f"{title}\n{snippet}"
    if any(term in haystack for term in _GENERIC_REJECT_TERMS):
        return 0

    required_terms = tuple(str(term) for term in profile["required_terms"])
    if not any(term in haystack for term in required_terms):
        return 0

    strong_required_terms = tuple(str(term) for term in profile.get("strong_required_terms", ()))
    if strong_required_terms and not any(term in haystack for term in strong_required_terms):
        return 0

    reject_title_terms = tuple(str(term) for term in profile.get("reject_title_terms", ()))
    if reject_title_terms and any(term in title for term in reject_title_terms):
        return 0

    score = 0
    for term in required_terms:
        score += title.count(term) * 8
        score += snippet.count(term) * 3
    for term in strong_required_terms:
        score += title.count(term) * 10
        score += snippet.count(term) * 4
    for term in tuple(str(item) for item in profile.get("preferred_terms", ())):
        score += title.count(term) * 6
        score += snippet.count(term) * 2
    for keyword in tuple(str(term) for term in profile["keywords"]):
        score += title.count(keyword) * 4
        score += snippet.count(keyword) * 2
    if str(item.get("category") or "") == "policy_original":
        score += 10
    if str(item.get("source") or "") == "nfra":
        score += 6
    if str(item.get("source") or "") == "govcn":
        score += 4
    return score


def _build_source_text(*, instruction: str, materials: list[object]) -> str:
    parts = [instruction.strip()]
    for item in materials:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        if title:
            parts.append(title)
        if text:
            parts.append(text[:1600])
    return "\n".join(part for part in parts if part)


def _detect_theme(source_text: str) -> dict[str, object] | None:
    scored: list[tuple[int, dict[str, object]]] = []
    for profile in _THEME_PROFILES:
        score = sum(source_text.count(keyword) for keyword in tuple(str(term) for term in profile["keywords"]))
        if score:
            scored.append((score, profile))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored else None


def _collect_candidates(*, profile: dict[str, object], tools: ToolGateway) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for query in tuple(str(item) for item in profile["queries"]):
        try:
            found = tools.call("policy_search", query, limit=10, category="policy_original")
        except (ToolNotAllowedError, KeyError):
            return []
        for item in found if isinstance(found, list) else []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("source") or ""), str(item.get("doc_id") or item.get("url") or ""))
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
    return results


def _format_selected_policy(*, best: dict[str, object], profile: dict[str, object]) -> dict[str, object]:
    matched_terms = [
        term
        for term in tuple(str(item) for item in profile["required_terms"])
        if term in f"{best.get('title', '')}\n{best.get('snippet', '') or best.get('text', '')}"
    ]
    snippet = str(best.get("snippet") or best.get("text") or "").strip()
    reason = f"命中主题：{profile['label']}；匹配关键词：{'、'.join(matched_terms[:4]) or '主题高度相关'}"
    return {
        "title": str(best.get("title") or ""),
        "text": f"相关性说明：{reason}\n政策摘录：{snippet}",
        "url": str(best.get("url") or ""),
        "source": "policy_knowledge",
        "category": str(best.get("category") or ""),
        "publish_date": str(best.get("publish_date") or ""),
    }


def _theme_profile_by_id(theme_id: str | None) -> dict[str, object] | None:
    if not theme_id:
        return None
    for profile in _THEME_PROFILES:
        if str(profile.get("id") or "") == theme_id:
            return profile
    return None


def _theme_label_for(theme_id: str | None) -> str | None:
    profile = _theme_profile_by_id(theme_id)
    if profile is not None:
        return str(profile.get("label") or "")
    return _SHARED_THEME_LABELS.get(str(theme_id or ""))


def _guidance_for_theme(theme_id: str | None) -> tuple[str, str]:
    profile = _theme_profile_by_id(theme_id)
    if profile is None:
        return (
            "如需政策背景，可先用1句概括与素材主题直接相关的政策导向，再迅速转入微众银行做法。",
            "结尾可落到继续提升相关服务质效、服务实体经济和高质量发展。",
        )
    return (
        str(profile.get("lead_guidance") or "如需政策背景，可先用1句概括政策导向，再迅速转入微众银行做法。"),
        str(profile.get("closing_guidance") or "结尾可落到继续提升相关服务质效和高质量发展。"),
    )
