import asyncio
from pathlib import Path

import pytest

from app.review.html_parser import parse_html
from app.review.document_type import DocumentType
from app.review.main import (
    ReviewConfig,
    _process_queued_single_review,
    is_html_filename,
    is_supported_review_filename,
)
from app.review.reviewer import Finding, ReviewResult
from app.review.output_formatter import format_review_result
from app.review.task_execution import (
    GENERAL_HTML_REVIEW_TASK_TYPE,
    GeneralReviewWorkspace,
)


def test_parse_html_extracts_visible_blocks_and_table_rows(tmp_path: Path):
    path = tmp_path / "report.html"
    path.write_text(
        """<html>
        <head>
          <title>浏览器标签标题</title>
          <style>.secret { display: none; }</style>
        </head>
        <body>
          <h1>经营情况</h1>
          <p>本期收入100万元。</p>
          <ul><li>客户数20户</li></ul>
          <table>
            <tr><th>指标</th><th>数值</th></tr>
            <tr><td>收入</td><td>100万元</td></tr>
          </table>
          <script>prompt injection</script>
          <template>模板文字</template>
          <!-- 注释文字 -->
          <p hidden>隐藏一</p>
          <p aria-hidden="true">隐藏二</p>
          <p style="display: none">隐藏三</p>
          <p style="visibility:hidden">隐藏四</p>
          <a href="https://internal.example">查看详情</a>
          <img alt="属性文字">
        </body>
        </html>""",
        encoding="utf-8",
    )

    parsed = parse_html(path)

    assert parsed.paragraphs == [
        "经营情况",
        "本期收入100万元。",
        "客户数20户",
        "指标 | 数值",
        "收入 | 100万元",
        "查看详情",
    ]
    combined = "\n".join(parsed.paragraphs)
    assert "prompt injection" not in combined
    assert "浏览器标签标题" not in combined
    assert "internal.example" not in combined
    assert "属性文字" not in combined
    assert parsed.encoding == "utf-8"
    assert parsed.paragraph_pages == [None, None, None, None, None, None]


def test_parse_html_maps_visible_paragraphs_to_slide_pages(tmp_path: Path):
    path = tmp_path / "deck.html"
    path.write_text(
        """<div class="slide" hidden><p>隐藏页面</p></div>
        <div class="slide slide-cover"><h1>封面</h1></div>
        <div class="slide slide-content"><p>本期客户100户。</p>
        <table><tr><td>客户</td><td>120户</td></tr></table></div>
        <div class="slide-content"><p>页外提示</p></div>""",
        encoding="utf-8",
    )

    parsed = parse_html(path)

    assert parsed.paragraphs == [
        "封面",
        "本期客户100户。",
        "客户 | 120户",
        "页外提示",
    ]
    assert parsed.paragraph_pages == [1, 2, 2, None]


def test_parse_html_maps_nested_slides_in_dom_order(tmp_path: Path):
    path = tmp_path / "nested-deck.html"
    path.write_text(
        """<div class="slide"><p>第一页</p>
        <div class="slide"><p>第二页</p></div>
        <p>回到第一页容器</p></div>""",
        encoding="utf-8",
    )

    parsed = parse_html(path)

    assert parsed.paragraphs == ["第一页", "第二页", "回到第一页容器"]
    assert parsed.paragraph_pages == [1, 2, 1]


def test_parse_html_maps_unclosed_slide_as_next_dom_page(tmp_path: Path):
    path = tmp_path / "unclosed-deck.html"
    path.write_text(
        """<div class="slide"><p>第一页
        <div class="slide"><p>第二页</div>""",
        encoding="utf-8",
    )

    parsed = parse_html(path)

    assert parsed.paragraphs == ["第一页", "第二页"]
    assert parsed.paragraph_pages == [1, 2]


