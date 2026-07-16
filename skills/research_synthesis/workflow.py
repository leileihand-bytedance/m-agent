import re
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

from app.platform.tools import ToolGateway
from skills.research_synthesis.docx_output import write_research_synthesis_docx
from skills.research_synthesis.schema import (
    ResearchEvidencePoint,
    ResearchPlanSection,
    ResearchSynthesisPlan,
    ResearchSynthesisResult,
)
from skills.writer1.workflow import _has_read_errors, _has_usable_materials, _source_materials


OUTLINE_FILENAME_MARKERS = ("提纲", "调研框架", "调查框架", "outline")
PRIMARY_OUTLINE_STEMS = ("调研提纲", "综合调研提纲", "调研框架", "调查框架", "outline")
RESPONSE_CONTENT_MARKERS = ("回复：", "回复:", "答复：", "答复:", "反馈内容", "我行", "本部门")


def run(inputs: dict[str, object], tools: ToolGateway) -> ResearchSynthesisResult:
    if list(inputs.get("urls") or []):
        return _clarification("综合调研整合第一版只读取本次上传的 Word、PDF、PPTX 文件和补充文字，暂不读取网页链接。请上传调研提纲和各部门素材文件。")

    materials = _source_materials(inputs=inputs, tools=tools)
    if not _has_usable_materials(materials):
        return _clarification("请至少上传 1 份调研提纲和 1 份部门素材。提纲文件名建议包含“提纲”二字，便于系统准确识别。")
    if _has_read_errors(materials):
        failed = _failed_material_names(materials)
        detail = f"：{'、'.join(failed)}" if failed else ""
        return _clarification(f"有文件未读到有效正文{detail}。为避免综合调研材料缺项，请重新发送可读取版本后再开始整合。")

    outline, outline_error = _select_outline(materials, str(inputs.get("text", "") or ""))
    if outline is None:
        return _clarification(outline_error)

    source_materials = [item for item in materials if item is not outline]
    if not source_materials:
        return _clarification("已识别调研提纲，但还没有可用于填充提纲的部门素材。请继续上传至少 1 份部门素材。")

    ordered_materials = [
        {**outline, "material_role": "outline"},
        *[
            {
                **item,
                "material_role": "source",
                "source_label": _source_label(item),
            }
            for item in source_materials
        ],
    ]
    source_names = _material_names(ordered_materials)
    plan_data = tools.call(
        "llm_writer",
        {
            "skill_id": "research_synthesis",
            "task": "research_synthesis_plan",
            "instruction": str(inputs.get("text", "") or ""),
            "materials": ordered_materials,
            "planning_note": _plan_stage_note(outline, source_materials),
            "prompt_path": "prompts/plan.md",
            "output_type": ResearchSynthesisPlan,
        },
    )
    plan = ResearchSynthesisPlan.model_validate(plan_data)
    source_labels = _unique_source_labels(source_materials)
    _validate_plan_evidence(plan, source_labels)
    if plan.needs_clarification or plan.outline_type == "unknown":
        return ResearchSynthesisResult(
            title=plan.title,
            body="",
            sources=source_names,
            needs_clarification=True,
            message=plan.message or "暂时无法判断提纲需要逐项覆盖，还是按相关性选择事项。请明确覆盖方式后再整合。",
        )
    if plan.coverage_mode == "selective" and not _expected_top_headings(plan, outline, source_labels):
        return ResearchSynthesisResult(
            title=plan.title,
            body="",
            sources=source_names,
            needs_clarification=True,
            message="当前材料没有可直接入稿的文字证据。请补充相关部门文字素材，或人工核对图片后再整合。",
        )

    draft = tools.call(
        "llm_writer",
        {
            "skill_id": "research_synthesis",
            "task": "research_synthesis",
            "instruction": str(inputs.get("text", "") or ""),
            "materials": ordered_materials,
            "planning_note": _drafting_note(plan, outline, source_materials),
            "output_type": ResearchSynthesisResult,
        },
    )
    title = _replace_raw_material_names(
        str(draft.get("title", "") or plan.title or ""),
        source_materials,
    )
    body = _normalize_draft_body(
        str(draft.get("body", "") or ""),
        outline=outline,
        sources=source_materials,
        plan=plan,
    )
    needs_clarification = bool(draft.get("needs_clarification", False)) and not (title.strip() or body.strip())
    if needs_clarification:
        return ResearchSynthesisResult(
            title=title,
            body=body,
            sources=source_names,
            needs_clarification=True,
            message=str(draft.get("message", "") or "请补充模型指出的缺失材料后再整合。"),
        )
    output_file = ""
    if str(inputs.get("output_dir", "") or "").strip():
        output_file = str(
            write_research_synthesis_docx(
                title=title,
                body=body,
                output_dir=str(inputs["output_dir"]),
            )
        )
    completion_message = f"已按1份提纲和{len(source_materials)}份部门素材生成综合调研 Word 初稿。"
    return ResearchSynthesisResult(
        title=title,
        body=body,
        sources=source_names,
        needs_clarification=False,
        message=completion_message,
        output_file=output_file,
    )


