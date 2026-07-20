import re
from collections import Counter

from skills.writer1.schema import BriefCriticResult, BriefPlanResult, BriefViolation


_TITLE_LEADING_PUNCTUATION = re.compile(r'''^[《“"'\[（(]+''')
_LIST_PATTERNS = (
    re.compile(r"[\n\r]+(?:\s*)(?:一|二|三|四|五|六|七|八|九|十)[是、].+"),
    re.compile(r"[\n\r]+(?:\s*)(?:第一|第二|第三|第四|第五).+"),
    re.compile(r"[\n\r]+(?:\s*)\d+[\.、)）].+"),
    re.compile(r"[\n\r]+(?:\s*)[\-•●▪]\s*.+"),
)
_THEME_PROFILES = (
    {"label": "small_micro", "keywords": ("小微", "普惠", "融资", "贷款", "授信", "微业贷", "个体工商户")},
    {"label": "foreign_trade", "keywords": ("外贸", "出口", "跨境", "报关", "稳订单", "拓市场")},
    {"label": "technology", "keywords": ("科技", "人工智能", "AI", "研发", "技术", "平台", "数据验证")},
    {"label": "consumer_protection", "keywords": ("消保", "消费者", "金融知识", "宣传", "反诈", "黑灰产")},
    {"label": "green", "keywords": ("绿色", "双碳", "环保", "节能")},
    {"label": "inclusive_service", "keywords": ("无障碍", "听障", "适老化", "特殊群体", "手语")},
    {"label": "internal_activity", "keywords": ("员工", "羽毛球", "比赛", "团建", "文体", "联谊")},
)
_REQUIRED_SUBJECT_NAME = "深圳前海微众银行（以下简称“我行”）"
_MAX_BODY_CHARS = 1200
_STRONG_CLAIMS = (
    "国内首个",
    "全国首个",
    "业内首个",
    "行业首创",
    "全国首创",
    "填补空白",
    "唯一",
    "最大",
    "第一梯队",
    "国际领先",
    "行业领先",
)
_NUMBER_TOKEN = re.compile(
    r"\d+(?:\.\d+)?(?:\+|余|多)?(?:%|％|万|亿|千|百)?"
    r"(?:亿元|万元|万户|万家|元|户|家|项|个|人|次|年|月|日|级|倍|场景)?"
)
_BUSINESS_THEME_LABELS = {
    "small_micro",
    "foreign_trade",
    "technology",
    "consumer_protection",
    "green",
    "inclusive_service",
}
_BRIEF_CASE_PROFILES = (
    {
        "label": "专项治理型",
        "keywords": ("黑灰产", "整治", "治理", "风险事件", "投诉", "举报", "震慑"),
        "guidance": "重点写问题背景、治理动作、协同机制和阶段性成效，压缩案件细节与口号式表态。",
        "must_cover": "治理对象、治理动作、协同机制和阶段性成效",
        "compress": "案件细节、风险主体信息和口号式表态",
        "structure": "问题背景—治理机制—具体动作—阶段成效",
    },
    {
        "label": "平台合作型",
        "keywords": ("平台", "接入", "上线", "商业数据通", "跨境数据验证", "合作理事会", "新加坡", "香港金管局", "方案设计", "技术支持"),
        "guidance": "重点写平台定位、合作分工、已落地场景和对区域协同或监管关注点的价值，压缩会议流程。",
        "must_cover": "平台定位、我行分工、落地场景和区域协同价值",
        "compress": "签约流程、会议名单、致辞和现场介绍",
        "structure": "平台背景—我行角色—运行机制—落地价值",
    },
    {
        "label": "外部认可型",
        "keywords": ("获奖", "评级", "认证", "认可", "奖", "铂金级"),
        "guidance": "把获奖、评级或认证改写成我行在相关领域持续推进并获权威认可，不要只罗列奖项或证书信息。",
        "must_cover": "持续实践、具体成果、认可主体和认可所证明的能力",
        "compress": "奖项沿革、评选流程和证书名称堆叠",
        "structure": "实践基础—近期认可—能力证明—后续推进",
    },
    {
        "label": "活动亮相型",
        "keywords": ("亮相", "活动", "金融科技周", "万里行", "研学", "宣传", "展会", "论坛", "直播", "参观"),
        "guidance": "重点写活动承载的主题、展示内容、传播效果和后续价值，压缩人物名单、现场流程和会务描写。",
        "must_cover": "活动主题、我行展示内容、有效触达和后续业务价值",
        "compress": "嘉宾名单、会议议程、展位描写和泛化规模数据",
        "structure": "活动背景—展示重点—交流成效—后续价值",
    },
    {
        "label": "标准引领型",
        "keywords": ("标准", "国际标准", "国家标准", "IEEE", "话语权"),
        "guidance": "重点写牵头或参与标准的内容、行业影响和对相关领域规范化发展的支撑，不要堆砌标准名称。",
        "must_cover": "参与方式、标准覆盖领域、成果应用和行业规范价值",
        "compress": "标准全称清单和缺少解释的数量堆叠",
        "structure": "标准化背景—参与机制—代表成果—行业价值",
    },
    {
        "label": "能力建设型",
        "keywords": ("科技管理", "研发", "运维", "数字员工", "Agent", "Copilot", "AI", "人工智能+", "工程化平台", "能力建设"),
        "guidance": "重点写能力建设场景、落地做法、效率提升和可复制经验，避免写成纯技术清单。",
        "must_cover": "外部趋势或业务需求、已有基础、能力建设路径和应用成效",
        "compress": "技术名词清单、无材料支撑的先进性判断和内部过程细节",
        "structure": "趋势与需求—能力基础—平台或机制建设—应用成效",
    },
    {
        "label": "机制成果型",
        "keywords": ("机制", "模式", "协调工作", "走深走实", "协同", "专班", "联动", "担保"),
        "guidance": "重点写机制为何建立、如何运转、解决什么问题、形成什么成效，是最常见的监管报送简报类型。",
        "must_cover": "机制背景、参与主体、运转方式、解决的问题和阶段成效",
        "compress": "一般性表态和缺少实际动作的组织描述",
        "structure": "机制背景—组织方式—运行措施—阶段成效",
    },
    {
        "label": "产品工具型",
        "keywords": ("产品", "工具", "自测", "贷款", "小程序", "APP", "诊断", "授信"),
        "guidance": "重点写服务痛点、产品或工具机制、使用路径和阶段性效果，不要写成产品说明书。",
        "must_cover": "服务痛点、工具机制、使用路径和实际效果",
        "compress": "功能菜单、操作说明和宣传性产品形容词",
        "structure": "问题痛点—工具方案—运行机制—服务成效",
    },
    {
        "label": "综合成果型",
        "keywords": ("大文章", "行稳致远", "积极成果", "阶段性成果", "持续提升", "聚力服务", "全面提升"),
        "guidance": "围绕一个总主题组织2到3个核心板块，分别交代主要动作和数据，适合阶段性盘点类简报。",
        "must_cover": "统一主题、2到3项代表进展、关键数据和整体阶段价值",
        "compress": "时间线流水账、重复成果和与主线无关的边缘事项",
        "structure": "总体进展—核心板块一—核心板块二/三—后续方向",
    },
)