def test_parse_html_uses_utf8_bom(tmp_path: Path):
    path = tmp_path / "bom.html"
    path.write_bytes("<p>可见文字</p>".encode("utf-8-sig"))

    parsed = parse_html(path)

    assert parsed.paragraphs == ["可见文字"]
    assert parsed.encoding == "utf-8-sig"


def test_parse_html_uses_meta_gb18030(tmp_path: Path):
    path = tmp_path / "legacy.htm"
    path.write_bytes(
        '<meta charset="gb18030"><p>本期收入100万元。</p>'.encode("gb18030")
    )

    parsed = parse_html(path)

    assert parsed.paragraphs == ["本期收入100万元。"]
    assert parsed.encoding == "gb18030"


def test_parse_html_tolerates_unclosed_blocks(tmp_path: Path):
    path = tmp_path / "broken.html"
    path.write_text("<h1>标题<p>第一段<p>第二段", encoding="utf-8")

    assert parse_html(path).paragraphs == ["标题", "第一段", "第二段"]


def test_parse_html_rejects_document_without_visible_text(tmp_path: Path):
    path = tmp_path / "empty.html"
    path.write_text(
        "<script>only code</script><p hidden>hidden</p>",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="没有可审核的可见文字"):
        parse_html(path)


def test_parse_html_respects_native_closed_dialog_and_details(tmp_path: Path):
    path = tmp_path / "native-hidden.html"
    path.write_text(
        """<dialog>关闭对话框</dialog>
        <dialog open><p>打开对话框</p></dialog>
        <details><summary>折叠摘要</summary><p>折叠正文</p></details>
        <details open><summary>展开摘要</summary><p>展开正文</p></details>""",
        encoding="utf-8",
    )

    assert parse_html(path).paragraphs == [
        "打开对话框",
        "折叠摘要",
        "展开摘要",
        "展开正文",
    ]


def test_parse_html_hidden_first_summary_does_not_expose_second_summary(
    tmp_path: Path,
):
    path = tmp_path / "hidden-first-summary.html"
    path.write_text(
        """<p>正常正文</p><details>
        <summary hidden>隐藏的首摘要</summary>
        <summary>第二摘要</summary>
        <p>折叠正文</p>
        </details>""",
        encoding="utf-8",
    )

    assert parse_html(path).paragraphs == ["正常正文"]


def test_parse_html_only_uses_charset_declared_by_real_meta_tag(tmp_path: Path):
    path = tmp_path / "misleading-charset.html"
    path.write_bytes(
        b'<!-- charset=iso-8859-1 --><script>charset=gb18030</script>'
        b'<meta charset="utf-8"><p>\xe4\xb8\xad\xe6\x96\x87\xe6\xad\xa3\xe6\x96\x87</p>'
    )

    parsed = parse_html(path)

    assert parsed.paragraphs == ["中文正文"]
    assert parsed.encoding == "utf-8"


def test_review_file_extensions_accept_docx_html_and_htm():
    assert is_supported_review_filename("report.docx") is True
    assert is_supported_review_filename("report.html") is True
    assert is_supported_review_filename("REPORT.HTM") is True
    assert is_supported_review_filename("report.pdf") is False
    assert is_supported_review_filename(None) is False
    assert is_html_filename("report.html") is True
    assert is_html_filename("REPORT.HTM") is True
    assert is_html_filename("report.docx") is False


def _review_config(tmp_path: Path) -> ReviewConfig:
    return ReviewConfig(
        wecom_bot_id="bot",
        wecom_bot_secret="secret",
        rules_path=tmp_path / "rules.md",
        reviews_dir=tmp_path / "reviews",
        logs_dir=tmp_path / "logs",
        admin_user_id="",
        admin_name="",
        notification_cooldown=300,
        direct_admin_notifications=False,
        require_registration=False,
    )


def _location_result(filename: str = "deck.html") -> ReviewResult:
    return ReviewResult(
        findings=[
            Finding(
                rule_id="general-logic-inconsistency",
                paragraph_index=1,
                line_number=2,
                original_text="同口径客户为120户。",
                description="与前文同口径的100户前后不一致",
                target_text="120户",
            )
        ],
        total_rules=1,
        passed_rules=0,
        filename=filename,
    )


def test_format_review_result_uses_html_slide_page_location():
    output = format_review_result(
        _location_result(),
        "deck.html",
        doc_type=DocumentType.GENERAL,
        paragraph_pages=[1, 2],
    )

    assert "位置：第2页" in output


def test_format_review_result_falls_back_to_html_paragraph_location():
    output = format_review_result(
        _location_result("article.html"),
        "article.html",
        doc_type=DocumentType.GENERAL,
        paragraph_pages=[None, None],
    )

    assert "位置：第2段" in output


def test_persistent_html_review_returns_message_without_marked_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import app.review.general_reviewer as general_reviewer

    async def fake_general(paragraphs, _rules, filename, **kwargs):
        assert paragraphs == ["本期客户100户。", "同口径客户为120户。"]
        assert filename == "经营报告.html"
        assert kwargs["whole_document_logic_min_chars"] == 0
        return ReviewResult(
            findings=[
                Finding(
                    rule_id="general-logic-inconsistency",
                    paragraph_index=1,
                    line_number=2,
                    original_text="同口径客户为120户。",
                    description="与前文同口径的100户前后不一致",
                    target_text="120户",
                )
            ],
            total_rules=1,
            passed_rules=0,
            filename=filename,
        )

    monkeypatch.setattr(general_reviewer, "review_general", fake_general)
    task_dir = tmp_path / "reviews" / "queued-html"
    input_dir = task_dir / "input"
    (task_dir / "output").mkdir(parents=True)
    input_dir.mkdir(parents=True)
    input_file = input_dir / "经营报告.html"
    source = (
        '<div class="slide"><p>本期客户100户。</p></div>'
        '<div class="slide"><p>同口径客户为120户。</p></div>'
    )
    input_file.write_text(source, encoding="utf-8")
    workspace = GeneralReviewWorkspace(
        task_id="task-html",
        task_dir=task_dir,
        input_file=input_file,
        filename="经营报告.html",
        sender_userid="user-1",
        sender_name="User One",
        task_type=GENERAL_HTML_REVIEW_TASK_TYPE,
        input_kind="html",
    )

    delivery = asyncio.run(
        _process_queued_single_review(
            workspace,
            config=_review_config(tmp_path),
            neican_rules_text="",
        )
    )

    assert delivery.kind == "text"
    assert "前后逻辑不一致" in delivery.text
    assert "120户" in delivery.text
    assert "位置：第2页" in delivery.text
    report_path = task_dir / "output" / "report.md"
    assert report_path.is_file()
    assert "位置：第2页" in report_path.read_text(encoding="utf-8")
    assert input_file.read_text(encoding="utf-8") == source
    assert list((task_dir / "output").glob("marked_*")) == []


def test_persistent_html_review_returns_clear_message_for_empty_page(tmp_path: Path):
    task_dir = tmp_path / "reviews" / "queued-empty-html"
    input_dir = task_dir / "input"
    (task_dir / "output").mkdir(parents=True)
    input_dir.mkdir(parents=True)
    input_file = input_dir / "空页面.html"
    input_file.write_text("<script>only code</script>", encoding="utf-8")
    workspace = GeneralReviewWorkspace(
        task_id="task-empty-html",
        task_dir=task_dir,
        input_file=input_file,
        filename="空页面.html",
        sender_userid="user-1",
        sender_name="User One",
        task_type=GENERAL_HTML_REVIEW_TASK_TYPE,
        input_kind="html",
    )

    delivery = asyncio.run(
        _process_queued_single_review(
            workspace,
            config=_review_config(tmp_path),
            neican_rules_text="",
        )
    )

    assert delivery.kind == "text"
    assert delivery.text == "HTML文件中没有可审核的可见文字，请提供包含静态正文的HTML文件。"
    assert not (task_dir / "output" / "report.md").exists()
