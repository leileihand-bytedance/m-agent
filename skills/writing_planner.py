import re

from skills.direct_report.policy_research import DirectReportPolicyResearch


_THEME_PROFILES = (
    {
        "label": "稳外贸",
        "keywords": ("外贸", "出口", "报关", "跨境贸易", "信保"),
        "direct_value": "稳外贸、增强外贸小微企业经营韧性",
        "ending": "稳订单、拓市场、增强经营韧性",
    },
    {
        "label": "科技创新",
        "keywords": ("科创", "科技", "研发", "专利", "硬科技", "创新"),
        "direct_value": "科技金融、支持创新发展",
        "ending": "提升科技企业融资可得性",
    },
    {
        "label": "数据要素",
        "keywords": ("数据要素", "跨境数据", "数据验证", "数据流动", "数字治理"),
        "direct_value": "数据要素流通、数字治理与高水平开放",
        "ending": "拓展场景应用、释放数据价值",
    },
    {
        "label": "普惠金融",
        "keywords": ("小微", "普惠", "融资担保", "微业贷", "个体工商户"),
        "direct_value": "普惠金融、服务实体经济",
        "ending": "提升小微企业融资可得性",
    },
    {
        "label": "绿色金融",
        "keywords": ("绿色", "双碳", "碳达峰", "节能", "环保"),
        "direct_value": "绿色金融与绿色转型",
        "ending": "推动绿色转型与高质量发展",
    },
    {
        "label": "提振消费",
        "keywords": ("消费", "汽车", "新能源车", "购车", "微车贷"),
        "direct_value": "提振消费、扩大内需",
        "ending": "助力扩大内需和绿色消费",
    },
    {
        "label": "消费者保护",
        "keywords": ("消保", "金融知识", "黑灰产", "诈骗", "消费者", "手语", "听障", "无障碍", "适老化", "老年", "爸妈版"),
        "direct_value": "保护金融消费者权益、提升特殊群体金融服务可得性",
        "ending": "提升金融素养与风险防范能力",
    },
    {
        "label": "外部认可",
        "keywords": ("获奖", "大奖", "典型案例", "入选", "评选"),
        "direct_value": "形成示范效应、体现外部认可",
        "ending": "为行业提供参考或示范",
    },
)

_CASE_ARCHETYPES = (
    {
        "label": "综合进展型",
        "keywords": (),
        "lead_strategy": "政策背景型",
        "policy_usage": "开头可用1句政策或发展背景引入，随后回到微众银行的整体推进情况；主体可并列写2-3个板块，但必须服务同一个总主题，不得写成素材摘编。",
        "body_frame": "正文骨架：总主题/总体判断 -> 微众银行围绕2-3个重点板块的做法与进展 -> 阶段性成效 -> 下一步安排。",
        "closing_style": "结尾方式：落在持续深化相关布局、进一步提升服务质效。",
    },
    {
        "label": "典型案例/外部认可型",
        "keywords": ("典型案例", "入选", "法院", "审理", "裁判", "知识产权"),
        "lead_strategy": "直入主题型",
        "policy_usage": "默认不写具体政策名称，直接写事件和治理机制；除非用户素材本身明确给出监管、公安、司法部署等依据，否则不要强行挂政策。",
        "body_frame": "正文骨架：事件入选或认定 -> 行业痛点或治理难点 -> 微众做法/裁判要点 -> 参考价值。",
        "closing_style": "结尾方式：优先落在“有望为同类案件审理或行业治理提供参考”。",
    },
    {
        "label": "活动开展型",
        "keywords": ("活动", "万里行", "宣传", "直播", "小课堂", "教育集市"),
        "lead_strategy": "直入主题型",
        "policy_usage": "默认不写具体政策名称，直接写活动主题、覆盖人群和实际效果；只可用“围绕消保、宣教或权益保护要求”等宽泛导向，不要强行挂政策。",
        "body_frame": "正文骨架：活动主题 -> 重点动作/覆盖人群 -> 覆盖效果与实际价值。",
        "closing_style": "结尾方式：落在持续开展、持续守护、持续提升相关能力。",
    },
    {
        "label": "专项服务型",
        "keywords": ("无障碍", "听障", "适老化", "老年", "爸妈版", "手语客服", "特殊群体"),
        "lead_strategy": "政策背景型",
        "policy_usage": "首段先点政策号召、无障碍建设或特殊群体服务要求，再落到微众行动，正文必须回到具体服务机制和覆盖效果。",
        "body_frame": "正文骨架：政策号召或现实需求 -> 微众行动/具体举措 -> 成果成效 -> 下一步安排。",
        "closing_style": "结尾方式：落在持续优化无障碍、适老化或特殊群体服务能力。",
    },
    {
        "label": "产品支持型",
        "keywords": ("贷", "产品", "推出", "上线", "授信", "融资服务", "信用贷款"),
        "lead_strategy": "政策背景型",
        "policy_usage": "开头点1句行业或政策背景即可，主体重点写产品机制、准入逻辑和支持效果。",
        "body_frame": "正文骨架：行业背景/现实痛点 -> 产品机制或服务设计 -> 数据成效与支持价值。",
        "closing_style": "结尾方式：落在下一步持续深化服务、提升可得性与适配度。",
    },
    {
        "label": "机制探索型",
        "keywords": ("探索", "模式", "机制", "平台", "验证", "识别体系", "双重认定"),
        "lead_strategy": "政策背景型",
        "policy_usage": "开头点规划、战略或改革背景，正文聚焦机制创新，不要把政策段写成主角。",
        "body_frame": "正文骨架：战略背景 -> 堵点难点 -> 机制/平台/模式创新 -> 复制推广价值。",
        "closing_style": "结尾方式：落在继续验证、拓展场景、形成可复制方案。",
    },
    {
        "label": "外部认可型",
        "keywords": ("荣获", "大奖", "评选", "认可", "蝉联"),
        "lead_strategy": "直入主题型",
        "policy_usage": "默认不写具体政策名称，主体重点写认可事项背后的能力基础与代表性成果；如需政策导向，只写宽泛方向，不写未经素材支撑的文件名。",
        "body_frame": "正文骨架：获奖或认可事实 -> 能力基础 -> 代表性成果或数据 -> 行业意义。",
        "closing_style": "结尾方式：落在持续深化相关能力、服务国家或行业重点方向。",
    },
)