def brief_case_profile(label: str) -> dict[str, object]:
    for profile in _BRIEF_CASE_PROFILES:
        if profile["label"] == label:
            return dict(profile)
    raise ValueError(f"未知简报类型：{label}")


def build_brief_plan(
    instruction: str,
    materials: list[object],
    *,
    multi_source: bool,
    tools: object | None = None,
    skill_id: str = "",
) -> str:
    core_materials = [item for item in materials if isinstance(item, dict)]
    case_type = classify_brief_case_type(instruction, core_materials, multi_source=multi_source)
    theme = _summarize_theme(instruction, core_materials)
    ledger = _build_fact_ledger(instruction, core_materials)
    key_facts = [item["text"] for item in ledger if not item["has_number"]][:3]
    key_data = [item["text"] for item in ledger if item["has_number"]][:2]
    lines = [
        f"写作类型：{'多素材简报' if multi_source else '单素材简报'}",
        "报送定位：面向深圳市金融办、南山区、前海管理局、深圳人行、深圳金监局等地方政府和监管部门，重点展示微众银行近期动态及成果。",
        "篇幅要求：正常控制在1000字左右，最长不超过1200字。",
        "文体要求：不要沿用新闻稿或通稿写法，要改写为正式、克制、可直接报送的地方监管简报体。",
        f"简报类型：{case_type['label']}",
        f"类型写法：{case_type['guidance']}",
        f"必须覆盖：{case_type['must_cover']}",
        f"应当压缩：{case_type['compress']}",
        f"推荐结构：{case_type['structure']}",
        f"{'统一主题' if multi_source else '核心主线'}：{theme}",
        f"{'结构要求：围绕一个统一主题组织材料，明确主线与辅线，不能逐条拼接素材。' if multi_source else '结构要求：围绕一个核心主线展开，按背景、做法、成效组织正文，不要写成活动报道。'}",
        "A类样本共通写法：第一段尽快点明值得报送的近期动态或成果；正文用2到3个核心板块展开，每个板块都要交代动作、机制和阶段性结果；活动、获奖、亮相类题材也要从监管和地方关注的价值切入，而不是堆现场信息。",
        "主体称谓：正文首次出现主体时写“深圳前海微众银行（以下简称“我行”）”，后文统一写“我行”。",
        "补充材料使用方式：政策和微众补充材料只能补背景、口径和关键数据，不能把正文带向用户材料之外的新主线。",
    ]
    if multi_source:
        relation = assess_multi_source_relation(core_materials)
        lines.append(f"素材关联判断：{relation['relation']}")
        lines.append(f"整合提示：{relation['summary']}")
    if key_facts:
        lines.append("优先写入事实：")
        lines.extend(f"- {sentence}" for sentence in key_facts)
    if key_data:
        lines.append("优先写入数据：")
        lines.extend(f"- {sentence}" for sentence in key_data)
    lines.append("避免：新闻发布式开头、口号化结尾、逐条拼接素材、列表式正文。")
    fallback = "\n".join(lines)
    if tools is None or not skill_id:
        return fallback
    return _semantic_brief_plan(
        instruction=instruction,
        materials=core_materials,
        multi_source=multi_source,
        skill_id=skill_id,
        tools=tools,
        fallback=fallback,
        ledger=ledger,
    )