def _select_outline(
    materials: list[dict[str, object]],
    instruction: str,
) -> tuple[dict[str, object] | None, str]:
    explicitly_named = [item for item in materials if _material_named_in_instruction(item, instruction)]
    if len(explicitly_named) == 1:
        return explicitly_named[0], ""
    if len(explicitly_named) > 1:
        return None, _outline_choice_message(explicitly_named, prefix="说明中同时点到了多份可能的提纲")

    candidates = [item for item in materials if _looks_like_outline_filename(_material_name(item))]
    primary_candidates = [item for item in candidates if _looks_like_primary_outline_filename(_material_name(item))]
    if len(primary_candidates) == 1:
        return primary_candidates[0], ""
    if len(primary_candidates) > 1:
        return None, _outline_choice_message(primary_candidates, prefix="识别到多份名称明确的调研提纲")
    if len(candidates) == 1:
        return candidates[0], ""
    if len(candidates) > 1:
        unanswered_candidates = [item for item in candidates if not _contains_response_content(item)]
        if len(unanswered_candidates) == 1:
            return unanswered_candidates[0], ""
        return None, _outline_choice_message(candidates, prefix="识别到多份可能的调研提纲")
    return None, _outline_choice_message(materials, prefix="暂时无法确定哪一份是调研提纲")


def _material_named_in_instruction(item: dict[str, object], instruction: str) -> bool:
    if "提纲" not in instruction.lower() and "outline" not in instruction.lower():
        return False
    name = _material_name(item)
    stem = Path(name).stem
    return bool(name and name in instruction) or bool(len(stem) >= 2 and stem in instruction)


def _looks_like_outline_filename(filename: str) -> bool:
    normalized = filename.lower()
    return any(marker in normalized for marker in OUTLINE_FILENAME_MARKERS)


def _looks_like_primary_outline_filename(filename: str) -> bool:
    normalized_stem = re.sub(r"[\s_-]+", "", Path(filename).stem.lower())
    return normalized_stem in {re.sub(r"[\s_-]+", "", stem) for stem in PRIMARY_OUTLINE_STEMS}


def _contains_response_content(item: dict[str, object]) -> bool:
    text = str(item.get("text") or "")
    return any(marker in text for marker in RESPONSE_CONTENT_MARKERS)


def _outline_choice_message(materials: list[dict[str, object]], *, prefix: str) -> str:
    names = _material_names(materials)
    listing = "、".join(names) if names else "当前材料"
    return f"{prefix}。请明确回复哪一份是调研提纲，例如“{names[0] if names else 'XX.docx'} 是提纲”。当前文件：{listing}。"


