from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.platform.tools import ToolGateway


RevisionScope = Literal["title", "paragraph", "whole"]

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


class RevisionPlan(BaseModel):
    """All revision-capable skills consume the same structured edit contract."""

    scope: RevisionScope = "whole"
    target_paragraphs: list[int] = Field(default_factory=list)
    preserve_title: bool = False
    preserve_other_paragraphs: bool = False
    target_length: int | None = Field(default=None, ge=1, le=20_000)
    preserve_facts_and_numbers: bool = False
    required_changes: list[str] = Field(default_factory=list)
    must_remove: list[str] = Field(default_factory=list)


class RevisionViolation(BaseModel):
    rule: str
    severity: str = "hard"
    message: str
    suggestion: str = ""


@dataclass(frozen=True)
class RevisionPolicy:
    """Skill adapter settings; business writing rules do not belong here."""

    supports_title: bool = True
    min_target_length: int = 1
    max_target_length: int = 20_000

    def __post_init__(self) -> None:
        if self.min_target_length < 1:
            raise ValueError("改稿目标字数下限必须大于 0")
        if self.max_target_length < self.min_target_length:
            raise ValueError("改稿目标字数上限不能小于下限")


@dataclass(frozen=True)
class PreparedRevision:
    skill_id: str
    request: str
    previous_title: str
    previous_body: str
    sources: tuple[str, ...]
    plan: RevisionPlan
    payload: dict[str, object]
    policy: RevisionPolicy

    def render_constraints(self) -> str:
        return render_revision_plan(self.plan)

    def apply(
        self,
        *,
        generated_title: str,
        generated_body: str,
    ) -> tuple[str, str]:
        return apply_revision_constraints(
            self.plan,
            previous_title=self.previous_title,
            previous_body=self.previous_body,
            generated_title=generated_title,
            generated_body=generated_body,
            supports_title=self.policy.supports_title,
        )

    def validate(
        self,
        *,
        revised_title: str,
        revised_body: str,
    ) -> list[RevisionViolation]:
        return validate_revision_result(
            self.plan,
            previous_title=self.previous_title,
            previous_body=self.previous_body,
            revised_title=revised_title,
            revised_body=revised_body,
            supports_title=self.policy.supports_title,
        )


class RevisionEngine:
    """Prepare, constrain and validate revisions independently of document type."""

    def __init__(self, *, policy: RevisionPolicy | None = None) -> None:
        self.policy = policy or RevisionPolicy()

    def prepare(
        self,
        inputs: dict[str, object],
        *,
        skill_id: str,
        tools: ToolGateway | None = None,
    ) -> PreparedRevision:
        request = str(
            inputs.get("revision_request", "") or inputs.get("text", "")
        ).strip()
        previous_title = str(inputs.get("previous_title", "") or "").strip()
        previous_body = str(inputs.get("previous_body", "") or "").strip()
        plan = self._build_plan(
            request=request,
            previous_title=previous_title,
            previous_body=previous_body,
            skill_id=skill_id,
            tools=tools,
        )
        instruction = _build_revision_instruction(
            inputs=inputs,
            request=request,
            plan=plan,
        )
        sources = tuple(
            str(item).strip()
            for item in list(inputs.get("previous_sources") or [])
            if str(item).strip()
        )
        return PreparedRevision(
            skill_id=skill_id,
            request=request,
            previous_title=previous_title,
            previous_body=previous_body,
            sources=sources,
            plan=plan,
            payload={
                "skill_id": skill_id,
                "task": skill_id,
                "instruction": instruction,
                "revision": True,
                "revision_request": request,
                "previous_job_id": str(inputs.get("previous_job_id", "")),
                "materials": [
                    previous_draft_material(
                        previous_title=previous_title,
                        previous_body=previous_body,
                    )
                ],
            },
            policy=self.policy,
        )

    def _build_plan(
        self,
        *,
        request: str,
        previous_title: str,
        previous_body: str,
        skill_id: str,
        tools: ToolGateway | None,
    ) -> RevisionPlan:
        local = _local_revision_plan(request, policy=self.policy)
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
        title_only = self.policy.supports_title and _is_title_only(request)
        target_length = _target_length(request, policy=self.policy)
        preserve_facts = _preserve_facts_and_numbers(request)
        must_remove = _must_remove(request)

        semantic = _normalize_semantic_plan(semantic, policy=self.policy)
        if title_only:
            semantic.scope = "title"
            semantic.target_paragraphs = []
            semantic.preserve_title = False
            semantic.preserve_other_paragraphs = True
        elif explicit_paragraphs:
            semantic.scope = "paragraph"
            semantic.target_paragraphs = explicit_paragraphs
            semantic.preserve_title = (
                self.policy.supports_title and not _requests_title_change(request)
            )
            semantic.preserve_other_paragraphs = True
        if target_length is not None:
            semantic.target_length = target_length
        if preserve_facts:
            semantic.preserve_facts_and_numbers = True
        if must_remove:
            semantic.must_remove = list(
                dict.fromkeys([*semantic.must_remove, *must_remove])
            )
        if request and not semantic.required_changes:
            semantic.required_changes = [request]
        return semantic