def classify_brief_case_type(
    instruction: str,
    materials: list[dict[str, object]],
    *,
    multi_source: bool,
) -> dict[str, str]:
    merged = _merged_brief_text(instruction, materials)
    instruction_lower = instruction.lower()
    merged_lower = merged.lower()
    scored: list[tuple[int, int, dict[str, object]]] = []
    for index, profile in enumerate(_BRIEF_CASE_PROFILES):
        score = 0
        for keyword in profile["keywords"]:
            lowered = str(keyword).lower()
            if lowered not in merged_lower:
                continue
            score += 3 if len(lowered) >= 4 else 1
            if lowered in instruction_lower:
                score += 2
        scored.append((score, -index, profile))
    score, _, selected = max(scored, key=lambda item: (item[0], item[1]))
    if score > 0:
        return {key: str(selected[key]) for key in ("label", "guidance", "must_cover", "compress", "structure")}
    if multi_source:
        return _profile_summary("综合成果型")
    return _profile_summary("机制成果型")


def _profile_summary(label: str) -> dict[str, str]:
    profile = brief_case_profile(label)
    return {
        key: str(profile[key])
        for key in ("label", "guidance", "must_cover", "compress", "structure")
    }


def _semantic_brief_plan(
    *,
    instruction: str,
    materials: list[dict[str, object]],
    multi_source: bool,
    skill_id: str,
    tools: object,
    fallback: str,
    ledger: list[dict[str, object]],
) -> str:
    ledger_lines = [
        f"{item['id']}（材料{item['source_index']}）：{item['text']}"
        for item in ledger
    ]
    try:
        result = tools.call(
            "llm_planner",
            {
                "task": f"{skill_id}_plan",
                "skill_id": skill_id,
                "output_type": BriefPlanResult,
                "prompt_path": "prompts/plan.md",
                "instruction": instruction,
                "planning_note": (
                    f"{fallback}\n\n候选事实台账（只能选择编号，不得改写事实）：\n"
                    + "\n".join(ledger_lines)
                ),
                "materials": materials,
            },
        )
        plan = BriefPlanResult.model_validate(result)
    except Exception:
        return fallback

    known_ids = {str(item["id"]) for item in ledger}
    selected_ids = [
        item_id
        for item_id in [*plan.selected_fact_ids, *plan.selected_data_ids]
        if item_id in known_ids
    ]
    selected_ids = list(dict.fromkeys(selected_ids))
    if not selected_ids:
        selected_ids = [str(item["id"]) for item in ledger[:3]]
    fact_by_id = {str(item["id"]): str(item["text"]) for item in ledger}
    semantic_profile = brief_case_profile(plan.brief_type)
    lines = [
        fallback,
        "",
        "语义策划：已完成（以下策划优先于关键词初筛）",
        f"简报类型：{plan.brief_type}",
        f"语义类型卡必须覆盖：{semantic_profile['must_cover']}",
        f"语义类型卡主动压缩：{semantic_profile['compress']}",
        f"核心信息：{plan.core_message.strip()}",
        f"报送价值：{plan.audience_value.strip()}",
        f"结构安排：{'；'.join(item.strip() for item in plan.section_plan if item.strip())}",
    ]
    if selected_ids:
        lines.append("选定事实台账：")
        lines.extend(f"- {item_id}：{fact_by_id[item_id]}" for item_id in selected_ids)
    if plan.excluded_details:
        lines.append(
            "主动压缩："
            + "；".join(item.strip() for item in plan.excluded_details if item.strip())
        )
    return "\n".join(lines)


