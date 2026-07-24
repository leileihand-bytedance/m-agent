from __future__ import annotations

from datetime import date
from pathlib import Path
from collections.abc import Sequence
from zipfile import ZipFile

import pytest
from lxml import etree

from skills.internal_weekly.docx_output import (
    DEFAULT_TEMPLATE_PATH,
    _issue_numbers,
    generate_internal_weekly_docx,
    is_explicit_word_approval,
    parse_approved_review,
    requests_clean_word,
    review_content_sha256,
)


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
W = f"{{{W_NS}}}"


def _cache_toc(path: Path, headings: Sequence[str]) -> None:
    with ZipFile(path) as package:
        entries = [(info, package.read(info.filename)) for info in package.infolist()]
        document_xml = package.read("word/document.xml")
    root = etree.fromstring(document_xml)
    toc_nodes = root.xpath(
        "//w:sdt[.//w:instrText[contains(., 'TOC')]]",
        namespaces=NS,
    )
    assert len(toc_nodes) == 1
    content = toc_nodes[0].find(f"{W}sdtContent")
    assert content is not None
    for index, heading in enumerate(headings, start=1):
        paragraph = etree.SubElement(content, f"{W}p")
        title_run = etree.SubElement(paragraph, f"{W}r")
        etree.SubElement(title_run, f"{W}t").text = heading
        instruction_run = etree.SubElement(paragraph, f"{W}r")
        etree.SubElement(instruction_run, f"{W}instrText").text = (
            f" PAGEREF _TocTest{index} \\h "
        )
        page_run = etree.SubElement(paragraph, f"{W}r")
        etree.SubElement(page_run, f"{W}t").text = "2"
    replacements = {
        "word/document.xml": etree.tostring(
            root,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        )
    }
    temporary = path.with_name(f".{path.name}.rewrite")
    try:
        with ZipFile(temporary, "w") as output:
            for info, payload in entries:
                output.writestr(info, replacements.get(info.filename, payload))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _approved_review() -> str:
    return """# 内参周报（2026-07-27）（内容核对稿）

出版日：2026-07-27｜统计期：2026-07-20 至 2026-07-26

## 党政要闻

### 1. 中央部署促进民营经济发展

中央有关会议部署进一步优化民营经济发展环境。

原文：[中央部署](https://www.gov.cn/example)

## 监管动态

### 1. 金融监管总局部署小微金融服务

金融监管总局部署提升小微企业金融服务质效。

原文：[监管部署](https://www.nfra.gov.cn/example)

## 同业动向

### 1. 某数字银行发布经营进展

该机构披露客户经营与风险管理的新进展。

原文：[经营进展](https://www.example-bank.com/report)

## 市场观察

### 1. 资本市场综述

上周A股、港股和美股主要指数以及周一A股收盘情况均已完成核验。

原文：[市场周报](https://www.example.com/market)

### 2. 全球主要央行释放新信号

主要央行政策信号影响全球流动性预期和银行资产负债管理判断。

原文：[央行动态](https://www.example.com/central-bank)

## 前沿观点

### 1. 数字金融基础设施的新变化

报告认为，数字金融基础设施正在改变支付效率和银行服务模式。

（来源：国际清算银行《Digital finance infrastructure》）

原文：[研究报告](https://www.bis.org/example)
"""


def _approval_metadata(**overrides: str) -> dict[str, str]:
    review = _approved_review()
    metadata = {
        "generation_mode": "full_weekly",
        "publication_date": "2026-07-27",
        "period_start": "2026-07-20",
        "period_end": "2026-07-26",
        "draft_version": "draft-20260727",
        "ready_for_approval": "true",
        "review_sha256": review_content_sha256(review),
    }
    metadata.update(overrides)
    return metadata