_NO_POLICY_MATERIAL_ARCHETYPES = {"典型案例/外部认可型", "活动开展型", "外部认可型"}

_ACTION_KEYWORDS = (
    "推出",
    "上线",
    "开展",
    "入选",
    "荣获",
    "支持",
    "探索",
    "形成",
    "联合",
    "发布",
    "服务",
    "助力",
)
_BOILERPLATE_KEYWORDS = (
    "联系人",
    "签发人",
    "责任编辑",
    "深圳前海微众银行股份有限公司",
    "微众银行信息直报件",
    "微众银行信息动态简报",
)

_COMPREHENSIVE_PROGRESS_TITLE_MARKERS = ("年度", "盘点", "综述", "回顾", "共成长", "深耕")
_COMPREHENSIVE_PROGRESS_MARKERS = ("持续发力", "持续推进", "多措并举", "不断丰富", "阶段性进展", "综合进展")
_COMPREHENSIVE_PROGRESS_SECTION_GROUPS = (
    ("普惠金融", "小微", "微业贷", "个体工商户"),
    ("产业金融", "制造业", "供应链", "产业链"),
    ("绿色", "双碳"),
    ("农业", "乡村振兴", "涉农"),
    ("科创", "科技", "专精特新"),
    ("公益", "社会公益", "教育帮扶", "慈善"),
    ("产品创新", "企业活期+", "账户服务", "资金管理"),
)


