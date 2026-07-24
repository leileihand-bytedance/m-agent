from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from lxml import etree


DEFAULT_WORD_APP_PATH = Path("/Applications/Microsoft Word.app")
DEFAULT_OSASCRIPT_PATH = Path("/usr/bin/osascript")
DEFAULT_SCRIPT_PATH = (
    Path(__file__).parent / "scripts" / "update_word_toc.applescript"
)
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_LOCK_TIMEOUT_SECONDS = 30
DEFAULT_LOCK_PATH = Path("/private/tmp/m-agent-word-toc.lock")
DEFAULT_STAGING_ROOT = (
    Path.home()
    / "Library"
    / "Containers"
    / "com.microsoft.Word"
    / "Data"
    / "Documents"
    / "M-Agent-TOC"
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
CUSTOM_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
)
NS = {"w": W_NS}
W = f"{{{W_NS}}}"

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class WordTocFinalizationError(RuntimeError):
    """The clean DOCX could not be finalized with a complete cached TOC."""


@dataclass(frozen=True)
class TocCacheReport:
    entry_count: int
    entries: tuple[str, ...]
    page_numbers: tuple[int, ...]


def finalize_word_toc(
    path: str | Path,
    *,
    allowed_root: str | Path,
    expected_headings: Sequence[str],
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    runner: CommandRunner = subprocess.run,
    system_name: str | None = None,
    word_app_path: str | Path = DEFAULT_WORD_APP_PATH,
    osascript_path: str | Path = DEFAULT_OSASCRIPT_PATH,
    script_path: str | Path = DEFAULT_SCRIPT_PATH,
    lock_path: str | Path = DEFAULT_LOCK_PATH,
    staging_root: str | Path = DEFAULT_STAGING_ROOT,
) -> TocCacheReport:
    """Refresh one task-local DOCX TOC in Word and verify the saved field cache."""
    target = _resolve_task_docx(path, allowed_root=allowed_root)
    headings = tuple(str(item).strip() for item in expected_headings if str(item).strip())
    if not headings:
        raise WordTocFinalizationError("Word 目录缺少预期标题，已停止生成")
    if (system_name or platform.system()) != "Darwin":
        raise WordTocFinalizationError("当前运行环境不支持 Microsoft Word 目录终稿")

    word_app = Path(word_app_path)
    osascript = Path(osascript_path)
    script = Path(script_path)
    if not word_app.is_dir():
        raise WordTocFinalizationError("未检测到 Microsoft Word，无法生成完整目录")
    if not osascript.is_file() or not os.access(osascript, os.X_OK):
        raise WordTocFinalizationError("系统 Word 自动化组件不可用")
    if not script.is_file():
        raise WordTocFinalizationError("Word 目录终稿脚本缺失")

    timeout = max(1, int(timeout_seconds))
    staging_dir = Path(staging_root)
    staged = staging_dir / f"m-agent-toc-{uuid4().hex}.docx"
    try:
        staging_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(target, staged)
        with _exclusive_lock(
            Path(lock_path),
            timeout_seconds=DEFAULT_LOCK_TIMEOUT_SECONDS,
        ):
            completed = runner(
                [str(osascript), str(script), str(staged)],
                capture_output=True,
                check=False,
                env=_word_automation_environment(),
                text=True,
                timeout=timeout,
            )
        if completed.returncode != 0 or "M_AGENT_TOC_OK:1" not in (completed.stdout or ""):
            raise WordTocFinalizationError("Microsoft Word 未能完成目录更新")
        _scrub_personal_metadata(staged)
        report = inspect_cached_toc(staged, expected_headings=headings)
        _replace_from_staging(staged, target)
        return report
    except subprocess.TimeoutExpired as exc:
        raise WordTocFinalizationError("Microsoft Word 后台更新目录超时") from exc
    except WordTocFinalizationError:
        raise
    except OSError as exc:
        raise WordTocFinalizationError("Microsoft Word 后台更新目录无法启动") from exc
    except Exception as exc:
        raise WordTocFinalizationError("Word 目录终稿校验失败") from exc
    finally:
        staged.unlink(missing_ok=True)