def _legacy_approved_review() -> str:
    return (
        _approved_review()
        .replace(
            "出版日：2026-07-27｜统计期：2026-07-20 至 2026-07-26",
            (
                "- 出版日：2026-07-27\n"
                "- 统计期：2026-07-20 至 2026-07-26\n"
                "- 草稿版本：`draft-20260727`\n"
                "- 状态：可提交人工核对"
            ),
        )
        .replace("原文：[", "核对信息：\n- 原文链接：[")
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("请生成 Word 洁净版", True),
        ("输出正式文档", True),
        ("继续生成内容核对稿", False),
    ],
)
def test_requests_clean_word(text: str, expected: bool):
    assert requests_clean_word(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("这版核对无误，请生成 Word 洁净版", True),
        ("确认通过，输出正式文档", True),
        ("请生成 Word 洁净版", False),
        ("没问题", False),
    ],
)
def test_explicit_word_approval_requires_approval_and_export(text: str, expected: bool):
    assert is_explicit_word_approval(text) is expected


def test_parse_approved_review_rejects_incomplete_or_changed_review():
    with pytest.raises(ValueError, match="尚未达到可批准状态"):
        parse_approved_review(
            _approved_review(),
            _approval_metadata(ready_for_approval="false"),
        )

    with pytest.raises(ValueError, match="核对稿内容与批准版本不一致"):
        parse_approved_review(
            _approved_review().replace("优化民营经济发展环境", "改变后的正文"),
            _approval_metadata(),
        )


def test_parse_approved_review_keeps_legacy_review_compatible():
    metadata = _approval_metadata()
    metadata.pop("review_sha256")

    draft = parse_approved_review(_legacy_approved_review(), metadata)

    assert draft.draft_version == "draft-20260727"
    assert [section.name for section in draft.sections] == [
        "党政要闻",
        "监管动态",
        "同业动向",
        "市场观察",
        "前沿观点",
    ]


def test_explicit_annual_and_total_issue_numbers_are_parsed_independently():
    assert _issue_numbers(
        date(2026, 9, 7),
        request_text="核对无误，生成 Word 洁净版，2026年第35期（总第416期）",
    ) == (35, 416)
    assert _issue_numbers(
        date(2026, 9, 7),
        request_text="核对无误，生成 Word 洁净版，总第416期",
    ) == (35, 416)


def test_sanitized_template_contains_no_case_content_or_personal_metadata():
    with ZipFile(DEFAULT_TEMPLATE_PATH) as package:
        document_xml = package.read("word/document.xml").decode("utf-8")
        core_xml = package.read("docProps/core.xml").decode("utf-8")
        core0_xml = package.read("docProps/core0.xml").decode("utf-8")
        custom_xml = etree.fromstring(package.read("docProps/custom.xml"))
        all_package_xml = "\n".join(
            package.read(name).decode("utf-8", errors="ignore")
            for name in package.namelist()
            if name.endswith((".xml", ".rels"))
        )

    for sample_text in (
        "民营银行持续缩减助贷合作机构",
        "《求是》杂志发表习近平总书记重要文章",
    ):
        assert sample_text not in document_xml
    for personal_name in ("johnny", "yakiyang", "wendy"):
        assert personal_name not in core_xml.lower()
        assert personal_name not in core0_xml.lower()
    assert len(custom_xml) == 0
    assert 'TargetMode="External"' not in all_package_xml


