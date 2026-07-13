from __future__ import annotations


def build_revision_payload(inputs: dict[str, object], *, skill_id: str) -> dict[str, object]:
    request = str(inputs.get("revision_request", "") or inputs.get("text", "")).strip()
    instruction = str(inputs.get("text", "")).strip()
    constraints = _revision_edit_constraints(request)
    if not instruction:
        instruction = (
            "请基于上一稿进行修改，不要把这次任务当作重新写作。\n"
            f"用户新的修改要求：{request}\n"
            "除非用户明确要求新增内容，否则保留上一稿中的事实、口径和来源，不编造新事实。\n"
            f"{constraints}"
        )
    elif constraints not in instruction:
        instruction = f"{instruction.rstrip()}\n{constraints}"

    return {
        "skill_id": skill_id,
        "task": skill_id,
        "instruction": instruction,
        "revision": True,
        "revision_request": request,
        "previous_job_id": str(inputs.get("previous_job_id", "")),
        "materials": [previous_draft_material(inputs)],
    }


def previous_draft_material(inputs: dict[str, object]) -> dict[str, object]:
    title = str(inputs.get("previous_title", "")).strip()
    body = str(inputs.get("previous_body", "")).strip()
    return {
        "title": f"上一稿：{title}" if title else "上一稿",
        "text": f"标题：{title}\n\n正文：{body}".strip(),
        "url": "",
        "source": "previous_draft",
    }


def previous_sources(inputs: dict[str, object]) -> list[str]:
    return [
        str(item).strip()
        for item in list(inputs.get("previous_sources") or [])
        if str(item).strip()
    ]


def _revision_edit_constraints(request: str) -> str:
    constraints = [
        "改稿执行约束：",
        "1. 先判断用户要求属于标题修改、局部段落修改、结构顺序调整、篇幅压缩还是全文重写；除非用户明确要求全文重写，否则不要重写整篇。",
        "2. 对局部修改请求，只改被点名的标题、句子或段落；未被点名的段落必须原样保留，不要顺手改写。",
        "3. 如果用户要求调整顺序，只移动相关段落或板块，不能改写事实口径。",
    ]
    if _mentions_title_only(request):
        constraints.append("4. 用户本轮要求只修改标题：只修改标题，不得拆分段落，不得调整正文结构。")
    if _mentions_no_split(request):
        constraints.append("5. 用户明确要求不要拆分：不得拆分段落，不得把一个板块改成两个板块。")
    if _mentions_original_meaning(request):
        constraints.append(
            "6. 用户质疑改变原文意思：只能根据上一稿和本轮用户贴出的原句修正；不能声称已经核对原始素材。无法确认原文时，应保守改为更贴近用户原句的表述。"
        )
    return "\n".join(constraints)


def _mentions_title_only(request: str) -> bool:
    negative_markers = (
        "不要只改标题",
        "别只改标题",
        "不能只改标题",
        "不只改标题",
        "不仅改标题",
        "不只是标题",
    )
    if any(marker in request for marker in negative_markers):
        return False
    return "标题" in request and any(marker in request for marker in ("就好", "只", "仅"))


def _mentions_no_split(request: str) -> bool:
    return any(marker in request for marker in ("不需要拆", "不要拆", "不得拆", "不用拆"))


def _mentions_original_meaning(request: str) -> bool:
    return "原文" in request or "原意" in request or "原稿" in request or "改变了" in request
