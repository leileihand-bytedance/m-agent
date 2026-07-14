from app.platform.intent import ConversationIntent, classify_conversation_intent, select_draft_version


def test_classifies_critique_without_revision_keyword_as_revision():
    intent = classify_conversation_intent(
        text="还是太像新闻稿，开头也有点虚",
        has_active_conversation=True,
        route_skill_id=None,
        route_needs_clarification=True,
    )

    assert intent == ConversationIntent.REVISE_PREVIOUS


def test_classifies_add_original_content_request_as_revision():
    examples = [
        "增加社会责任作为正文的第三部分。全文的篇幅再控制一下",
        "把原文社会责任的内容作为加进来",
        "把原稿中的社会责任部分，加进来",
    ]

    for text in examples:
        intent = classify_conversation_intent(
            text=text,
            has_active_conversation=True,
            route_skill_id=None,
            route_needs_clarification=True,
        )

        assert intent == ConversationIntent.REVISE_PREVIOUS


def test_classifies_explicit_new_material_as_new_task():
    intent = classify_conversation_intent(
        text="根据这篇材料写简报：微众银行持续完善小微企业服务能力。",
        has_active_conversation=True,
        route_skill_id="writer1",
        route_needs_clarification=False,
    )

    assert intent == ConversationIntent.NEW_TASK


def test_classifies_inline_polish_with_new_text_as_new_task_even_with_active_conversation():
    intent = classify_conversation_intent(
        text="帮我润色这段：这是一段新的材料文字，不要沿着上一稿继续改。",
        has_active_conversation=True,
        route_skill_id="rewrite",
        route_needs_clarification=False,
    )

    assert intent == ConversationIntent.NEW_TASK


def test_classifies_inline_formalize_request_with_new_text_as_new_task():
    intent = classify_conversation_intent(
        text="帮我把下面这段更正式一点：这是一段新的材料文字，不要沿着上一稿继续改。",
        has_active_conversation=True,
        route_skill_id=None,
        route_needs_clarification=True,
    )

    assert intent == ConversationIntent.NEW_TASK


def test_classifies_rewrite_request_after_new_material_as_new_task():
    intent = classify_conversation_intent(
        text="这是一段新的材料文字，不要沿着上一稿继续改。\n\n帮我整体润色一下",
        has_active_conversation=True,
        route_skill_id="rewrite",
        route_needs_clarification=False,
    )

    assert intent == ConversationIntent.NEW_TASK


def test_classifies_realistic_polish_request_after_pasted_material_as_new_task():
    intent = classify_conversation_intent(
        text=(
            "县域经济作为国民经济的基本单元，是国家推动乡村振兴的重要切入点。"
            "微众银行持续完善县域金融服务供给。\n\n帮我整体润色一下"
        ),
        has_active_conversation=True,
        route_skill_id="rewrite",
        route_needs_clarification=False,
    )

    assert intent == ConversationIntent.NEW_TASK


def test_classifies_thanks_as_clarify_even_with_active_conversation():
    intent = classify_conversation_intent(
        text="谢谢，我先看看",
        has_active_conversation=True,
        route_skill_id=None,
        route_needs_clarification=True,
    )

    assert intent == ConversationIntent.CLARIFY


def test_selects_previous_or_numbered_draft_version():
    assert select_draft_version("回到上一版再改", current_version=3) == 2
    assert select_draft_version("按第一版的结构再压缩", current_version=3) == 1
    assert select_draft_version("参考第2版标题", current_version=3) == 2
    assert select_draft_version("继续优化", current_version=3) is None