def _build_fact_ledger(
    instruction: str,
    materials: list[dict[str, object]],
    *,
    limit: int = 12,
) -> list[dict[str, object]]:
    instruction_compact = re.sub(r"\s+", "", instruction)
    grams = {
        instruction_compact[index : index + 2].lower()
        for index in range(max(0, len(instruction_compact) - 1))
        if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", instruction_compact[index : index + 2])
    }
    candidates: list[tuple[int, int, int, str, bool]] = []
    seen: set[str] = set()
    order = 0
    for source_index, item in enumerate(materials, 1):
        for sentence in re.split(r"[。！？；\n]", _material_text(item)):
            cleaned = re.sub(r"\s+", " ", sentence).strip()
            normalized = re.sub(r"\s+", "", cleaned)
            if len(normalized) < 12 or normalized in seen:
                continue
            seen.add(normalized)
            has_number = bool(re.search(r"\d", normalized))
            score = 3 if has_number else 0
            score += min(5, sum(1 for gram in grams if gram and gram in normalized.lower()))
            if any(word in normalized for word in ("建立", "推出", "建设", "上线", "形成", "提升", "覆盖", "实现", "参与", "协同")):
                score += 2
            if any(word in normalized for word in ("出席", "致辞", "会议召开", "会上表示", "嘉宾")):
                score -= 4
            candidates.append((score, -order, source_index, cleaned, has_number))
            order += 1
    selected = sorted(candidates, reverse=True)[:limit]
    return [
        {
            "id": f"F{index}",
            "source_index": source_index,
            "text": text,
            "has_number": has_number,
        }
        for index, (_, _, source_index, text, has_number) in enumerate(selected, 1)
    ]


def assess_multi_source_relation(materials: list[object]) -> dict[str, str]:
    core_materials = [item for item in materials if isinstance(item, dict)]
    label_sets = [_matched_labels(_material_text(item)) for item in core_materials]
    label_sets = [labels for labels in label_sets if labels]
    if not label_sets:
        return {
            "relation": "medium",
            "summary": "素材主题信号不够强，整合时要人为压缩成一个统一主题。",
            "message": "",
        }

    overlap = set.intersection(*label_sets) if len(label_sets) >= 2 else set(next(iter(label_sets), set()))
    if overlap:
        theme = "、".join(sorted(overlap))
        return {
            "relation": "strong",
            "summary": f"多份素材共享主题“{theme}”，可围绕一个统一主线整合。",
            "message": "",
        }

    label_counter = Counter(label for labels in label_sets for label in labels)
    dominant = [label for label, count in label_counter.items() if count >= max(2, len(label_sets) - 1)]
    if dominant:
        theme = "、".join(sorted(dominant))
        return {
            "relation": "medium",
            "summary": f"多份素材存在相近主题“{theme}”，需要强约束整合，不可逐条拼接。",
            "message": "",
        }

    if all(labels and labels.issubset(_BUSINESS_THEME_LABELS) for labels in label_sets):
        return {
            "relation": "medium",
            "summary": "多份素材都属于经营业务相关主题，但侧重点不同，整合时要先提炼一个更高层次的统一主线。",
            "message": "",
        }

    return {
        "relation": "weak",
        "summary": "多份素材主题分散，缺少稳定共同主线。",
        "message": "当前素材关联性较弱，暂不适合整合成一篇简报，建议拆分后分别撰写，或补充能支撑同一主线的材料。",
    }


