from __future__ import annotations

import asyncio
from pathlib import Path

from app.review.format_checker import check_quote_pair
from app.review.rule_loader import load_rules
from app.review.reviewer import (
    Finding,
    ReviewDocument,
    ReviewEntry,
    _build_phase_prompt,
    _build_review_document,
    _find_toc_range,
    _find_weekly_body_start,
    _normalize_neican_findings,
    _render_phase1_context,
    _render_phase2_context,
    review_phase1,
    review_phase2,
)


def _sample_weekly_paragraphs() -> list[str]:
    return [
        "内部资料",
        "目录",
        "党政要闻2",
        "监管动态3",
        "党政要闻",
        "示例政策会议召开",
        "会议部署了年度重点工作，并提出后续安排。",
        "监管动态",
        "金融监管部门发布示例规则",
        "规则围绕风险管理和服务质效提出要求。",
    ]


def test_quote_pair_treats_symmetric_double_quotes_as_paired():
    findings = check_quote_pair(
        ['李强主持召开国务院常务会议，部署就业优先战略"十五五"规划']
    )

    assert findings == []


def test_quote_pair_detects_unpaired_chinese_curly_quotes():
    """检测中文双引号只有一边的情况."""
    left = "“"
    right = "”"

    # 只有左引号
    findings = check_quote_pair([f"他说：{left}今天开会。"])
    assert any(f.target_text == left for f in findings)

    # 只有右引号
    findings = check_quote_pair([f"他说：今天开会。{right}"])
    assert any(f.target_text == right for f in findings)


def test_quote_pair_detects_same_side_chinese_curly_quotes():
    """检测中文双引号同一边的情况."""
    left = "“"
    right = "”"

    # 两个左引号
    findings = check_quote_pair([f"他说：{left}今天开会{left}明天继续。"])
    assert any(f.target_text == left for f in findings)

    # 两个右引号
    findings = check_quote_pair([f"他说：{right}今天开会{right}明天继续。"])
    assert any(f.target_text == right for f in findings)


def test_quote_pair_allows_proper_chinese_curly_quote_pair():
    """正确成对的中文双引号不报错."""
    left = "“"
    right = "”"

    findings = check_quote_pair([f"他说：{left}今天开会。{right}"])
    assert findings == []


def test_quote_pair_detects_single_curly_quotes():
    """检测中文单引号不成对的情况."""
    left = "‘"
    right = "’"

    findings = check_quote_pair([f"他说：{left}测试{left}结束。"])
    assert any(f.target_text == left for f in findings)

    findings = check_quote_pair([f"他说：{right}测试{right}结束。"])
    assert any(f.target_text == right for f in findings)

    findings = check_quote_pair([f"他说：{left}测试{right}结束。"])
    assert findings == []


def test_quote_pair_does_not_repeat_ascii_quote_errors():
    paragraphs = [
        '材料提到"十五五"规划。',
        '英文名称使用"Example Bank"表示。',
    ]

    assert check_quote_pair(paragraphs) == []


def test_phase1_prompt_is_compact_and_only_mentions_phase1_rules():
    paragraphs = _sample_weekly_paragraphs()
    rules_text = load_rules(Path("app/data/rules.md"))

    prompt = _build_phase_prompt(rules_text, paragraphs, "示例周报.docx", 1)

    assert "智能审核规则库" not in prompt
    assert "content-out-of-scope" not in prompt
    assert "content-wrong-section" not in prompt
    assert "content-duplicate" not in prompt
    assert "示例政策会议召开" in prompt
    assert "会议部署了年度重点工作" in prompt
    assert len(prompt) < 10000


def test_phase2_prompt_is_compact_and_only_mentions_phase2_rules():
    paragraphs = _sample_weekly_paragraphs()
    rules_text = load_rules(Path("app/data/rules.md"))

    prompt = _build_phase_prompt(rules_text, paragraphs, "示例周报.docx", 2)

    assert "智能审核规则库" not in prompt
    assert "title-truncated" not in prompt
    assert "content-mismatch" not in prompt
    assert "content-incomplete" not in prompt
    assert "金融监管部门发布示例规则" in prompt
    assert "[板块:监管动态" in prompt
    assert len(prompt) < 12000