def build_direct_report_plan(
    instruction: str,
    materials: list[object],
    *,
    policy_research: DirectReportPolicyResearch | None = None,
) -> str:
    core_materials = _core_materials(materials)
    theme = _summarize_theme(instruction, core_materials)
    archetype = _detect_case_archetype(core_materials, theme)
    lead_strategy = _direct_report_lead_strategy(core_materials, theme, archetype)
    ending_direction = _theme_ending(instruction, core_materials)
    key_facts = _select_sentences(core_materials, theme, limit=3, require_number=False)
    key_data = _select_sentences(core_materials, theme, limit=2, require_number=True)
    policy_anchor = _first_material_by_source(materials, "policy_knowledge")
    title_focus = _title_focus(core_materials, theme, archetype)

    lines = [
        "文体：直报",
        f"案例类型：{archetype['label']}",
        f"开头策略：{lead_strategy}",
        f"建议标题方向：{title_focus}",
        f"核心主题：{theme}",
        f"核心事件：{_core_event(core_materials, archetype, theme)}",
        "主体要求：全文以微众银行为组织中心，优先让“微众银行”承担主语位，合作方、政府基金、平台、活动和媒体表述只作背景或协同支撑，不喧宾夺主。",
        _mainline_rule(archetype),
        _transition_rule(lead_strategy),
        "段落推进：各段之间按“背景/部署 -> 微众响应/动作 -> 成效 -> 下一步”递进，段首要承担承上启下作用，不要只是换一组事实继续堆砌。",
        "个案使用方式：单个企业受益案例如需使用，只能作为一句辅助例证，不单独成段，不作为标题或主线。",
        archetype["body_frame"],
        f"政策使用方式：{archetype['policy_usage']}",
        f"结尾抬升：先写微众银行下一步安排，再自然抬升到{ending_direction}、做好金融“五篇大文章”或服务高质量发展等与主题一致的更高层落点，结尾不只停留在就事论事，也不要脱离素材空喊口号。",
        "补充材料使用边界：政策或其他补充材料只可用于首段背景一句，不得据此引入用户素材未出现的新业务线、产品、做法或数据。",
        archetype["closing_style"],
    ]
    if policy_anchor:
        lines.append(f"可借用政策背景：{policy_anchor}")
    if policy_research:
        if policy_research.use_policy and policy_research.selected_policy:
            policy_theme_label = policy_research.theme_label or "与素材主题直接相关的"
            lines.append(
                f"政策研究结论：本稿可挂{policy_theme_label}相关政策，优先使用《{policy_research.selected_policy['title']}》作背景一笔。"
            )
            lines.append(f"政策开头建议：{policy_research.lead_guidance}")
            lines.append(f"政策转微众建议：{policy_research.bridge_guidance}")
            lines.append(f"政策结尾建议：{policy_research.closing_guidance}")
        else:
            lines.append("政策研究结论：本稿直入主题，不挂具体政策背景。")
    if key_facts:
        lines.append("优先写入事实：")
        lines.extend(f"- {sentence}" for sentence in key_facts)
    if key_data:
        lines.append("优先写入数据：")
        lines.extend(f"- {sentence}" for sentence in key_data)
    lines.append("避免：多点平铺、宣传腔、空泛口号、把联系人或来源写进正文。")
    return "\n".join(lines)


def should_add_direct_report_policy_materials(instruction: str, materials: list[object]) -> bool:
    """判断直报是否需要额外注入政策库材料。"""
    core_materials = _core_materials(materials)
    theme = _summarize_theme(instruction, core_materials)
    archetype = _detect_case_archetype(core_materials, theme)
    return str(archetype.get("label", "")) not in _NO_POLICY_MATERIAL_ARCHETYPES


def _core_materials(materials: list[object]) -> list[dict[str, object]]:
    preferred = [
        item
        for item in materials
        if isinstance(item, dict) and str(item.get("source", "") or "") not in {"policy_knowledge", "bank_knowledge"}
    ]
    if preferred:
        return preferred
    return [item for item in materials if isinstance(item, dict)]


def _summarize_theme(instruction: str, materials: list[dict[str, object]]) -> str:
    hits = _matched_profiles(instruction + "\n" + "\n".join(_material_text(item) for item in materials))
    if not hits:
        return "服务实体经济与业务亮点"
    profile = hits[0]
    return profile["direct_value"]


def _matched_profiles(text: str) -> list[dict[str, object]]:
    lowered = text.replace(" ", "")
    scored = []
    for profile in _THEME_PROFILES:
        score = sum(1 for keyword in profile["keywords"] if keyword in lowered)
        if score:
            scored.append((score, profile))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [profile for _, profile in scored]


def _theme_ending(instruction: str, materials: list[dict[str, object]]) -> str:
    hits = _matched_profiles(instruction + "\n" + "\n".join(_material_text(item) for item in materials))
    if not hits:
        return "服务实体经济和高质量发展"
    return str(hits[0]["ending"])


def _detect_case_archetype(materials: list[dict[str, object]], theme: str) -> dict[str, str]:
    text = "\n".join(_material_text(item) for item in materials)
    if _looks_like_comprehensive_progress_case(materials, text):
        return _archetype_by_label("综合进展型")

    scored = []
    for archetype in _CASE_ARCHETYPES:
        if not archetype["keywords"]:
            continue
        score = sum(text.count(keyword) for keyword in archetype["keywords"])
        if score:
            scored.append((score, archetype))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
    if "特殊群体金融服务可得性" in theme:
        return _archetype_by_label("专项服务型")
    if "保护金融消费者权益" in theme:
        return _archetype_by_label("活动开展型")
    return _archetype_by_label("机制探索型")