def previous_draft_material(
    *,
    previous_title: str,
    previous_body: str,
) -> dict[str, object]:
    return {
        "title": f"上一稿：{previous_title}" if previous_title else "上一稿",
        "text": f"标题：{previous_title}\n\n正文：{previous_body}".strip(),
        "url": "",
        "source": "previous_draft",
    }


def previous_sources(inputs: dict[str, object]) -> list[str]:
    return [
        str(item).strip()
        for item in list(inputs.get("previous_sources") or [])
        if str(item).strip()
    ]


def render_revision_plan(plan: RevisionPlan) -> str:
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
        (
            "- 未被点名的段落必须原样保留："
            f"{'是' if plan.preserve_other_paragraphs else '否'}"
        ),
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
    plan: RevisionPlan,
    *,
    previous_title: str,
    previous_body: str,
    generated_title: str,
    generated_body: str,
    supports_title: bool = True,
) -> tuple[str, str]:
    if not supports_title:
        title = previous_title
    elif plan.scope == "title":
        return generated_title, previous_body
    else:
        title = previous_title if plan.preserve_title else generated_title

    if plan.scope != "paragraph" or not plan.preserve_other_paragraphs:
        return title, generated_body

    previous_paragraphs = _paragraphs(previous_body)
    generated_paragraphs = _paragraphs(generated_body)
    target_indexes = [
        number - 1
        for number in plan.target_paragraphs
        if 0 < number <= len(previous_paragraphs)
    ]
    if not previous_paragraphs or not target_indexes:
        return title, generated_body

    merged = list(previous_paragraphs)
    compact_target_output = (
        len(generated_paragraphs) == len(target_indexes)
        and len(generated_paragraphs) != len(previous_paragraphs)
    )
    for target_position, index in enumerate(target_indexes):
        if compact_target_output:
            replacement = generated_paragraphs[target_position]
        elif index < len(generated_paragraphs):
            replacement = generated_paragraphs[index]
        elif len(target_indexes) == 1 and generated_body.strip():
            replacement = generated_body.strip()
        else:
            continue
        merged[index] = replacement
    return title, "\n\n".join(merged)