def _plan_stage_note(outline: dict[str, object], sources: list[dict[str, object]]) -> str:
    source_names = "、".join(_material_names(sources))
    source_labels = "、".join(_source_label(item) for item in sources)
    reminders = _image_reminders(sources)
    image_note = (
        "图片提醒（必须原样放在对应材料所支撑的正文位置）：\n" + "\n".join(reminders) + "\n"
        if reminders
        else "本次未检测到需要保留的图片提醒。\n"
    )
    return (
        "先做材料台账，不写最终正文。先判断提纲类型和覆盖方式，再以提纲问题为中心，把不同部门的同类事实放进同一个 evidence point。\n"
        f"调研提纲：{_material_name(outline)}\n"
        f"部门素材：{source_names}\n"
        f"部门来源标签：{source_labels}\n"
        "问卷和报告骨架通常逐项覆盖；政策目录只有与本任务直接相关且有可用证据的事项才进入 selected_headings。\n"
        "逐项覆盖时保留必答主题和顺序；选择性覆盖时记录未采用事项及理由，不要把整份目录机械复制为正文。\n"
        "重复事实要跨部门合并；只有对象、时间、单位和口径一致时才能计算合计，并在 derivation_note 写清算式。\n"
        "逐项登记缺口和冲突，不得用空话、推断或虚构事实填满。\n"
        "台账只能使用上面的规范化部门来源标签，不要复制文件名或本机路径。\n"
        "不要读取、描述或猜测图片内容，也不要把图片插入汇总稿；只保留下列人工评估提醒。\n"
        f"{image_note}"
        "把图片提醒映射到其前后文字所对应的小节，连续同部门提醒可以合并计数。"
    )


def _drafting_note(
    plan: ResearchSynthesisPlan,
    outline: dict[str, object],
    sources: list[dict[str, object]],
) -> str:
    source_labels = _unique_source_labels(sources)
    image_counts = _image_reminder_counts(sources)
    image_note = "、".join(f"{label}{count}张" for label, count in image_counts.items()) or "无"
    return (
        "按提纲章节综合表达，不要再按部门分别堆叠。以下材料台账是本轮正文的直接写作依据：\n"
        f"{plan.model_dump_json(indent=2)}\n\n"
        f"提纲一级主题：{'、'.join(_outline_top_headings(str(outline.get('text') or '')))}\n"
        f"本次覆盖方式：{plan.coverage_mode}；正文采用主题：{'、'.join(_expected_top_headings(plan, outline, source_labels))}\n"
        f"正文允许使用的来源标签：{'、'.join(source_labels)}\n"
        f"图片核对总数：{image_note}。图片提醒要放在台账对应小节；连续提醒合并计数，不插入或描述图片。\n"
        "不可使用 usable=false 的证据；image_candidate 和 external_missing 只能作为人工核验提示，不能写成已确认事实。\n"
        "综合事实写成一个连贯段落，在段末只保留一次合并后的来源标签，例如“【来源：甲部、乙部】”。\n"
        "一级标题统一使用“一、”，二级标题统一使用“（一）”；明确的三级短标题可以保留“1.”，正文列举可使用“一是、二是”。\n"
        "不要撰写报告开头和结尾；Word 生成阶段会在开头和末尾加入待用户补充的备注。"
    )


def _image_reminders(materials: list[dict[str, object]]) -> list[str]:
    reminders: list[str] = []
    pattern = re.compile(r"【提醒：[^】]*素材含图片，请评估是否需要】")
    for item in materials:
        reminders.extend(pattern.findall(str(item.get("text") or "")))
    return reminders