def test_generate_word_from_approved_review_preserves_template_and_toc(tmp_path: Path):
    draft = parse_approved_review(_approved_review(), _approval_metadata())
    finalizer_calls: list[tuple[Path, Path, tuple[str, ...]]] = []

    def finalizer(
        path: str | Path,
        *,
        allowed_root: str | Path,
        expected_headings: Sequence[str],
    ) -> object:
        resolved_path = Path(path).resolve()
        finalizer_calls.append(
            (
                resolved_path,
                Path(allowed_root).resolve(),
                tuple(expected_headings),
            )
        )
        _cache_toc(resolved_path, expected_headings)
        return object()

    output_path = generate_internal_weekly_docx(
        draft=draft,
        request_text="这版核对无误，请生成 Word 洁净版",
        output_dir=tmp_path,
        toc_finalizer=finalizer,
    )

    assert output_path.name == "微众银行信息内参周报-2026-07-27.docx"
    assert output_path.is_file()
    assert len(finalizer_calls) == 1
    assert finalizer_calls[0][0].parent == tmp_path.resolve()
    assert finalizer_calls[0][1] == tmp_path.resolve()
    assert finalizer_calls[0][2] == (
        "党政要闻",
        "中央部署促进民营经济发展",
        "监管动态",
        "金融监管总局部署小微金融服务",
        "同业动向",
        "某数字银行发布经营进展",
        "市场观察",
        "资本市场综述",
        "全球主要央行释放新信号",
        "前沿观点",
        "数字金融基础设施的新变化",
    )

    with ZipFile(DEFAULT_TEMPLATE_PATH) as template, ZipFile(output_path) as output:
        template_parts = {name: template.read(name) for name in template.namelist()}
        output_parts = {name: output.read(name) for name in output.namelist()}

    assert set(output_parts) == set(template_parts)
    for name, payload in template_parts.items():
        if name not in {"word/document.xml", "word/settings.xml"}:
            assert output_parts[name] == payload, name

    document = etree.fromstring(output_parts["word/document.xml"])
    settings = etree.fromstring(output_parts["word/settings.xml"])
    paragraph_rows = [
        (
            "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip(),
            (paragraph.xpath("./w:pPr/w:pStyle/@w:val", namespaces=NS) or [""])[0],
        )
        for paragraph in document.xpath("//w:body//w:p", namespaces=NS)
    ]
    heading_1 = [text for text, style in paragraph_rows if style == "1"]
    heading_3 = [text for text, style in paragraph_rows if style == "3"]
    all_text = "\n".join(text for text, _style in paragraph_rows)

    assert heading_1 == ["党政要闻", "监管动态", "同业动向", "市场观察", "前沿观点"]
    assert heading_3 == [
        "中央部署促进民营经济发展",
        "金融监管总局部署小微金融服务",
        "某数字银行发布经营进展",
        "资本市场综述",
        "全球主要央行释放新信号",
        "数字金融基础设施的新变化",
    ]
    assert "2026年第29期" in all_text
    assert "总410期" in all_text
    assert "发稿日期：2026年7月27日" in all_text
    assert "主编：" in all_text
    assert "责任编辑：" in all_text
    assert "国际清算银行《Digital finance infrastructure》" in all_text
    assert "核对信息" not in all_text
    assert "https://" not in all_text
    assert "草稿版本" not in all_text
    assert "待核事项" not in all_text
    assert "{{" not in all_text

    instructions = [
        "".join(node.itertext())
        for node in document.xpath("//w:instrText", namespaces=NS)
    ]
    assert any('TOC \\o "1-3" \\h \\z \\u' in instruction for instruction in instructions)
    assert sum("PAGEREF" in instruction for instruction in instructions) == 11
    assert "_Toc" in output_parts["word/document.xml"].decode("utf-8")
    assert not document.xpath("//w:fldChar[@w:dirty]", namespaces=NS)
    assert not settings.xpath("/w:settings/w:updateFields", namespaces=NS)
    assert document.xpath(
        "//w:body/w:p[w:r/w:br[@w:type='page']]",
        namespaces=NS,
    )


def test_generate_word_does_not_replace_existing_file_when_toc_finalization_fails(
    tmp_path: Path,
):
    draft = parse_approved_review(_approved_review(), _approval_metadata())
    target = tmp_path / "微众银行信息内参周报-2026-07-27.docx"
    target.write_bytes(b"previous-approved-version")

    def failing_finalizer(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("toc finalization failed")

    with pytest.raises(RuntimeError, match="toc finalization failed"):
        generate_internal_weekly_docx(
            draft=draft,
            request_text="这版核对无误，请生成 Word 洁净版",
            output_dir=tmp_path,
            toc_finalizer=failing_finalizer,
        )

    assert target.read_bytes() == b"previous-approved-version"
    assert not [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(".微众银行信息内参周报-2026-07-27")
    ]