def validate_revision_result(
    plan: RevisionPlan,
    *,
    previous_title: str,
    previous_body: str,
    revised_title: str,
    revised_body: str,
    supports_title: bool = True,
) -> list[RevisionViolation]:
    violations: list[RevisionViolation] = []
    if (
        supports_title
        and plan.preserve_title
        and revised_title.strip() != previous_title.strip()
    ):
        violations.append(
            RevisionViolation(
                rule="revision-title-preservation",
                message="用户未要求修改标题，但标题发生了变化。",
                suggestion="恢复上一版标题，只执行本轮点名的修改。",
            )
        )
    if plan.scope == "paragraph" and plan.preserve_other_paragraphs:
        previous_paragraphs = _paragraphs(previous_body)
        revised_paragraphs = _paragraphs(revised_body)
        targets = {item - 1 for item in plan.target_paragraphs}
        changed = [
            index
            for index, paragraph in enumerate(previous_paragraphs)
            if index not in targets
            and (
                index >= len(revised_paragraphs)
                or revised_paragraphs[index].strip() != paragraph.strip()
            )
        ]
        if changed:
            violations.append(
                RevisionViolation(
                    rule="revision-scope-preservation",
                    message="用户未点名的段落发生了变化。",
                    suggestion="恢复未点名段落，只修改指定位置。",
                )
            )
    if plan.target_length is not None:
        count = len(re.sub(r"[\s*_#]", "", revised_body))
        tolerance = max(30, int(plan.target_length * 0.05))
        if abs(count - plan.target_length) > tolerance:
            violations.append(
                RevisionViolation(
                    rule="revision-target-length",
                    message=f"用户要求约{plan.target_length}字，当前正文约{count}字。",
                    suggestion="在不改变关键事实和数据的前提下继续压缩或补足到目标区间。",
                )
            )
    if plan.preserve_facts_and_numbers:
        before = Counter(_NUMBER_TOKEN.findall(re.sub(r"\s+", "", previous_body)))
        after = Counter(_NUMBER_TOKEN.findall(re.sub(r"\s+", "", revised_body)))
        if before != after:
            violations.append(
                RevisionViolation(
                    rule="revision-number-preservation",
                    message="用户要求保留事实和数据，但改稿前后的数字口径发生了变化。",
                    suggestion="恢复上一版数字及单位，只调整表达和结构。",
                )
            )
    revised_text = f"{revised_title}\n{revised_body}"
    remaining = [item for item in plan.must_remove if item and item in revised_text]
    if remaining:
        violations.append(
            RevisionViolation(
                rule="revision-removal",
                message="用户明确要求删除的内容仍保留在改稿中。",
                suggestion="删除指定内容，并检查上下文衔接。",
            )
        )
    return violations


def format_revision_violations(violations: list[RevisionViolation]) -> str:
    lines = ["本轮改稿未完全执行用户要求，请逐项修正："]
    for index, violation in enumerate(violations, 1):
        lines.append(
            f"{index}. {violation.message}（{violation.rule}）\n"
            f"   修改建议：{violation.suggestion}"
        )
    return "\n".join(lines)