def check_brief_title_format(title: str) -> list[BriefViolation]:
    stripped = _TITLE_LEADING_PUNCTUATION.sub("", title.strip())
    if "微众银行" not in stripped:
        return [
            BriefViolation(
                rule="title-format",
                severity="hard",
                message="标题未包含“微众银行”。",
                suggestion="标题应明确体现微众银行及核心主题。",
            )
        ]
    if _has_space_split_title(stripped):
        return [
            BriefViolation(
                rule="title-format",
                severity="hard",
                message="标题存在用空格硬连接的两段式结构。",
                suggestion="如果标题分成两层意思，中间应用中文逗号或冒号连接，不要用空格断开。",
            )
        ]
    return []


def check_brief_subject_name(body: str) -> list[BriefViolation]:
    text = body.strip()
    if not text:
        return []
    if re.search(r"(?<!前海)微众银行（以下简称“我行”）", text):
        return [
            BriefViolation(
                rule="brief-subject-name",
                severity="hard",
                message="正文首次主体称谓使用了错误简称引入。",
                suggestion="首次提及主体时请使用“深圳前海微众银行（以下简称“我行”）”，后文统一写“我行”。",
            )
        ]
    if _REQUIRED_SUBJECT_NAME not in text:
        return [
            BriefViolation(
                rule="brief-subject-name",
                severity="hard",
                message="正文未按要求使用首次主体称谓。",
                suggestion="正文首次提及主体时请使用“深圳前海微众银行（以下简称“我行”）”，后文统一写“我行”。",
            )
        ]
    return []


def check_no_list_style(body: str) -> list[BriefViolation]:
    for pattern in _LIST_PATTERNS:
        if pattern.search(body):
            return [
                BriefViolation(
                    rule="no-list-style",
                    severity="hard",
                    message="正文出现列表式或分点式结构。",
                    suggestion="把列表项改写成连贯段落，用自然衔接句组织简报内容。",
                )
            ]
    return []


def check_brief_length(body: str) -> list[BriefViolation]:
    count = len(re.sub(r"[\s*_#]", "", body))
    if count > _MAX_BODY_CHARS:
        return [
            BriefViolation(
                rule="brief-length",
                severity="hard",
                message=f"正文约{count}字，超过1200字上限。",
                suggestion="压缩背景、会务信息和重复表述，保留主线相关事实与关键数据。",
            )
        ]
    return []


def check_numeric_grounding(
    title: str,
    body: str,
    materials: list[dict[str, object]],
) -> list[BriefViolation]:
    if not materials:
        return []
    source_text = re.sub(
        r"\s+",
        "",
        "\n".join(_material_text(item) for item in materials),
    )
    output_text = re.sub(r"\s+", "", f"{title}\n{body}")
    source_tokens = set(_NUMBER_TOKEN.findall(source_text))
    unsupported = [
        token
        for token in dict.fromkeys(_NUMBER_TOKEN.findall(output_text))
        if token and token not in source_tokens
    ]
    if not unsupported:
        return []
    return [
        BriefViolation(
            rule="numeric-grounding",
            severity="hard",
            message=f"成稿出现材料中无法逐字找到的数字口径：{'、'.join(unsupported[:5])}。",
            suggestion="只保留用户材料、经允许的政策材料或我行知识材料中已有的数字及单位，不做推算或改写口径。",
        )
    ]


def check_claim_grounding(
    title: str,
    body: str,
    materials: list[dict[str, object]],
) -> list[BriefViolation]:
    if not materials:
        return []
    source_text = re.sub(r"\s+", "", "\n".join(_material_text(item) for item in materials))
    output_text = re.sub(r"\s+", "", f"{title}\n{body}")
    unsupported = [
        claim
        for claim in _STRONG_CLAIMS
        if claim in output_text and claim not in source_text
    ]
    if not unsupported:
        return []
    return [
        BriefViolation(
            rule="claim-grounding",
            severity="hard",
            message=f"成稿新增了材料未支持的强结论：{'、'.join(dict.fromkeys(unsupported))}。",
            suggestion="删除或改为材料能够直接支持的事实表述，不自行增加首创、唯一、领先或填补空白等评价。",
        )
    ]