def _direct_report_lead_strategy(materials: list[dict[str, object]], theme: str, archetype: dict[str, str]) -> str:
    if archetype.get("lead_strategy"):
        return str(archetype["lead_strategy"])
    text = "\n".join(_material_text(item) for item in materials)
    if any(keyword in text for keyword in ("日前", "近日", "近期", "入选", "荣获", "上线", "推出")):
        if any(keyword in text for keyword in ("以来", "背景下", "规划", "部署", "工作要求")):
            return "政策背景型"
        return "直入主题型"
    if any(keyword in theme for keyword in ("稳外贸", "绿色", "提振消费", "科技金融", "数据要素")):
        return "政策背景型"
    return "直入主题型"


def _title_focus(materials: list[dict[str, object]], theme: str, archetype: dict[str, str]) -> str:
    if str(archetype.get("label", "")) == "综合进展型":
        return (
            "标题以“微众银行+总主题+阶段性成效/综合进展”组织，避免从某个单一产品或近期动作切入；"
            f"可围绕“{_comprehensive_focus(theme)}”提炼。"
        )
    if materials:
        title = str(materials[0].get("title", "") or "").strip()
        if title and _title_needs_recentering(title):
            focus = _webank_focus_sentence(materials)
            if focus:
                return (
                    "标题以“微众银行+核心举措/机制+结果/作用”组织，避免沿用外部媒体标题或他方口径；"
                    f"可围绕“{focus}”提炼。"
                )
        if title:
            return title
    return f"围绕{theme}概括核心动作和结果"


def _core_event(materials: list[dict[str, object]], archetype: dict[str, str], theme: str) -> str:
    if str(archetype.get("label", "")) == "综合进展型":
        return _comprehensive_focus(theme)
    focus = _webank_focus_sentence(materials)
    if focus:
        return focus
    titles = [str(item.get("title", "") or "").strip() for item in materials if str(item.get("title", "") or "").strip()]
    if titles:
        return titles[0]
    sentences = _select_sentences(materials, "", limit=1, require_number=False)
    return sentences[0] if sentences else "结合材料提炼最能代表主线的一项进展"


def _first_material_by_source(materials: list[object], source: str) -> str:
    for item in materials:
        if not isinstance(item, dict):
            continue
        if str(item.get("source", "") or "") == source:
            title = str(item.get("title", "") or "").strip()
            return title or str(item.get("text", "") or "").strip()[:60]
    return ""


def _select_sentences(
    materials: list[dict[str, object]],
    theme: str,
    *,
    limit: int,
    require_number: bool,
) -> list[str]:
    candidates: list[tuple[int, int, str, bool]] = []
    seen: set[str] = set()
    for material_idx, item in enumerate(materials):
        for sentence_idx, sentence in enumerate(_split_sentences(_material_text(item))):
            if _should_skip_sentence(sentence):
                continue
            if require_number and not re.search(r"\d", sentence):
                continue
            key = re.sub(r"\s+", "", sentence)
            if key in seen:
                continue
            seen.add(key)
            score = _sentence_score(sentence, theme)
            is_single_case = _is_single_enterprise_case_sentence(sentence)
            if is_single_case:
                score -= 8
            candidates.append((score, material_idx * 100 + sentence_idx, sentence, is_single_case))

    preferred = [item for item in candidates if not item[3]]
    preferred.sort(key=lambda item: (-item[0], item[1]))
    if preferred:
        return [sentence for _, _, sentence, _ in preferred[:limit]]

    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [sentence for _, _, sentence, _ in candidates[:limit]]


def _split_sentences(text: str) -> list[str]:
    chunks = re.split(r"[。！？；\n]", text)
    return [chunk.strip(" /-—") for chunk in chunks if chunk.strip(" /-—")]


def _material_text(item: dict[str, object]) -> str:
    title = str(item.get("title", "") or "")
    text = str(item.get("text", "") or "")
    return f"{title}\n{text}".strip()


def _should_skip_sentence(sentence: str) -> bool:
    if len(sentence) < 12:
        return True
    return any(keyword in sentence for keyword in _BOILERPLATE_KEYWORDS)


def _sentence_score(sentence: str, theme: str) -> int:
    score = 0
    if re.search(r"\d", sentence):
        score += 4
    if re.search(r"(亿元|万元|万户|户|人次|分钟|秒|万笔|笔|场|家|年限|期限|贷款|授信)", sentence):
        score += 3
    if re.search(r"^20\d{2}年", sentence):
        score -= 2
    if len(sentence) <= 45:
        score += 1
    if len(sentence) >= 90:
        score -= 1
    if any(keyword in sentence for keyword in _ACTION_KEYWORDS):
        score += 2
    if any(fragment for fragment in theme.split("、") if fragment and fragment[:2] in sentence):
        score += 2
    return score