def _semantic_revision_plan(
    *,
    request: str,
    previous_title: str,
    previous_body: str,
    tools: ToolGateway | None,
    skill_id: str,
) -> RevisionPlan | None:
    if tools is None or not skill_id:
        return None
    try:
        result = tools.call(
            "llm_planner",
            {
                "task": f"{skill_id}_revision_plan",
                "skill_id": skill_id,
                "output_type": RevisionPlan,
                "platform_prompt": "revision-plan",
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
        return RevisionPlan.model_validate(result)
    except Exception:
        return None


def _local_revision_plan(
    request: str,
    *,
    policy: RevisionPolicy,
) -> RevisionPlan:
    paragraphs = _target_paragraphs(request)
    title_only = policy.supports_title and _is_title_only(request)
    if title_only:
        scope: RevisionScope = "title"
    elif paragraphs:
        scope = "paragraph"
    else:
        scope = "whole"
    return RevisionPlan(
        scope=scope,
        target_paragraphs=paragraphs,
        preserve_title=(
            policy.supports_title
            and scope == "paragraph"
            and not _requests_title_change(request)
        ),
        preserve_other_paragraphs=scope in {"title", "paragraph"},
        target_length=_target_length(request, policy=policy),
        preserve_facts_and_numbers=_preserve_facts_and_numbers(request),
        required_changes=[request] if request else [],
        must_remove=_must_remove(request),
    )


def _normalize_semantic_plan(
    plan: RevisionPlan,
    *,
    policy: RevisionPolicy,
) -> RevisionPlan:
    if not policy.supports_title:
        plan.preserve_title = False
        if plan.scope == "title":
            plan.scope = "whole"
    if plan.target_length is not None:
        plan.target_length = min(
            policy.max_target_length,
            max(policy.min_target_length, plan.target_length),
        )
    plan.target_paragraphs = list(
        dict.fromkeys(item for item in plan.target_paragraphs if item > 0)
    )
    if plan.scope == "paragraph" and not plan.target_paragraphs:
        plan.scope = "whole"
        plan.preserve_other_paragraphs = False
    return plan


def _build_revision_instruction(
    *,
    inputs: dict[str, object],
    request: str,
    plan: RevisionPlan,
) -> str:
    instruction = str(inputs.get("text", "") or "").strip()
    if not instruction:
        instruction = (
            "请基于上一稿进行修改，不要把这次任务当作重新写作。\n"
            f"用户新的修改要求：{request}\n"
            "除非用户明确要求新增内容，否则保留上一稿中的事实、口径和来源，不编造新事实。"
        )
    constraints = render_revision_plan(plan)
    if constraints not in instruction:
        instruction = f"{instruction.rstrip()}\n{constraints}"
    if _mentions_no_split(request):
        instruction += "\n- 用户明确要求不要拆分：不得拆分段落，不得拆分板块。"
    if _mentions_original_meaning(request):
        instruction += (
            "\n- 用户质疑改变原文意思：只能根据上一稿和本轮用户贴出的原句修正；"
            "不能声称已经核对原始素材。无法确认原文时，应采用更贴近用户原句的保守表述。"
        )
    return instruction


def _target_paragraphs(request: str) -> list[int]:
    values: list[int] = []
    pattern = re.compile(r"第([一二三四五六七八九十\d]+)段")
    for match in pattern.finditer(request):
        before = request[max(0, match.start() - 5) : match.start()]
        after = request[match.end() : match.end() + 6]
        protected_after = re.match(
            r"^[，,、：:\s]*(不动|不变|不要改|别改|保持|保留)",
            after,
        )
        if protected_after or any(
            before.endswith(marker)
            for marker in ("保留", "保持", "不改", "别动", "不要改")
        ):
            continue
        raw = match.group(1)
        value = int(raw) if raw.isdigit() else _CHINESE_NUMBERS.get(raw)
        if value and value not in values:
            values.append(value)
    return values


def _is_title_only(request: str) -> bool:
    compact = re.sub(r"\s+", "", request)
    negative_markers = (
        "不要只改标题",
        "别只改标题",
        "不能只改标题",
        "不只改标题",
        "不仅改标题",
        "不只是标题",
    )
    if any(marker in compact for marker in negative_markers):
        return False
    return "只改标题" in compact or (
        "标题" in compact
        and any(
            marker in compact
            for marker in (
                "正文不动",
                "正文不要动",
                "其他不变",
                "其余不变",
                "就好",
                "只",
                "仅",
            )
        )
    )


def _requests_title_change(request: str) -> bool:
    return "标题" in request and any(
        marker in request for marker in ("改", "调整", "换", "优化")
    )


def _target_length(
    request: str,
    *,
    policy: RevisionPolicy,
) -> int | None:
    match = re.search(
        r"(?:压缩到|控制在|改成|调整为|约)\s*(\d{2,5})\s*字",
        request,
    )
    if match is None:
        return None
    return min(
        policy.max_target_length,
        max(policy.min_target_length, int(match.group(1))),
    )


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


def _mentions_no_split(request: str) -> bool:
    compact = re.sub(r"\s+", "", request)
    return bool(re.search(r"(?:不需要|不要|不得|不用).{0,10}拆", compact))


def _mentions_original_meaning(request: str) -> bool:
    return any(
        marker in request
        for marker in ("原文", "原意", "原稿", "改变了")
    )


def _paragraphs(body: str) -> list[str]:
    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", body)
        if item.strip()
    ]
    if len(paragraphs) > 1:
        return paragraphs
    lines = [item.strip() for item in body.splitlines() if item.strip()]
    return lines or ([body.strip()] if body.strip() else [])
