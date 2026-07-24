from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import subprocess
from zipfile import ZipFile

from lxml import etree
import pytest

from app.platform.documents.word_toc import (
    WordTocFinalizationError,
    finalize_word_toc,
    inspect_cached_toc,
)
from skills.internal_weekly.docx_output import DEFAULT_TEMPLATE_PATH


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DC_NS = "http://purl.org/dc/elements/1.1/"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
NS = {"w": W_NS}
W = f"{{{W_NS}}}"

EXPECTED_HEADINGS = (
    "党政要闻",
    "中央部署促进民营经济发展",
    "监管动态",
    "同业动向",
    "市场观察",
    "前沿观点",
)


def _replace_package_parts(path: Path, replacements: dict[str, bytes]) -> None:
    with ZipFile(path) as package:
        entries = [(info, package.read(info.filename)) for info in package.infolist()]
    temporary = path.with_name(f".{path.name}.rewrite")
    try:
        with ZipFile(temporary, "w") as output:
            for info, payload in entries:
                output.writestr(info, replacements.get(info.filename, payload))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _cache_toc(path: Path, headings: Sequence[str]) -> None:
    with ZipFile(path) as package:
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
        bookmark_name = f"_TocTest{index}"
        start = etree.SubElement(paragraph, f"{W}bookmarkStart")
        start.set(f"{W}id", str(1000 + index))
        start.set(f"{W}name", bookmark_name)
        end = etree.SubElement(paragraph, f"{W}bookmarkEnd")
        end.set(f"{W}id", str(1000 + index))

        title_run = etree.SubElement(paragraph, f"{W}r")
        title_node = etree.SubElement(title_run, f"{W}t")
        title_node.text = heading
        tab_run = etree.SubElement(paragraph, f"{W}r")
        etree.SubElement(tab_run, f"{W}tab")

        begin_run = etree.SubElement(paragraph, f"{W}r")
        begin = etree.SubElement(begin_run, f"{W}fldChar")
        begin.set(f"{W}fldCharType", "begin")
        instruction_run = etree.SubElement(paragraph, f"{W}r")
        instruction = etree.SubElement(instruction_run, f"{W}instrText")
        instruction.text = f" PAGEREF {bookmark_name} \\h "
        separate_run = etree.SubElement(paragraph, f"{W}r")
        separate = etree.SubElement(separate_run, f"{W}fldChar")
        separate.set(f"{W}fldCharType", "separate")
        page_run = etree.SubElement(paragraph, f"{W}r")
        page_node = etree.SubElement(page_run, f"{W}t")
        page_node.text = str(index + 1)
        end_run = etree.SubElement(paragraph, f"{W}r")
        field_end = etree.SubElement(end_run, f"{W}fldChar")
        field_end.set(f"{W}fldCharType", "end")

    _replace_package_parts(
        path,
        {
            "word/document.xml": etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )
        },
    )


def _set_personal_metadata(path: Path) -> None:
    with ZipFile(path) as package:
        core_xml = package.read("docProps/core.xml")
    root = etree.fromstring(core_xml)
    creator = root.find(f"{{{DC_NS}}}creator")
    modifier = root.find(f"{{{CP_NS}}}lastModifiedBy")
    assert creator is not None
    assert modifier is not None
    creator.text = "Local User"
    modifier.text = "Local User"
    _replace_package_parts(
        path,
        {
            "docProps/core.xml": etree.tostring(
                root,
                xml_declaration=True,
                encoding="UTF-8",
                standalone=True,
            )
        },
    )


def _fake_dependencies(tmp_path: Path) -> tuple[Path, Path, Path]:
    word_app = tmp_path / "Microsoft Word.app"
    word_app.mkdir()
    osascript = tmp_path / "osascript"
    osascript.write_text("#!/bin/sh\n", encoding="utf-8")
    osascript.chmod(0o700)
    script = tmp_path / "update_word_toc.applescript"
    script.write_text("on run argv\nend run\n", encoding="utf-8")
    return word_app, osascript, script


