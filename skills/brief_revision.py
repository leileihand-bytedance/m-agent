from __future__ import annotations

from collections import Counter
import re

from skills.writer1.schema import BriefRevisionPlanResult, BriefViolation


_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_NUMBER_TOKEN = re.compile(
    r"\d+(?:\.\d+)?(?:\+|余|多)?(?:%|％|万|亿|千|百)?"
    r"(?:亿元|万元|万户|万家|元|户|家|项|个|人|次|年|月|日|级|倍|场景)?"
)


def build_revision_plan(
    request: str,
    *,
    previous_title: str,
    previous_body: str,
    tools: object | None = None,
    skill_id: str = "",
) -> BriefRevisionPlanResult:
    local = _local_revision_plan(request)
    semantic = _semantic_revision_plan(
        request=request,
        previous_title=previous_title,
        previous_body=previous_body,
        tools=tools,
        skill_id=skill_id,
    )
    if semantic is None:
        return local

    explicit_paragraphs = _target_paragraphs(request)
    title_only = _is_title_only(request)
    target_length = _target_length(request)
    preserve_facts = _preserve_facts_and_numbers(request)
    must_remove = _must_remove(request)
    if title_only:
        semantic.scope = "title"
        semantic.target_paragraphs = []
        semantic.preserve_title = False
        semantic.preserve_other_paragraphs = True
    elif explicit_paragraphs:
        semantic.scope = "paragraph"
        semantic.target_paragraphs = explicit_paragraphs
        semantic.preserve_title = not _requests_title_change(request)
        semantic.preserve_other_paragraphs = True
    if target_length is not None:
        semantic.target_length = target_length
    if preserve_facts:
        semantic.preserve_facts_and_numbers = True
    if must_remove:
        semantic.must_remove = list(dict.fromkeys([*semantic.must_remove, *must_remove]))
    return semantic


def render_revision_plan(plan: BriefRevisionPlanResult) -> str:
    scope = {
        "title": "只修改标题",
        "paragraph": "只修改指定段落",
        "whole": "允许整体调整",
    }[plan.scope]
    lines = [
        "改稿执行约束：",
        f"- 修改范围：{scope}",
        f"- 指定段落：{'、'.join(str(item) for item in plan.target_paragraphs) or '无'}",
        f"- 标题保持不变：{'是' if plan.preserve_title else '否'}",
        f"- 未点名段落逐字保持：{'是' if plan.preserve_other_paragraphs else '否'}",
        f"- 事实和数字保持：{'是' if plan.preserve_facts_and_numbers else '否'}",
    ]
    if plan.target_length is not None:
        lines.append(f"- 目标篇幅：约{plan.target_length}字")
    if plan.required_changes:
        lines.append("- 必须执行：" + "；".join(plan.required_changes))
    if plan.must_remove:
        lines.append("- 必须删除：" + "；".join(plan.must_remove))
    return "\n".join(lines)


def apply_revision_constraints(
    plan: BriefRevisionPlanResult,
    *,
    previous_title: str,
    previous_body: str,
    generated_title: str,
    generated_body: str,
) -> tuple[str, str]:
    if plan.scope == "title":
        return generated_title, previous_body

    title = previous_title if plan.preserve_title else generated_title
    if plan.scope != "paragraph" or not plan.preserve_other_paragraphs:
        return title, generated_body

    previous_paragraphs = _paragraphs(previous_body)
    generated_paragraphs = _paragraphs(generated_body)
    if not previous_paragraphs:
        return title, generated_body
    merged = list(previous_paragraphs)
    for paragraph_number in plan.target_paragraphs:
        index = paragraph_number - 1
        if index < 0 or index >= len(previous_paragraphs):
            continue
        if index < len(generated_paragraphs):
            merged[index] = generated_paragraphs[index]
        elif generated_body.strip():
            merged[index] = generated_body.strip()
    return title, "\n\n".join(merged)