def validate_brief_deterministic(
    title: str,
    body: str,
    *,
    materials: list[object] | None = None,
) -> list[BriefViolation]:
    core_materials = [item for item in list(materials or []) if isinstance(item, dict)]
    violations: list[BriefViolation] = []
    violations.extend(check_brief_title_format(title))
    violations.extend(check_brief_subject_name(body))
    violations.extend(check_no_list_style(body))
    violations.extend(check_brief_length(body))
    violations.extend(check_numeric_grounding(title, body, core_materials))
    violations.extend(check_claim_grounding(title, body, core_materials))
    return violations


def brief_critic_check(
    *,
    title: str,
    body: str,
    materials: list[object],
    planning_note: str,
    tools: object,
    skill_id: str,
) -> list[BriefViolation]:
    instruction = f"""请审查以下简报初稿：

标题：{title}

正文：
{body}
"""
    try:
        result = tools.call(
            "llm_writer",
            {
                "task": f"{skill_id}_critic",
                "skill_id": skill_id,
                "output_type": BriefCriticResult,
                "prompt_path": "prompts/critic.md",
                "instruction": instruction,
                "planning_note": planning_note,
                "materials": materials,
            },
        )
    except Exception:
        return []

    if not isinstance(result, dict):
        return []
    violations = result.get("violations") or []
    if isinstance(violations, list):
        return [BriefViolation.model_validate(item) for item in violations]
    return []


def format_brief_violations(violations: list[object]) -> str:
    lines = ["上一稿存在以下问题，请逐项修正后重写："]
    for idx, violation in enumerate(violations, 1):
        lines.append(
            f"{idx}. [{violation.severity}] {violation.message}（{violation.rule}）\n   修改建议：{violation.suggestion}"
        )
    return "\n".join(lines)


def _matched_labels(text: str) -> set[str]:
    lowered = text.replace(" ", "")
    labels = {
        profile["label"]
        for profile in _THEME_PROFILES
        if any(keyword.lower() in lowered.lower() for keyword in profile["keywords"])
    }
    return labels


def _merged_brief_text(instruction: str, materials: list[dict[str, object]]) -> str:
    parts = [instruction]
    for item in materials:
        parts.append(str(item.get("title", "") or ""))
        parts.append(_material_text(item))
    return "\n".join(part for part in parts if part).replace(" ", "")


def _summarize_theme(instruction: str, materials: list[dict[str, object]]) -> str:
    labels = Counter()
    merged_text = instruction + "\n" + "\n".join(_material_text(item) for item in materials)
    for label in _matched_labels(merged_text):
        labels[label] += 1
    if not labels:
        return "围绕核心进展提炼背景、做法与成效"
    mapping = {
        "small_micro": "提升小微企业金融服务质效",
        "foreign_trade": "服务稳外贸与外贸企业发展",
        "technology": "以科技创新提升金融服务能力",
        "consumer_protection": "提升金融消费者权益保护质效",
        "green": "推进绿色金融与可持续发展",
        "inclusive_service": "提升特殊群体金融服务可得性",
        "internal_activity": "提炼与经营管理相关的内部建设亮点",
    }
    top_label = labels.most_common(1)[0][0]
    return mapping.get(top_label, "围绕核心进展提炼背景、做法与成效")


def _select_sentences(materials: list[dict[str, object]], *, require_number: bool, limit: int) -> list[str]:
    sentences: list[str] = []
    seen: set[str] = set()
    for item in materials:
        for sentence in re.split(r"[。！？；\n]", _material_text(item)):
            cleaned = sentence.strip()
            if len(cleaned) < 12:
                continue
            if require_number and not re.search(r"\d", cleaned):
                continue
            normalized = re.sub(r"\s+", "", cleaned)
            if normalized in seen:
                continue
            seen.add(normalized)
            sentences.append(cleaned)
            if len(sentences) >= limit:
                return sentences
    return sentences


def _material_text(item: dict[str, object]) -> str:
    title = str(item.get("title", "") or "")
    text = str(item.get("text", "") or "")
    return f"{title}\n{text}".strip()


def _has_space_split_title(title: str) -> bool:
    if "，" in title or "：" in title:
        return False
    parts = [part for part in title.split() if part]
    if len(parts) < 2:
        return False
    chinese_parts = sum(1 for part in parts if re.search(r"[\u4e00-\u9fff]", part))
    return chinese_parts >= 2
