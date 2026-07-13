import re
from collections import Counter

from skills.writer1.schema import BriefCriticResult, BriefViolation


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
_BUSINESS_THEME_LABELS = {
    "small_micro",
    "foreign_trade",
    "technology",
    "consumer_protection",
    "green",
    "inclusive_service",
}


def build_brief_plan(instruction: str, materials: list[object], *, multi_source: bool) -> str:
    core_materials = [item for item in materials if isinstance(item, dict)]
    theme = _summarize_theme(instruction, core_materials)
    key_facts = _select_sentences(core_materials, require_number=False, limit=3)
    key_data = _select_sentences(core_materials, require_number=True, limit=2)
    lines = [
        f"写作类型：{'多素材简报' if multi_source else '单素材简报'}",
        "文体要求：不要沿用新闻稿或通稿写法，要改写为适合内部流转和领导阅读的简报体。",
        f"{'统一主题' if multi_source else '核心主线'}：{theme}",
        f"{'结构要求：围绕一个统一主题组织材料，明确主线与辅线，不能逐条拼接素材。' if multi_source else '结构要求：围绕一个核心主线展开，按背景、做法、成效组织正文，不要写成活动报道。'}",
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
    return "\n".join(lines)


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


def validate_brief_deterministic(title: str, body: str) -> list[BriefViolation]:
    violations: list[BriefViolation] = []
    violations.extend(check_brief_title_format(title))
    violations.extend(check_brief_subject_name(body))
    violations.extend(check_no_list_style(body))
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