def validate_revision_result(
    plan: BriefRevisionPlanResult,
    *,
    previous_title: str,
    previous_body: str,
    revised_title: str,
    revised_body: str,
) -> list[BriefViolation]:
    violations: list[BriefViolation] = []
    if plan.preserve_title and revised_title.strip() != previous_title.strip():
        violations.append(
            BriefViolation(
                rule="revision-title-preservation",
                severity="hard",
                message="用户未要求修改标题，但标题发生了变化。",
                suggestion="恢复上一版标题，只执行本轮点名的修改。",
            )
        )
    if plan.target_length is not None:
        count = len(re.sub(r"[\s*_#]", "", revised_body))
        tolerance = max(30, int(plan.target_length * 0.05))
        if abs(count - plan.target_length) > tolerance:
            violations.append(
                BriefViolation(
                    rule="revision-target-length",
                    severity="hard",
                    message=f"用户要求约{plan.target_length}字，当前正文约{count}字。",
                    suggestion="在不改变关键事实和数据的前提下继续压缩或补足到目标区间。",
                )
            )
    if plan.preserve_facts_and_numbers:
        before = Counter(_NUMBER_TOKEN.findall(re.sub(r"\s+", "", previous_body)))
        after = Counter(_NUMBER_TOKEN.findall(re.sub(r"\s+", "", revised_body)))
        if before != after:
            violations.append(
                BriefViolation(
                    rule="revision-number-preservation",
                    severity="hard",
                    message="用户要求保留事实和数据，但改稿前后的数字口径发生了变化。",
                    suggestion="恢复上一版数字及单位，只调整表达和结构。",
                )
            )
    remaining = [item for item in plan.must_remove if item and item in revised_body]
    if remaining:
        violations.append(
            BriefViolation(
                rule="revision-removal",
                severity="hard",
                message="用户明确要求删除的内容仍保留在改稿中。",
                suggestion="删除指定内容，并检查上下文衔接。",
            )
        )
    return violations


def _semantic_revision_plan(
    *,
    request: str,
    previous_title: str,
    previous_body: str,
    tools: object | None,
    skill_id: str,
) -> BriefRevisionPlanResult | None:
    if tools is None or not skill_id:
        return None
    try:
        result = tools.call(
            "llm_planner",
            {
                "task": f"{skill_id}_revision_plan",
                "skill_id": skill_id,
                "output_type": BriefRevisionPlanResult,
                "prompt_path": "prompts/revision-plan.md",
                "instruction": request,
                "materials": [
                    {
                        "title": previous_title,
                        "text": previous_body,
                        "source": "previous_draft",
                    }
                ],
            },
        )
        return BriefRevisionPlanResult.model_validate(result)
    except Exception:
        return None


def _local_revision_plan(request: str) -> BriefRevisionPlanResult:
    paragraphs = _target_paragraphs(request)
    title_only = _is_title_only(request)
    if title_only:
        scope = "title"
    elif paragraphs:
        scope = "paragraph"
    else:
        scope = "whole"
    return BriefRevisionPlanResult(
        scope=scope,
        target_paragraphs=paragraphs,
        preserve_title=scope == "paragraph" and not _requests_title_change(request),
        preserve_other_paragraphs=scope in {"title", "paragraph"},
        target_length=_target_length(request),
        preserve_facts_and_numbers=_preserve_facts_and_numbers(request),
        required_changes=[request.strip()] if request.strip() else [],
        must_remove=_must_remove(request),
    )


def _target_paragraphs(request: str) -> list[int]:
    values: list[int] = []
    for raw in re.findall(r"第([一二三四五六七八九十\d]+)段", request):
        value = int(raw) if raw.isdigit() else _CHINESE_NUMBERS.get(raw)
        if value and value not in values:
            values.append(value)
    return values


def _is_title_only(request: str) -> bool:
    compact = re.sub(r"\s+", "", request)
    return "只改标题" in compact or (
        "标题" in compact and any(marker in compact for marker in ("正文不动", "正文不要动", "其他不变", "其余不变"))
    )


def _requests_title_change(request: str) -> bool:
    return "标题" in request and any(marker in request for marker in ("改", "调整", "换", "优化"))


def _target_length(request: str) -> int | None:
    match = re.search(r"(?:压缩到|控制在|改成|调整为|约)\s*(\d{3,4})\s*字", request)
    if match is None:
        return None
    return min(1200, max(100, int(match.group(1))))


def _preserve_facts_and_numbers(request: str) -> bool:
    compact = re.sub(r"\s+", "", request)
    return any(
        marker in compact
        for marker in (
            "不要改变事实和数据",
            "不改变事实和数据",
            "保留事实和数据",
            "数据不要动",
            "数字不要动",
        )
    )


def _must_remove(request: str) -> list[str]:
    values = re.findall(r"(?:删掉|删除|去掉)[“\"']([^”\"']+)[”\"']", request)
    return [item.strip() for item in values if item.strip()]


def _paragraphs(body: str) -> list[str]:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", body) if item.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    lines = [item.strip() for item in body.splitlines() if item.strip()]
    return lines or ([body.strip()] if body.strip() else [])