def _image_reminder_counts(materials: list[dict[str, object]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    pattern = re.compile(r"【提醒：[^】]*素材含图片，请评估是否需要】")
    for item in materials:
        count = len(pattern.findall(str(item.get("text") or "")))
        if count:
            counts[_source_label(item)] += count
    return counts


def _failed_material_names(materials: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for item in materials:
        if item.get("source") != "read_error":
            continue
        name = str(item.get("failed_file") or item.get("failed_url") or "").strip()
        if name:
            names.append(name)
    return names


def _material_names(materials: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for item in materials:
        name = _material_name(item)
        if name and name not in names:
            names.append(name)
    return names


def _material_name(item: dict[str, object]) -> str:
    return str(item.get("title") or item.get("failed_file") or "未命名材料").strip()


def _source_label(item: dict[str, object]) -> str:
    reminder_match = re.search(r"【提醒：([^】]+?)素材含图片，请评估是否需要】", str(item.get("text") or ""))
    if reminder_match:
        return _clean_source_label(reminder_match.group(1))

    stem = Path(_material_name(item)).stem.strip()
    stem = re.sub(r"(?:素材|材料|文件)$", "", stem).strip(" _-")
    parts = [part for part in re.split(r"[-_—]+", stem) if part]
    suffix_pattern = re.compile(r"([\u4e00-\u9fff]{2,20}(?:项目组|办公室|中心|部门|部))$")
    for part in reversed(parts or [stem]):
        cleaned_part = re.sub(r"^\d{6,14}", "", part).strip()
        match = suffix_pattern.search(cleaned_part)
        if match:
            return _clean_source_label(match.group(1))

    for part_index, part in enumerate(parts or [stem]):
        cleaned_part = re.sub(r"[（(].*$", "", part).strip()
        if re.fullmatch(r"[\u4e00-\u9fff]{2,12}", cleaned_part) and not any(
            marker in cleaned_part for marker in ("提纲", "反馈", "汇总", "附件", "材料")
        ):
            if cleaned_part.endswith("金融"):
                return cleaned_part + "部"
            remaining_name = "".join(parts[part_index + 1 :])
            if remaining_name and any(marker in remaining_name for marker in ("提纲", "反馈", "汇总")):
                return cleaned_part + "部"
            return cleaned_part
    return _clean_source_label(stem) or "未命名部门"


def _clean_source_label(label: str) -> str:
    clean = re.sub(r"^\d{6,14}", "", label).strip(" _-—")
    clean = re.sub(r"(?:素材|材料|文件)$", "", clean).strip()
    return clean


def _unique_source_labels(materials: list[dict[str, object]]) -> list[str]:
    labels: list[str] = []
    for item in materials:
        label = _source_label(item)
        if label and label not in labels:
            labels.append(label)
    return labels


def _normalize_draft_body(
    body: str,
    *,
    outline: dict[str, object],
    sources: list[dict[str, object]],
    plan: ResearchSynthesisPlan,
) -> str:
    clean = _replace_raw_material_names(body, sources)
    allowed_labels = _unique_source_labels(sources)
    top_headings = _expected_top_headings(plan, outline, allowed_labels)
    allowed_numbers = _allowed_numeric_tokens(plan, allowed_labels)
    lines = [line.strip() for line in clean.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
    lines = _filter_unselected_sections(lines, plan=plan, outline=outline, expected_headings=top_headings)
    normalized_lines = [_normalize_heading_line(line, top_headings) for line in lines]
    normalized_lines = _ensure_outline_headings(normalized_lines, top_headings)
    normalized_lines = [_normalize_source_tags(line, allowed_labels) for line in normalized_lines]
    normalized_lines = [_ensure_fact_attribution(line) for line in normalized_lines]
    normalized_lines = [_mark_untraceable_numbers(line, allowed_numbers) for line in normalized_lines]
    normalized_lines = _collapse_consecutive_image_reminders(normalized_lines, allowed_labels)
    normalized_lines = _ensure_image_reminders(
        normalized_lines,
        expected_counts=_image_reminder_counts(sources),
    )
    return "\n".join(normalized_lines).strip()


def _expected_top_headings(
    plan: ResearchSynthesisPlan,
    outline: dict[str, object],
    allowed_labels: list[str] | None = None,
) -> list[str]:
    outline_headings = _outline_top_headings(str(outline.get("text") or ""))
    if plan.coverage_mode != "selective":
        return _unique_headings(outline_headings or plan.required_headings or plan.selected_headings)

    selected = plan.selected_headings or [section.heading for section in plan.sections]
    if not plan.sections:
        return _unique_headings(selected)
    usable_section_headings = [
        section.heading for section in plan.sections if _section_has_usable_evidence(section, allowed_labels)
    ]
    return _unique_headings(
        heading
        for heading in selected
        if any(_headings_match(heading, usable_heading) for usable_heading in usable_section_headings)
    )


def _unique_headings(headings: Iterable[str]) -> list[str]:
    unique: list[str] = []
    for heading in headings:
        clean = _clean_outline_heading(str(heading))
        if clean and clean not in unique:
            unique.append(clean)
    return unique


def _section_has_usable_evidence(
    section: ResearchPlanSection,
    allowed_labels: list[str] | None = None,
) -> bool:
    return any(
        _evidence_point_is_usable(point, allowed_labels)
        for subsection in section.subsections
        for point in subsection.evidence_points
    )


def _usable_evidence_points(
    plan: ResearchSynthesisPlan,
    allowed_labels: list[str] | None = None,
) -> Iterator[ResearchEvidencePoint]:
    for section in plan.sections:
        for subsection in section.subsections:
            for point in subsection.evidence_points:
                if _evidence_point_is_usable(point, allowed_labels):
                    yield point


def _allowed_numeric_tokens(plan: ResearchSynthesisPlan, allowed_labels: list[str]) -> set[str]:
    tokens: set[str] = set()
    for point in _usable_evidence_points(plan, allowed_labels):
        tokens.update(_numeric_tokens(point.content))
        if point.evidence_kind == "derived":
            tokens.update(_numeric_tokens(point.derivation_note))
    return tokens


def _validate_plan_evidence(plan: ResearchSynthesisPlan, allowed_labels: list[str]) -> None:
    for section in plan.sections:
        for subsection in section.subsections:
            for point in subsection.evidence_points:
                if not point.usable or _evidence_point_has_valid_sources(point, allowed_labels):
                    continue
                point.usable = False
                if not point.verification_note:
                    point.verification_note = "来源标签无法匹配本次上传材料，需人工核对。"


def _evidence_point_is_usable(
    point: ResearchEvidencePoint,
    allowed_labels: list[str] | None = None,
) -> bool:
    if not point.usable:
        return False
    if allowed_labels is None:
        return bool(point.source_labels)
    return _evidence_point_has_valid_sources(point, allowed_labels)


def _evidence_point_has_valid_sources(point: ResearchEvidencePoint, allowed_labels: list[str]) -> bool:
    return bool(point.source_labels) and all(
        _match_allowed_source_label(label, allowed_labels) for label in point.source_labels
    )


def _filter_unselected_sections(
    lines: list[str],
    *,
    plan: ResearchSynthesisPlan,
    outline: dict[str, object],
    expected_headings: list[str],
) -> list[str]:
    if plan.coverage_mode != "selective":
        return lines

    possible_headings = _unique_headings(
        [
            *_outline_top_headings(str(outline.get("text") or "")),
            *plan.required_headings,
            *plan.selected_headings,
            *(section.heading for section in plan.sections),
            *(re.split(r"[:：]", item, maxsplit=1)[0] for item in plan.omitted_outline_items),
        ]
    )
    filtered: list[str] = []
    keep_section = True
    for line in lines:
        heading = _numbered_heading_text(line)
        if heading and any(_headings_match(heading, candidate) for candidate in possible_headings):
            keep_section = any(_headings_match(heading, expected) for expected in expected_headings)
        if keep_section:
            filtered.append(line)
    return filtered


def _numbered_heading_text(line: str) -> str:
    chinese = re.match(r"^[一二三四五六七八九十百零〇]+、\s*(.+)$", line)
    if chinese:
        return _clean_outline_heading(chinese.group(1))
    arabic = re.match(r"^\d+[.．、]\s*(.+)$", line)
    if arabic:
        return _clean_outline_heading(arabic.group(1))
    return ""


def _ensure_outline_headings(lines: list[str], top_headings: list[str]) -> list[str]:
    complete = list(lines)
    expected = [f"{_chinese_number(index)}、{heading}" for index, heading in enumerate(top_headings, start=1)]
    for index, heading in enumerate(expected):
        if heading in complete:
            continue
        next_heading = next((candidate for candidate in expected[index + 1 :] if candidate in complete), "")
        insert_at = complete.index(next_heading) if next_heading else len(complete)
        complete[insert_at:insert_at] = [
            heading,
            "【材料待补充：该提纲主题未在模型初稿中形成内容，请人工核对。】",
        ]
    return complete


def _replace_raw_material_names(text: str, sources: list[dict[str, object]]) -> str:
    clean = text
    replacements: list[tuple[str, str]] = []
    for item in sources:
        name = _material_name(item)
        stem = Path(name).stem
        label = _source_label(item)
        replacements.extend(((name, label), (stem, label)))
    for raw, label in sorted(replacements, key=lambda pair: len(pair[0]), reverse=True):
        if raw and raw != label:
            clean = clean.replace(raw, label)
    return clean


def _outline_top_headings(text: str) -> list[str]:
    lines = [line.strip() for line in text.replace("\r", "\n").split("\n") if line.strip()]
    chinese_headings: list[str] = []
    for line in lines:
        match = re.match(r"^[一二三四五六七八九十百零〇]+、\s*(.+)$", line)
        if match:
            chinese_headings.append(_clean_outline_heading(match.group(1)))
    if chinese_headings:
        return chinese_headings

    headings: list[str] = []
    expected_number = 1
    for line in lines:
        match = re.match(r"^(\d+)[.．、]\s*(.+)$", line)
        if not match or int(match.group(1)) != expected_number:
            continue
        headings.append(_clean_outline_heading(match.group(2)))
        expected_number += 1
    return headings


def _clean_outline_heading(text: str) -> str:
    clean = re.split(r"[—－-]{2,}\s*(?:牵头|责任|负责)部门", text, maxsplit=1)[0]
    clean = re.sub(r"(?:牵头|责任|负责)部门\s*[:：].*$", "", clean)
    return clean.strip().rstrip("。；;：:").strip()


def _normalize_heading_line(line: str, top_headings: list[str]) -> str:
    chinese_top = re.match(r"^([一二三四五六七八九十百零〇]+)、\s*(.+)$", line)
    if chinese_top:
        return f"{chinese_top.group(1)}、{_clean_outline_heading(chinese_top.group(2))}"

    arabic = re.match(r"^(\d+)[.．、]\s*(.+)$", line)
    if arabic:
        number = int(arabic.group(1))
        heading = _clean_outline_heading(arabic.group(2))
        if 1 <= number <= len(top_headings) and _headings_match(heading, top_headings[number - 1]):
            return f"{_chinese_number(number)}、{top_headings[number - 1]}"
        return f"{number}.{arabic.group(2).strip()}"

    chinese_sub = re.match(r"^（([一二三四五六七八九十百零〇]+)）\s*(.+)$", line)
    if chinese_sub:
        return f"（{chinese_sub.group(1)}）{_clean_outline_heading(chinese_sub.group(2))}"

    arabic_sub = re.match(r"^[（(](\d+)[）)]\s*(.+)$", line)
    if arabic_sub:
        number = int(arabic_sub.group(1))
        content = arabic_sub.group(2).strip()
        if len(content) <= 40 and not content.endswith(("。", "；", ";")):
            return f"（{_chinese_number(number)}）{_clean_outline_heading(content)}"
        if 1 <= number <= 10:
            return f"{_chinese_number(number)}是{content}"
    return line


def _headings_match(left: str, right: str) -> bool:
    normalize = lambda value: re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value)
    left_key = normalize(left)
    right_key = normalize(right)
    return bool(left_key and right_key and (left_key == right_key or left_key.startswith(right_key) or right_key.startswith(left_key)))


def _chinese_number(number: int) -> str:
    digits = "零一二三四五六七八九"
    if number < 10:
        return digits[number]
    if number == 10:
        return "十"
    if number < 20:
        return "十" + digits[number % 10]
    tens, ones = divmod(number, 10)
    return digits[tens] + "十" + (digits[ones] if ones else "")


def _normalize_source_tags(line: str, allowed_labels: list[str]) -> str:
    matches = re.findall(r"【来源：([^】]+)】", line)
    if not matches:
        return line
    labels: list[str] = []
    for match in matches:
        for raw_label in re.split(r"[、,，/；;]+", match):
            label = _match_allowed_source_label(raw_label, allowed_labels)
            if label and label not in labels:
                labels.append(label)
    text = re.sub(r"\s*【来源：[^】]+】\s*", "", line).strip()
    if not text or text.startswith(("一、", "二、", "三、", "四、", "五、", "六、", "七、", "八、", "九、", "十、", "（")):
        return text
    tag = f"【来源：{'、'.join(labels)}】" if labels else "【来源：待核对】"
    return f"{text.rstrip()}{tag}"


def _ensure_fact_attribution(line: str) -> str:
    if _is_structural_or_annotation_line(line) or "【来源：" in line:
        return line
    return f"{line.rstrip()}【来源：待核对】"


def _mark_untraceable_numbers(line: str, allowed_numbers: set[str]) -> str:
    if _is_structural_or_annotation_line(line):
        return line
    present = _numeric_tokens(line)
    if not present or present.issubset(allowed_numbers):
        return line
    warning = "【来源待核对：该段包含材料台账未登记的数据，请人工核对。】"
    return line if warning in line else f"{line.rstrip()}{warning}"


def _numeric_tokens(text: str) -> set[str]:
    raw_tokens = re.findall(r"(?<![A-Za-z0-9])\d[\d,，]*(?:\.\d+)?", text)
    return {_normalize_numeric_token(token) for token in raw_tokens}


def _normalize_numeric_token(token: str) -> str:
    clean = token.replace(",", "").replace("，", "")
    if "." in clean:
        integer, decimal = clean.split(".", maxsplit=1)
        integer = integer.lstrip("0") or "0"
        decimal = decimal.rstrip("0") or "0"
        return f"{integer}.{decimal}"
    return clean.lstrip("0") or "0"


def _is_structural_or_annotation_line(line: str) -> bool:
    if line.startswith("【"):
        return True
    if re.match(r"^[一二三四五六七八九十百零〇]+、", line):
        return True
    if re.match(r"^（[一二三四五六七八九十百零〇]+）", line):
        return True
    return bool(re.match(r"^\d+[.．、]\s*[^。；;]{1,40}$", line))


def _match_allowed_source_label(raw_label: str, allowed_labels: list[str]) -> str:
    clean = _clean_source_label(raw_label)
    for label in allowed_labels:
        if clean == label:
            return label
    for label in allowed_labels:
        if label in clean or clean in label:
            return label
    return ""


def _collapse_consecutive_image_reminders(lines: list[str], allowed_labels: list[str]) -> list[str]:
    pattern = re.compile(r"^【提醒：([^】]+?)(?:素材)?含图片，请评估是否需要】$")
    collapsed: list[str] = []
    index = 0
    while index < len(lines):
        match = pattern.match(lines[index])
        if not match:
            collapsed.append(lines[index])
            index += 1
            continue
        raw_label = match.group(1)
        label = _match_allowed_source_label(raw_label, allowed_labels) or _clean_source_label(raw_label)
        count = 1
        while index + count < len(lines):
            next_match = pattern.match(lines[index + count])
            if not next_match:
                break
            next_label = _match_allowed_source_label(next_match.group(1), allowed_labels) or _clean_source_label(next_match.group(1))
            if next_label != label:
                break
            count += 1
        collapsed.append(f"【图片提醒：{label}本节素材包含{count}张图片，请评估是否需要】")
        index += count
    return collapsed


def _ensure_image_reminders(lines: list[str], *, expected_counts: Counter[str]) -> list[str]:
    complete = list(lines)
    for label, count in expected_counts.items():
        covered = _covered_image_count(complete, label)
        if covered >= count:
            continue
        missing = count - covered
        reminder = (
            f"【图片提醒：{label}素材共含{count}张图片，请结合原材料位置评估是否需要】"
            if covered == 0
            else f"【图片提醒：{label}素材另有{missing}张图片未在正文中定位，请结合原材料评估是否需要】"
        )
        insert_at = next(
            (index + 1 for index, line in enumerate(complete) if f"【来源：{label}" in line or f"、{label}】" in line),
            len(complete),
        )
        complete.insert(insert_at, reminder)
    return complete


def _covered_image_count(lines: list[str], label: str) -> int:
    count = 0
    escaped_label = re.escape(label)
    patterns = (
        re.compile(rf"^【图片提醒：{escaped_label}本节素材包含(\d+)张图片"),
        re.compile(rf"^【图片提醒：{escaped_label}素材共含(\d+)张图片"),
    )
    for line in lines:
        matched = None
        for pattern in patterns:
            matched = pattern.match(line)
            if matched:
                break
        if matched:
            count += int(matched.group(1))
        elif line.startswith(f"【提醒：{label}"):
            count += 1
    return count


def _clarification(message: str) -> ResearchSynthesisResult:
    return ResearchSynthesisResult(
        title="",
        body="",
        sources=[],
        needs_clarification=True,
        message=message,
    )