def inspect_cached_toc(
    path: str | Path,
    *,
    expected_headings: Sequence[str],
) -> TocCacheReport:
    """Verify that a saved DOCX contains exactly the expected TOC entries and pages."""
    target = Path(path)
    headings = tuple(str(item).strip() for item in expected_headings if str(item).strip())
    try:
        with ZipFile(target) as package:
            document_xml = package.read("word/document.xml")
        root = etree.fromstring(document_xml)
    except (BadZipFile, KeyError, OSError, etree.XMLSyntaxError) as exc:
        raise WordTocFinalizationError("Word 文件结构损坏，无法校验目录") from exc

    toc_nodes = root.xpath(
        "//w:sdt[.//w:instrText[contains(., 'TOC')]]",
        namespaces=NS,
    )
    if len(toc_nodes) != 1:
        raise WordTocFinalizationError("Word 自动目录数量异常")

    bookmark_names = set(root.xpath("//w:bookmarkStart/@w:name", namespaces=NS))
    entries: list[str] = []
    pages: list[int] = []
    missing_bookmarks: list[str] = []
    for paragraph in toc_nodes[0].xpath(
        ".//w:p[.//w:instrText[contains(., 'PAGEREF')]]",
        namespaces=NS,
    ):
        instructions = " ".join(
            str(value).strip()
            for value in paragraph.xpath(".//w:instrText/text()", namespaces=NS)
            if str(value).strip()
        )
        bookmark_match = re.search(r"\bPAGEREF\s+([^\s\\]+)", instructions)
        if bookmark_match and bookmark_match.group(1) not in bookmark_names:
            missing_bookmarks.append(bookmark_match.group(1))
        text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip()
        match = re.fullmatch(r"(.+?)\s*(\d+)", text)
        if match is None:
            continue
        entries.append(match.group(1).strip())
        pages.append(int(match.group(2)))

    expected_counts = Counter(headings)
    actual_counts = Counter(entries)
    missing = list((expected_counts - actual_counts).elements())
    unexpected = list((actual_counts - expected_counts).elements())
    if (
        missing
        or unexpected
        or len(entries) != len(headings)
        or any(page <= 0 for page in pages)
        or missing_bookmarks
    ):
        details: list[str] = []
        if missing:
            details.append(f"缺少：{'、'.join(missing)}")
        if unexpected:
            details.append(f"多出：{'、'.join(unexpected)}")
        if missing_bookmarks:
            details.append("存在失效页码书签")
        suffix = f"（{'；'.join(details)}）" if details else ""
        raise WordTocFinalizationError(f"Word 目录未完整生成{suffix}")

    visible_text = "\n".join(entries)
    if "Error!" in visible_text or "错误！" in visible_text:
        raise WordTocFinalizationError("Word 目录包含失效域")
    return TocCacheReport(
        entry_count=len(entries),
        entries=tuple(entries),
        page_numbers=tuple(pages),
    )


def _resolve_task_docx(path: str | Path, *, allowed_root: str | Path) -> Path:
    try:
        root = Path(allowed_root).resolve(strict=True)
        target = Path(path).resolve(strict=True)
    except OSError as exc:
        raise WordTocFinalizationError("Word 文件或当前任务输出目录不存在") from exc
    if not root.is_dir() or (target != root and root not in target.parents):
        raise WordTocFinalizationError("只允许处理当前任务输出目录内的 Word 文件")
    if not target.is_file() or target.suffix.lower() != ".docx":
        raise WordTocFinalizationError("目录终稿只支持当前任务内的 DOCX 文件")
    return target


def _word_automation_environment() -> dict[str, str]:
    return {
        "HOME": str(Path.home()),
        "LANG": "zh_CN.UTF-8",
        "LC_ALL": "zh_CN.UTF-8",
        "PATH": "/usr/bin:/bin",
        "TMPDIR": "/private/tmp" if Path("/private/tmp").is_dir() else tempfile.gettempdir(),
    }


def _replace_from_staging(staged: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.toc")
    try:
        shutil.copyfile(staged, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path, *, timeout_seconds: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    deadline = time.monotonic() + max(1, timeout_seconds)
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise WordTocFinalizationError("Word 目录终稿队列等待超时")
                time.sleep(0.1)
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _scrub_personal_metadata(path: Path) -> None:
    with ZipFile(path) as package:
        entries = [(info, package.read(info.filename)) for info in package.infolist()]

    replacements: dict[str, bytes] = {}
    for name in ("docProps/core.xml", "docProps/core0.xml"):
        payload = _part(entries, name)
        if payload is None:
            continue
        root = etree.fromstring(payload)
        for tag in (f"{{{DC_NS}}}creator", f"{{{CP_NS}}}lastModifiedBy"):
            node = root.find(tag)
            if node is not None:
                node.text = "M-Agent"
        printed = root.find(f"{{{CP_NS}}}lastPrinted")
        if printed is not None:
            root.remove(printed)
        replacements[name] = _serialize(root)

    custom_payload = _part(entries, "docProps/custom.xml")
    if custom_payload is not None:
        custom_root = etree.fromstring(custom_payload)
        if custom_root.tag == f"{{{CUSTOM_NS}}}Properties":
            for child in list(custom_root):
                custom_root.remove(child)
            replacements["docProps/custom.xml"] = _serialize(custom_root)

    temporary = path.with_name(f".{path.name}.{uuid4().hex}.privacy")
    try:
        with ZipFile(temporary, "w") as output:
            for info, payload in entries:
                output.writestr(info, replacements.get(info.filename, payload))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _part(entries: list[tuple[object, bytes]], name: str) -> bytes | None:
    return next(
        (
            payload
            for info, payload in entries
            if getattr(info, "filename", "") == name
        ),
        None,
    )


def _serialize(root: etree._Element) -> bytes:
    return etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )
