from datetime import date
from pathlib import Path

import pytest
from docx import Document

from skills.shenyinxie_news.docx_output import write_shenyinxie_docx
from skills.shenyinxie_news.schema import SelectedArticle


def _article(**kwargs) -> SelectedArticle:
    defaults = {
        "title": "微众银行发布年报",
        "media_name": "人民网",
        "publish_date": "2026-07-15",
        "body": "2026年7月15日，微众银行发布年报，营收增长20%。" * 5,
        "original_url": "https://people.com.cn/1",
    }
    defaults.update(kwargs)
    return SelectedArticle(**defaults)


def test_docx_output_without_template_uses_scratch_format(tmp_path):
    output_dir = tmp_path / "out"
    articles = [_article()]

    path = write_shenyinxie_docx(
        title="深圳银行业协会工作动态（2026年7月第2期）",
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 29),
        issue_number="2026-14",
        articles=articles,
        output_dir=output_dir,
        template_path=tmp_path / "nonexistent.docx",
    )

    assert path.exists()
    assert "深银协动态202614" in path.name

    doc = Document(str(path))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "深圳银行业协会工作动态" in full_text
    assert "2026年7月16日—29日" in full_text
    assert "动态一" in full_text
    assert "微众银行发布年报" in full_text
    assert "https://people.com.cn/1" in full_text


def test_docx_output_with_placeholder_template_replaces_articles(tmp_path):
    template = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("{{TITLE}}")
    doc.add_paragraph("{{PERIOD_RANGE}}")
    doc.add_paragraph("{{ARTICLE_1}}")
    doc.add_paragraph("{{ARTICLE_2}}")
    doc.add_paragraph("{{ARTICLE_3}}")
    doc.save(str(template))

    articles = [
        _article(title="第一篇", body="正文一" * 5),
        _article(title="第二篇", body="正文二" * 5),
    ]

    path = write_shenyinxie_docx(
        title="T",
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 29),
        issue_number="2026-14",
        articles=articles,
        output_dir=tmp_path / "out",
        template_path=template,
    )

    assert path.exists()
    doc = Document(str(path))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "T" in full_text
    assert "2026年7月16日—29日" in full_text
    assert "第一篇" in full_text
    assert "第二篇" in full_text
    assert "正文一" in full_text
    assert "正文二" in full_text
    # 第三篇占位符应被替换为空
    assert "{{ARTICLE_3}}" not in full_text


def test_docx_output_with_blank_template_falls_back_to_scratch(tmp_path):
    """占位模板不含约定占位符时，应视为空白占位并按代码新建文档。"""
    template = tmp_path / "blank.docx"
    Document().save(str(template))

    articles = [_article()]
    path = write_shenyinxie_docx(
        title="深圳银行业协会工作动态（2026年7月第2期）",
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 29),
        issue_number="2026-14",
        articles=articles,
        output_dir=tmp_path / "out",
        template_path=template,
    )

    doc = Document(str(path))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "深圳银行业协会工作动态" in full_text
    assert "动态一" in full_text


def test_docx_output_cross_month_period_range(tmp_path):
    articles = [_article()]
    path = write_shenyinxie_docx(
        title="T",
        period_start=date(2026, 7, 16),
        period_end=date(2026, 8, 15),
        issue_number="2026-15",
        articles=articles,
        output_dir=tmp_path / "out",
        template_path=tmp_path / "nonexistent.docx",
    )

    doc = Document(str(path))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "2026年7月16日—8月15日" in full_text


def test_docx_output_includes_all_three_articles(tmp_path):
    articles = [
        _article(title="第一篇"),
        _article(title="第二篇"),
        _article(title="第三篇"),
    ]
    path = write_shenyinxie_docx(
        title="T",
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 29),
        issue_number="2026-14",
        articles=articles,
        output_dir=tmp_path / "out",
        template_path=tmp_path / "nonexistent.docx",
    )

    doc = Document(str(path))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "动态一" in full_text
    assert "动态二" in full_text
    assert "动态三" in full_text
    assert "第一篇" in full_text
    assert "第二篇" in full_text
    assert "第三篇" in full_text


def test_docx_output_path_issue_number_formatting(tmp_path):
    path = write_shenyinxie_docx(
        title="T",
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 29),
        issue_number="2026-14",
        articles=[_article()],
        output_dir=tmp_path / "out",
        template_path=tmp_path / "nonexistent.docx",
    )
    assert path.name == "深银协动态202614.docx"