def test_phase_prompts_require_target_text_for_marking():
    paragraphs = _sample_weekly_paragraphs()
    rules_text = load_rules(Path("app/data/rules.md"))

    phase1_prompt = _build_phase_prompt(rules_text, paragraphs, "示例周报.docx", 1)
    phase2_prompt = _build_phase_prompt(rules_text, paragraphs, "示例周报.docx", 2)

    assert '"target_text"' in phase1_prompt
    assert "target_text 必须是原文里真实出现的短片段" in phase1_prompt
    assert '"target_text"' in phase2_prompt
    assert "target_text 必须是原文里真实出现的短片段" in phase2_prompt


def test_phase1_context_keeps_full_body_text_without_omission_markers():
    long_body = (
        "第一部分完整展开，说明背景和政策要求。"
        + "甲" * 380
        + "第二部分继续展开，补充风险点和结论。"
    )
    document = ReviewDocument(
        toc_entries=(),
        entries=(
            ReviewEntry(
                section="监管动态",
                title_index=3,
                title_text="央行发布新规",
                body_indexes=(4,),
                body_paragraphs=(long_body,),
            ),
        ),
    )

    context = _render_phase1_context(document)

    assert long_body in context
    assert "[后文略]" not in context
    assert "[前文略]" not in context


def test_phase2_context_keeps_full_body_text_without_summary_clipping():
    first_body = "甲" * 260 + "这里是正文后半段，不能再被裁成摘要。"
    last_body = "乙" * 200 + "这里是最后一段的结尾，也不能被裁掉。"
    document = ReviewDocument(
        toc_entries=((1, "监管动态"),),
        entries=(
            ReviewEntry(
                section="监管动态",
                title_index=5,
                title_text="央行发布新规",
                body_indexes=(6, 7),
                body_paragraphs=(first_body, last_body),
            ),
        ),
    )

    context = _render_phase2_context(document)

    assert first_body in context
    assert last_body in context
    assert "..." not in context


def test_normalize_neican_findings_fills_target_text_for_marking():
    paragraphs = [
        "示例政策会议介绍年度重点工作",
        "会议强调，要持续完善服务机制，后续将",
    ]

    findings = [
        Finding(
            rule_id="content-mismatch",
            paragraph_index=0,
            line_number=1,
            original_text=paragraphs[0],
            description="标题和正文讲的不是同一件事",
        ),
        Finding(
            rule_id="content-incomplete",
            paragraph_index=1,
            line_number=2,
            original_text=paragraphs[1],
            description="正文末尾语义不完整",
        ),
    ]

    normalized = _normalize_neican_findings(findings, paragraphs)

    assert normalized[0].target_text == paragraphs[0]
    assert normalized[1].target_text
    assert normalized[1].target_text in paragraphs[1]
    assert normalized[1].target_text.endswith("后续将")


def test_normalize_neican_findings_only_keeps_body_incomplete_findings():
    paragraphs = ["目录项", "示例标题", "后续将"]
    document = ReviewDocument(
        toc_entries=((0, "目录项"),),
        entries=(
            ReviewEntry(
                section="党政要闻",
                title_index=1,
                title_text="示例标题",
                body_indexes=(2,),
                body_paragraphs=("后续将",),
            ),
        ),
    )
    findings = [
        Finding(
            rule_id="content-incomplete",
            paragraph_index=0,
            line_number=1,
            original_text=paragraphs[0],
            description="正文末尾句子突然中断，语义不完整。",
        ),
        Finding(
            rule_id="content-incomplete",
            paragraph_index=2,
            line_number=3,
            original_text=paragraphs[2],
            description="正文末尾句子突然中断，语义不完整。",
        ),
    ]

    normalized = _normalize_neican_findings(
        findings,
        paragraphs,
        document=document,
        active_rules=("content-incomplete",),
    )

    assert {f.paragraph_index for f in normalized} == {2}


def test_toc_range_stops_before_body_section_after_directory():
    paragraphs = [
        "内部资料",
        "微众银行信息内参周报",
        "目录",
        "一、党政要闻2",
        "监管动态3",
        "市场观察4",
        "党政要闻",
        "标题A",
        "正文A。",
    ]

    toc_start, toc_end = _find_toc_range(paragraphs)
    document = _build_review_document(paragraphs)

    assert (toc_start, toc_end) == (3, 6)
    assert document.toc_entries == (
        (3, "一、党政要闻2"),
        (4, "监管动态3"),
        (5, "市场观察4"),
    )


def test_weekly_body_start_begins_at_first_body_section_after_toc():
    paragraphs = [
        "内部资料",
        "微众银行信息内参周报",
        "目录",
        "一、党政要闻2",
        "监管动态3",
        "市场观察4",
        "党政要闻",
        "标题A",
        "正文A。",
    ]

    assert _find_weekly_body_start(paragraphs) == 6


def test_review_phase1_reports_toc_ordinal_issues_when_model_returns_empty(monkeypatch):
    counter = {"calls": 0}
    fake_output = '{"issues": []}'
    paragraphs = [
        "内部资料",
        "微众银行信息内参周报",
        "目录",
        "一、党政要闻2",
        "监管动态3",
        "三、市场观察4",
        "党政要闻",
        "标题A",
        "正文A。",
    ]

    monkeypatch.setattr(
        "app.review.reviewer._get_anthropic_client",
        lambda: (_FakeClient(counter, fake_output), "fake-model"),
    )

    result = asyncio.run(review_phase1(paragraphs, "", "demo.docx"))

    toc_findings = [
        (f.paragraph_index, f.rule_id)
        for f in result.findings
        if f.rule_id in {"toc-no-ordinal", "toc-seq-skip"}
    ]

    assert counter["calls"] == 1
    assert (3, "toc-no-ordinal") in toc_findings
    assert (5, "toc-no-ordinal") in toc_findings


def test_review_phase2_reports_toc_content_mismatch_when_directory_not_refreshed(monkeypatch):
    counter = {"calls": 0}
    fake_output = '{"issues": []}'
    paragraphs = [
        "内部资料",
        "微众银行信息内参周报",
        "目录",
        "党政要闻2",
        "旧标题2",
        "监管动态3",
        "党政要闻",
        "国务院发布新规",
        "正文A。",
        "监管动态",
        "金融监管总局部署重点工作",
        "正文B。",
    ]

    monkeypatch.setattr(
        "app.review.reviewer._get_anthropic_client",
        lambda: (_FakeClient(counter, fake_output), "fake-model"),
    )

    result = asyncio.run(review_phase2(paragraphs, "", "demo.docx"))

    mismatch = next(
        f for f in result.findings
        if f.rule_id == "toc-mismatch" and f.paragraph_index == 4
    )

    assert counter["calls"] == 1
    assert "旧标题" in mismatch.description
    assert "国务院发布新规" in mismatch.description


class _FakeBlock:
    def __init__(self, text: str):
        self.text = text


class _FakeMessage:
    def __init__(self, text: str):
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, counter: dict[str, int], text: str):
        self._counter = counter
        self._text = text

    def create(self, **_: object) -> _FakeMessage:
        self._counter["calls"] += 1
        return _FakeMessage(self._text)


class _FakeClient:
    def __init__(self, counter: dict[str, int], text: str):
        self.messages = _FakeMessages(counter, text)


def test_review_phase1_only_calls_model_once_when_first_response_is_valid(monkeypatch):
    counter = {"calls": 0}
    fake_output = '{"issues": []}'

    monkeypatch.setattr(
        "app.review.reviewer._get_anthropic_client",
        lambda: (_FakeClient(counter, fake_output), "fake-model"),
    )

    result = asyncio.run(review_phase1(["测试标题", "测试正文。"], "", "demo.docx"))

    assert counter["calls"] == 1
    assert not [f for f in result.findings if f.rule_id.startswith("__")]


def test_review_phase2_only_calls_model_once_when_first_response_is_valid(monkeypatch):
    counter = {"calls": 0}
    fake_output = '{"issues": []}'

    monkeypatch.setattr(
        "app.review.reviewer._get_anthropic_client",
        lambda: (_FakeClient(counter, fake_output), "fake-model"),
    )

    result = asyncio.run(review_phase2(["测试标题", "测试正文。"], "", "demo.docx"))

    assert counter["calls"] == 1
    assert not [f for f in result.findings if f.rule_id.startswith("__")]