def test_finalize_word_toc_uses_fixed_script_and_requires_populated_cache(
    tmp_path: Path,
):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    document = output_dir / "weekly.docx"
    document.write_bytes(DEFAULT_TEMPLATE_PATH.read_bytes())
    _set_personal_metadata(document)
    word_app, osascript, script = _fake_dependencies(tmp_path)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        _cache_toc(Path(command[-1]), EXPECTED_HEADINGS)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="M_AGENT_TOC_OK:1\n",
            stderr="",
        )

    report = finalize_word_toc(
        document,
        allowed_root=output_dir,
        expected_headings=EXPECTED_HEADINGS,
        runner=runner,
        system_name="Darwin",
        word_app_path=word_app,
        osascript_path=osascript,
        script_path=script,
        lock_path=tmp_path / "word.lock",
        staging_root=tmp_path / "word-staging",
    )

    assert report.entry_count == len(EXPECTED_HEADINGS)
    assert report.page_numbers == tuple(range(2, len(EXPECTED_HEADINGS) + 2))
    assert calls == [
        (
            [
                str(osascript),
                str(script),
                str(calls[0][0][-1]),
            ],
            {
                "capture_output": True,
                "check": False,
                "env": {
                    "HOME": str(Path.home()),
                    "LANG": "zh_CN.UTF-8",
                    "LC_ALL": "zh_CN.UTF-8",
                    "PATH": "/usr/bin:/bin",
                    "TMPDIR": "/private/tmp",
                },
                "text": True,
                "timeout": 90,
            },
        )
    ]
    assert Path(calls[0][0][-1]).parent == tmp_path / "word-staging"
    assert not list((tmp_path / "word-staging").iterdir())
    with ZipFile(document) as package:
        core = etree.fromstring(package.read("docProps/core.xml"))
    assert core.find(f"{{{DC_NS}}}creator").text == "M-Agent"
    assert core.find(f"{{{CP_NS}}}lastModifiedBy").text == "M-Agent"


def test_finalize_word_toc_rejects_success_without_complete_cache(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    document = output_dir / "weekly.docx"
    document.write_bytes(DEFAULT_TEMPLATE_PATH.read_bytes())
    word_app, osascript, script = _fake_dependencies(tmp_path)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="M_AGENT_TOC_OK:1\n",
            stderr="",
        )

    with pytest.raises(WordTocFinalizationError, match="目录未完整生成"):
        finalize_word_toc(
            document,
            allowed_root=output_dir,
            expected_headings=EXPECTED_HEADINGS,
            runner=runner,
            system_name="Darwin",
            word_app_path=word_app,
            osascript_path=osascript,
            script_path=script,
            lock_path=tmp_path / "word.lock",
            staging_root=tmp_path / "word-staging",
        )


def test_finalize_word_toc_rejects_file_outside_allowed_root(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    document = tmp_path / "outside.docx"
    document.write_bytes(DEFAULT_TEMPLATE_PATH.read_bytes())
    word_app, osascript, script = _fake_dependencies(tmp_path)

    with pytest.raises(WordTocFinalizationError, match="当前任务输出目录"):
        finalize_word_toc(
            document,
            allowed_root=output_dir,
            expected_headings=EXPECTED_HEADINGS,
            system_name="Darwin",
            word_app_path=word_app,
            osascript_path=osascript,
            script_path=script,
            lock_path=tmp_path / "word.lock",
            staging_root=tmp_path / "word-staging",
        )


def test_finalize_word_toc_returns_safe_error_on_timeout(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    document = output_dir / "weekly.docx"
    document.write_bytes(DEFAULT_TEMPLATE_PATH.read_bytes())
    word_app, osascript, script = _fake_dependencies(tmp_path)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout=90)

    with pytest.raises(WordTocFinalizationError, match="后台更新目录超时"):
        finalize_word_toc(
            document,
            allowed_root=output_dir,
            expected_headings=EXPECTED_HEADINGS,
            runner=runner,
            system_name="Darwin",
            word_app_path=word_app,
            osascript_path=osascript,
            script_path=script,
            lock_path=tmp_path / "word.lock",
            staging_root=tmp_path / "word-staging",
        )
    assert not list((tmp_path / "word-staging").iterdir())


def test_inspect_cached_toc_reports_missing_heading(tmp_path: Path):
    document = tmp_path / "weekly.docx"
    document.write_bytes(DEFAULT_TEMPLATE_PATH.read_bytes())
    _cache_toc(document, EXPECTED_HEADINGS[:-1])

    with pytest.raises(WordTocFinalizationError, match="前沿观点"):
        inspect_cached_toc(document, expected_headings=EXPECTED_HEADINGS)