def _title_needs_recentering(title: str) -> bool:
    stripped = title.lstrip("《“\"'【（(")
    return not stripped.startswith("微众银行")


def _webank_focus_sentence(materials: list[dict[str, object]]) -> str:
    candidates: list[tuple[int, int, str, bool]] = []
    for material_idx, item in enumerate(materials):
        text_body = str(item.get("text", "") or "").strip()
        for sentence_idx, sentence in enumerate(_split_sentences(text_body)):
            if "微众银行" not in sentence or _should_skip_sentence(sentence):
                continue
            score = _sentence_score(sentence, "")
            if any(keyword in sentence for keyword in _ACTION_KEYWORDS):
                score += 2
            is_single_case = _is_single_enterprise_case_sentence(sentence)
            if is_single_case:
                score -= 8
            candidates.append((score, material_idx * 100 + sentence_idx, _normalize_focus_sentence(sentence), is_single_case))

    non_case_candidates = [item for item in candidates if not item[3]]
    non_case_candidates.sort(key=lambda item: (-item[0], item[1]))
    if non_case_candidates:
        return non_case_candidates[0][2]
    candidates.sort(key=lambda item: (-item[0], item[1]))
    if candidates:
        return candidates[0][2]

    for item in materials:
        title = str(item.get("title", "") or "").strip()
        if "微众银行" in title and not _should_skip_sentence(title):
            return _normalize_focus_sentence(title)
    return ""


def _normalize_focus_sentence(sentence: str) -> str:
    cleaned = sentence.strip()
    for prefix in ("近日，", "日前，", "近期，", "一直以来，", "今年以来，", "2026年以来，"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned


def _is_single_enterprise_case_sentence(sentence: str) -> bool:
    if not re.search(r"(一家|某家|该|某)(?:[^\n，。；]{0,12})?(企业|公司|商户|工厂)", sentence):
        return False
    return bool(re.search(r"(获批|授信|贷款|放款|融资|备货|周转|资金)", sentence))


def _mainline_rule(archetype: dict[str, str]) -> str:
    if str(archetype.get("label", "")) == "综合进展型":
        return (
            "主线限定：这类材料允许围绕一个总主题展开，主体分 2-3 个并列板块承接，"
            "不要被某个单一产品、活动或近期细节带偏；各板块都要回到微众银行整体推进和阶段性成效。"
        )
    return "主线限定：正文按“政策/场景背景 -> 微众银行做了什么 -> 取得什么成效 -> 下一步怎么做”展开，不另起第二条业务线。"


def _transition_rule(lead_strategy: str) -> str:
    if lead_strategy == "政策背景型":
        return (
            "衔接要求：如首段先写党中央、国务院、监管部署或政策背景，下一句要用“在此背景下，微众银行……”"
            "或“微众银行积极响应相关部署……”承接，体现“上级有部署、微众有响应”，不要背景句后直接跳到数据、个案或细节。"
        )
    return "衔接要求：即使直入主题，句与句之间也要有自然过渡，先交代事件，再顺势转入微众银行做法、成效和意义，不要把素材原句硬拼在一起。"


def _archetype_by_label(label: str) -> dict[str, str]:
    return next(item for item in _CASE_ARCHETYPES if item["label"] == label)


def _looks_like_comprehensive_progress_case(materials: list[dict[str, object]], text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    title = str(materials[0].get("title", "") or "").strip() if materials else ""
    section_hits = sum(1 for group in _COMPREHENSIVE_PROGRESS_SECTION_GROUPS if any(keyword in compact for keyword in group))
    marker_hits = sum(1 for keyword in _COMPREHENSIVE_PROGRESS_MARKERS if keyword in compact)
    if re.search(r"在[^。；\n]{0,12}方面", compact):
        marker_hits += 1
    if "此外" in compact or "同时" in compact:
        marker_hits += 1

    profile_hits = len(_matched_profiles(text))
    title_signal = any(keyword in title for keyword in _COMPREHENSIVE_PROGRESS_TITLE_MARKERS)

    if title_signal and section_hits >= 3:
        return True
    return section_hits >= 4 and marker_hits >= 2 and profile_hits >= 2


def _comprehensive_focus(theme: str) -> str:
    return f"微众银行围绕{theme}持续推进并形成阶段性进展"
