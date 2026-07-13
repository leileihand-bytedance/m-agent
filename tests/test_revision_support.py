from skills.revision_support import build_revision_payload


def test_revision_payload_adds_local_edit_constraints_for_title_only_request():
    payload = build_revision_payload(
        {
            "text": "",
            "revision_request": "我的意思是修改标题就好，不需要把这一段拆成2部分",
            "previous_title": "上一稿标题",
            "previous_body": "上一稿正文",
            "previous_job_id": "job-001",
        },
        skill_id="writer1",
    )

    instruction = str(payload["instruction"])

    assert "只修改标题" in instruction
    assert "不得拆分段落" in instruction
    assert "未被点名的段落必须原样保留" in instruction


def test_revision_payload_requires_source_check_honesty_for_original_meaning_request():
    payload = build_revision_payload(
        {
            "text": "",
            "revision_request": "这句话改变了原文的意思，修改一下",
            "previous_title": "上一稿标题",
            "previous_body": "上一稿正文",
        },
        skill_id="writer1",
    )

    instruction = str(payload["instruction"])

    assert "不能声称已经核对原始素材" in instruction
    assert "无法确认原文时" in instruction


def test_revision_payload_appends_precise_constraints_to_platform_instruction():
    payload = build_revision_payload(
        {
            "text": "请基于上一稿进行修改。\n用户新的修改要求：只改标题就好",
            "revision_request": "只改标题就好",
            "previous_title": "上一稿标题",
            "previous_body": "上一稿正文",
        },
        skill_id="direct_report",
    )

    instruction = str(payload["instruction"])

    assert "未被点名的段落必须原样保留" in instruction
    assert "只修改标题" in instruction


def test_revision_payload_does_not_treat_negated_title_only_request_as_title_only():
    payload = build_revision_payload(
        {
            "text": "",
            "revision_request": "不要只改标题，正文也要一起调整",
            "previous_title": "上一稿标题",
            "previous_body": "上一稿正文",
        },
        skill_id="writer1",
    )

    assert "用户本轮要求只修改标题" not in str(payload["instruction"])
