from pathlib import Path

from app.platform.tools import ToolGateway
from skills.research_synthesis.schema import ResearchSynthesisResult
from skills.writer1.workflow import _has_read_errors, _has_usable_materials, _source_materials


OUTLINE_FILENAME_MARKERS = ("提纲", "调研框架", "调查框架", "outline")


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
        *[{**item, "material_role": "source"} for item in source_materials],
    ]
    source_names = _material_names(ordered_materials)
    draft = tools.call(
        "llm_writer",
        {
            "skill_id": "research_synthesis",
            "task": "research_synthesis",
            "instruction": str(inputs.get("text", "") or ""),
            "materials": ordered_materials,
            "planning_note": _planning_note(outline, source_materials),
            "output_type": ResearchSynthesisResult,
        },
    )
    title = str(draft.get("title", "") or "")
    body = str(draft.get("body", "") or "")
    needs_clarification = bool(draft.get("needs_clarification", False)) and not (title.strip() or body.strip())
    if needs_clarification:
        return ResearchSynthesisResult(
            title=title,
            body=body,
            sources=source_names,
            needs_clarification=True,
            message=str(draft.get("message", "") or "请补充模型指出的缺失材料后再整合。"),
        )
    return ResearchSynthesisResult(
        title=title,
        body=body,
        sources=source_names,
        needs_clarification=False,
        message=str(draft.get("message", "") or "已按现成提纲完成综合调研材料初稿。"),
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
    if len(candidates) == 1:
        return candidates[0], ""
    if len(candidates) > 1:
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


def _outline_choice_message(materials: list[dict[str, object]], *, prefix: str) -> str:
    names = _material_names(materials)
    listing = "、".join(names) if names else "当前材料"
    return f"{prefix}。请明确回复哪一份是调研提纲，例如“{names[0] if names else 'XX.docx'} 是提纲”。当前文件：{listing}。"


def _planning_note(outline: dict[str, object], sources: list[dict[str, object]]) -> str:
    source_names = "、".join(_material_names(sources))
    return (
        f"调研提纲：{_material_name(outline)}\n"
        f"部门素材：{source_names}\n"
        "严格保留提纲层级、顺序和章节名称，不自行另起结构。\n"
        "逐章归集部门素材；重复事实合并，来源口径冲突时在对应位置标注“【口径待确认】”。\n"
        "提纲章节没有材料支撑时保留该章节并标注“【材料待补充】”，不得用空话或虚构事实填满。\n"
        "第一版以忠实整合为目标，只做必要的衔接和去重，不进行宣传化润色。"
    )


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


def _clarification(message: str) -> ResearchSynthesisResult:
    return ResearchSynthesisResult(
        title="",
        body="",
        sources=[],
        needs_clarification=True,
        message=message,
    )
