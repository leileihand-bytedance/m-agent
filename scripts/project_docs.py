from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
TODO_PATH = ROOT / "docs/development/TODO.md"
STATUS_REPORT_INDEX_PATH = ROOT / "STATUS-REPORT.md"
STATUS_REPORT_DIR_ENV = "M_AGENT_STATUS_REPORT_DIR"
DATA_ROOT_ENV = "M_AGENT_DATA_DIR"
DEFAULT_STATUS_REPORT_DIR = ROOT.parent / "M-Agent-Files" / "runtime" / "development-logs"
LOCAL_ONLY_PATHS = {
    "STATUS-REPORT.md": "/STATUS-REPORT.md",
    "config/platform-policy.yaml": "/config/platform-policy.yaml",
}
VALID_TODO_STATUSES = {"未开始", "进行中", "已暂缓"}
TODO_HEADING_RE = re.compile(r"^### TODO-(\d+)：(.+)$", re.MULTILINE)
TODO_STATUS_RE = re.compile(r"^状态：([^\n]+)$", re.MULTILINE)
TODO_REFERENCE_RE = re.compile(r"TODO-(\d+)")
STATUS_SECTION_RE = re.compile(r"^## .+$", re.MULTILINE)
STATUS_MONTH_RE = re.compile(r"\[(\d{4}-\d{2})-\d{2}(?:[^\]]*)\]")
ACTIVE_PLAN_RE = re.compile(r"^docs/plans/\d{4}-\d{2}-\d{2}-.+-(?:design|plan)\.md$")
DATED_DOCUMENT_RE = re.compile(r"(?:19|20)\d{2}-\d{2}-\d{2}|(?:19|20)\d{6}")
ROOT_TEMP_SCRIPT_RE = re.compile(
    r"^(?:(?:test|debug|tmp|temp|audit)_.+\.py|full_audit\.py|parse_result\.py|run_audit\.(?:py|sh))$"
)
REQUIRED_DOCUMENT_PATHS = {
    "docs/README.md",
    "docs/development/README.md",
    "docs/development/TODO.md",
    "docs/development/architecture.md",
    "docs/development/directory-standard.md",
    "docs/development/testing-and-delivery.md",
    "docs/development/status-report.md",
    "docs/agent-platform/README.md",
    "docs/capabilities/README.md",
    "docs/operations/bots.md",
    "docs/knowledge/README.md",
    "docs/history/README.md",
    "docs/plans/README.md",
}
CORE_DOCUMENTS = {
    "AGENTS.md",
    "CLAUDE.md",
    "docs/README.md",
    "docs/agent-platform/README.md",
    "docs/capabilities/README.md",
    "docs/capabilities/brief-writing.md",
    "docs/capabilities/research-synthesis.md",
    "docs/capabilities/review.md",
    "docs/capabilities/rewrite.md",
    "docs/capabilities/shenyinxie-news.md",
    "docs/development/README.md",
    "docs/development/TODO.md",
    "docs/development/directory-standard.md",
    "docs/operations/admin-console.md",
    "docs/development/architecture.md",
    "docs/knowledge/bank.md",
    "docs/knowledge/policy.md",
    "docs/knowledge/README.md",
    "docs/development/codex-claude-workflow.md",
    "docs/development/status-report.md",
    "docs/development/testing-and-delivery.md",
    "docs/operations/bots.md",
    "docs/history/README.md",
    "docs/plans/README.md",
}
SOURCE_SUFFIXES = (".py", ".yaml", ".yml", ".json", ".env", ".txt")
MAC_HOME_PREFIX = "/" + "Users" + "/"
CHANGE_AREA_RULES = (
    (
        "依赖与交付",
        lambda path: path in {".python-version", "pyproject.toml", "uv.lock"}
        or path.startswith("app/requirements"),
    ),
    ("运维", lambda path: path.startswith("app/platform/ops/")),
    ("底座", lambda path: path.startswith("app/platform/") and not path.startswith("app/platform/ops/")),
    ("写作入口", lambda path: path.startswith("app/writing/")),
    ("审核", lambda path: path.startswith("app/review/") or path == "app/data/rules.md"),
    ("Skills", lambda path: path.startswith("skills/")),
    ("配置", lambda path: path.startswith("config/") or path.endswith(".env")),
    ("文档与规范", lambda path: path.startswith("docs/") or path in {"AGENTS.md", "CLAUDE.md", "README.md"}),
    ("测试", lambda path: path.startswith("tests/")),
    ("工程化", lambda path: path.startswith(("scripts/", ".githooks/"))),
)


def validate_todo_document(text: str) -> list[str]:
    """检查 TODO 编号唯一性和状态值。"""
    errors: list[str] = []
    matches = list(TODO_HEADING_RE.finditer(text))
    defined_ids = {match.group(1) for match in matches}
    seen: dict[str, str] = {}
    for index, match in enumerate(matches):
        todo_id, title = match.groups()
        if todo_id in seen:
            errors.append(f"TODO-{todo_id} 编号重复：{seen[todo_id]} / {title}")
        else:
            seen[todo_id] = title

        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.end() : section_end]
        status_match = TODO_STATUS_RE.search(section)
        if status_match is None:
            errors.append(f"TODO-{todo_id} 缺少状态")
            continue
        status = status_match.group(1).strip()
        if status not in VALID_TODO_STATUSES:
            errors.append(f"TODO-{todo_id} 状态无效：{status}")

        for reference in sorted(set(TODO_REFERENCE_RE.findall(section))):
            if reference not in defined_ids:
                errors.append(f"TODO-{todo_id} 引用了不存在的 TODO-{reference}")

    if matches:
        for reference in sorted(set(TODO_REFERENCE_RE.findall(text[: matches[0].start()]))):
            if reference not in defined_ids:
                errors.append(f"统筹视图引用了不存在的 TODO-{reference}")
    return errors


def document_tree_errors(paths: Iterable[str]) -> list[str]:
    normalized = {path.replace("\\", "/") for path in paths}
    errors: list[str] = []
    if any(path.startswith("docs/superpowers/") for path in normalized):
        errors.append("历史目录 docs/superpowers/ 已停用，请使用 docs/plans/ 或 docs/history/")
    if any(path.startswith("docs/archive/") for path in normalized):
        errors.append("历史目录 docs/archive/ 已停用，请使用 docs/history/")
    for path in sorted(normalized):
        if (
            path.startswith("docs/")
            and not path.startswith(("docs/history/", "docs/plans/"))
            and DATED_DOCUMENT_RE.search(Path(path).name)
        ):
            errors.append(f"当前事实文档文件名不得带日期：{path}")
    for path in sorted(normalized):
        if path.startswith("docs/plans/") and path != "docs/plans/README.md":
            if ACTIVE_PLAN_RE.fullmatch(path) is None:
                errors.append(f"当前计划命名不规范：{path}")
    for path in sorted(REQUIRED_DOCUMENT_PATHS - normalized):
        errors.append(f"缺少文档体系必需文件：{path}")
    return errors


def root_layout_errors(paths: Iterable[str]) -> list[str]:
    errors: list[str] = []
    root_files = sorted(
        path.replace("\\", "/")
        for path in paths
        if "/" not in path.replace("\\", "/")
    )
    for path in root_files:
        if path == ".DS_Store":
            errors.append("根目录存在系统临时文件：.DS_Store")
        elif ROOT_TEMP_SCRIPT_RE.fullmatch(path):
            errors.append(f"根目录存在一次性脚本：{path}")
    return errors


def is_core_document(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in CORE_DOCUMENTS:
        return True
    if normalized.startswith("docs/capabilities/direct-report/") and normalized.endswith(".md"):
        return True
    if normalized.startswith("app/") and normalized.endswith("/README.md"):
        return True
    if re.fullmatch(r"skills/[^/]+/(?:SKILL|README)\.md", normalized):
        return True
    return False


def has_core_document_change(paths: Iterable[str]) -> bool:
    return any(is_core_document(path) for path in paths)


def requires_core_document_change(paths: Iterable[str]) -> bool:
    return any(_is_behavior_change(path) for path in paths)


def documentation_sync_errors(paths: Iterable[str]) -> list[str]:
    """要求行为变更与对应模块的核心文档在同一提交中同步。"""
    normalized_paths = {path.replace("\\", "/") for path in paths}
    behavior_paths = {path for path in normalized_paths if _is_behavior_change(path)}
    if not behavior_paths:
        return []

    errors: list[str] = []
    changed_docs = {path for path in normalized_paths if is_core_document(path)}

    def require(label: str, affected: bool, allowed_documents: set[str]) -> None:
        if affected and changed_docs.isdisjoint(allowed_documents):
            expected = "、".join(sorted(allowed_documents))
            errors.append(f"{label}有行为变更，但未同步对应核心文档（可选：{expected}）")

    require(
        "底座",
        any(path.startswith("app/platform/") for path in behavior_paths),
        {
            "docs/agent-platform/README.md",
            "docs/development/architecture.md",
            "docs/operations/bots.md",
        },
    )
    require(
        "写作入口",
        any(path.startswith("app/writing/") for path in behavior_paths),
        {
            "app/writing/README.md",
            "docs/operations/bots.md",
        },
    )
    require(
        "审核入口",
        any(path.startswith("app/review/") for path in behavior_paths),
        {
            "app/review/README.md",
            "docs/capabilities/review.md",
            "docs/operations/bots.md",
        },
    )
    require(
        "配置",
        any(path.startswith("config/") or path == "app/config.example.env" for path in behavior_paths),
        {
            "docs/operations/admin-console.md",
            "docs/development/architecture.md",
            "docs/operations/bots.md",
        },
    )
    require(
        "依赖与交付机制",
        any(
            path.startswith(("scripts/", ".githooks/"))
            or path.startswith("app/requirements")
            or path in {"pyproject.toml", "uv.lock", ".python-version"}
            for path in behavior_paths
        ),
        {
            "AGENTS.md",
            "CLAUDE.md",
            "docs/development/codex-claude-workflow.md",
            "docs/development/testing-and-delivery.md",
        },
    )

    changed_skills = {
        path.split("/", 2)[1]
        for path in behavior_paths
        if path.startswith("skills/") and len(path.split("/", 2)) >= 3
    }
    for skill_id in sorted(changed_skills):
        require(
            f"Skill {skill_id}",
            True,
            {
                f"skills/{skill_id}/SKILL.md",
            },
        )

    covered_prefixes = ("app/platform/", "app/writing/", "app/review/")
    other_app_modules = {
        path.split("/", 2)[1]
        for path in behavior_paths
        if path.startswith("app/")
        and not path.startswith(covered_prefixes)
        and path != "app/config.example.env"
        and not path.startswith("app/requirements")
        and len(path.split("/", 2)) >= 3
    }
    for module in sorted(other_app_modules):
        module_documents = {
            "admin": {"app/admin/README.md", "docs/operations/admin-console.md"},
            "bank_knowledge": {"docs/knowledge/bank.md"},
            "policy_knowledge": {"docs/knowledge/policy.md"},
            "policy_research": {"docs/knowledge/policy.md"},
            "rewrite_bot": {"app/rewrite_bot/README.md", "docs/operations/bots.md"},
        }.get(
            module,
            {f"app/{module}/README.md", "docs/development/architecture.md"},
        )
        require(
            f"模块 app/{module}",
            True,
            module_documents,
        )
    return errors


def _is_behavior_change(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in {"pyproject.toml", "uv.lock", ".python-version"}:
        return True
    if normalized.startswith(("app/", "skills/", "scripts/", "config/")):
        return normalized.endswith(SOURCE_SUFFIXES) or normalized.startswith("app/requirements")
    return normalized.startswith(".githooks/")


def parse_sync_counts(output: str) -> tuple[int, int]:
    """解析 `git rev-list --left-right --count` 的远端独有/本地独有提交数。"""
    parts = output.strip().split()
    if len(parts) != 2:
        raise ValueError("无法解析 Git 同步状态")
    try:
        behind, ahead = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("无法解析 Git 同步状态") from exc
    if behind < 0 or ahead < 0:
        raise ValueError("Git 同步状态不能为负数")
    return behind, ahead


def repository_sync_counts(
    *,
    upstream_ref: str = "@{upstream}",
    local_ref: str = "HEAD",
) -> tuple[int, int]:
    result = _run_git(
        "rev-list",
        "--left-right",
        "--count",
        f"{upstream_ref}...{local_ref}",
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "当前分支没有可用的远端跟踪分支"
        raise RuntimeError(detail)
    return parse_sync_counts(result.stdout)


def build_sync_status_message(*, behind: int, ahead: int) -> str:
    if behind == 0 and ahead == 0:
        return "本地分支与远端已同步。"
    parts: list[str] = []
    if behind:
        parts.append(f"远端有 {behind} 个新提交尚未同步到本地")
    if ahead:
        parts.append(f"本地有 {ahead} 个提交尚未推送")
    if behind and ahead:
        return "本地与远端已分叉：" + "；".join(parts) + "。禁止强推，请先安全合并。"
    return "；".join(parts) + "。"


def check_sync(*, warn_only: bool = False, upstream_ref: str = "@{upstream}") -> int:
    try:
        behind, ahead = repository_sync_counts(upstream_ref=upstream_ref)
        message = build_sync_status_message(behind=behind, ahead=ahead)
    except (RuntimeError, ValueError) as exc:
        prefix = "WARN" if warn_only else "ERROR"
        print(f"{prefix}: 无法检查 Git 远端同步状态：{exc}", file=sys.stderr)
        return 0 if warn_only else 1

    if behind == 0 and ahead == 0:
        print(message)
        return 0
    prefix = "WARN" if warn_only else "ERROR"
    print(f"{prefix}: {message}", file=sys.stderr)
    return 0 if warn_only else 1


def classify_changed_areas(paths: Iterable[str]) -> tuple[str, ...]:
    normalized_paths = tuple(path.replace("\\", "/") for path in paths)
    areas = tuple(
        label
        for label, predicate in CHANGE_AREA_RULES
        if any(predicate(path) for path in normalized_paths)
    )
    return areas or ("其他",)


def status_report_directory(values: dict[str, str] | None = None) -> Path:
    environment = values if values is not None else os.environ
    raw_path = str(environment.get(STATUS_REPORT_DIR_ENV, "") or "").strip()
    raw_data_root = str(environment.get(DATA_ROOT_ENV, "") or "").strip()
    if raw_path:
        path = Path(raw_path).expanduser()
    elif raw_data_root:
        path = Path(raw_data_root).expanduser() / "runtime" / "development-logs"
    else:
        path = DEFAULT_STATUS_REPORT_DIR
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve(strict=False)


def monthly_report_path(report_dir: Path, timestamp: datetime) -> Path:
    return report_dir / f"{timestamp.strftime('%Y-%m')}.md"


def write_status_index(*, index_path: Path, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_dir.chmod(0o700)
    month_files = sorted(report_dir.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].md"), reverse=True)
    legacy_file = report_dir / "legacy-undated.md"
    display_path = os.path.relpath(report_dir, index_path.parent)
    lines = [
        "<!-- monthly-status-index -->",
        "# M-Agent 月度开发日志索引",
        "",
        "> 本文件只保留在本机，不进入 Git。完整开发记录按月保存在仓库外运行数据目录。",
        "",
        f"日志目录：`{display_path}`",
        "",
        "## 已有月份",
        "",
    ]
    if month_files:
        lines.extend(f"- `{path.name}`" for path in month_files)
    else:
        lines.append("- 暂无月度记录")
    if legacy_file.exists():
        lines.append("- `legacy-undated.md`（旧日志中无法识别月份的记录）")
    lines.extend(
        [
            "",
            "新记录由 `scripts/project_docs.py push` 在远端推送成功后自动写入当月文件。",
            "",
        ]
    )
    index_path.write_text("\n".join(lines), encoding="utf-8")


def migrate_status_report(*, source_path: Path, report_dir: Path) -> dict[str, int]:
    if not source_path.exists():
        write_status_index(index_path=source_path, report_dir=report_dir)
        return {}
    source = source_path.read_text(encoding="utf-8")
    if "<!-- monthly-status-index -->" in source:
        return {}

    matches = list(STATUS_SECTION_RE.finditer(source))
    if not matches:
        write_status_index(index_path=source_path, report_dir=report_dir)
        return {}

    grouped: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        section = source[match.start() : section_end].strip() + "\n\n"
        month_match = STATUS_MONTH_RE.search(match.group(0))
        key = month_match.group(1) if month_match else "legacy-undated"
        grouped.setdefault(key, []).append(section)

    report_dir.mkdir(parents=True, exist_ok=True)
    report_dir.chmod(0o700)
    for key, sections in grouped.items():
        target = report_dir / f"{key}.md"
        current = target.read_text(encoding="utf-8") if target.exists() else _new_report_header(key)
        for section in sections:
            if section.strip() not in current:
                current = current.rstrip() + "\n\n" + section
        target.write_text(current.rstrip() + "\n", encoding="utf-8")
        target.chmod(0o600)

    for key, sections in grouped.items():
        migrated = (report_dir / f"{key}.md").read_text(encoding="utf-8")
        if any(section.strip() not in migrated for section in sections):
            raise RuntimeError(f"{key} 月度日志迁移校验失败，根目录索引未改写")

    write_status_index(index_path=source_path, report_dir=report_dir)
    return {key: len(sections) for key, sections in grouped.items()}


def record_push_status(
    *,
    report_path: Path,
    remote: str,
    branch: str,
    before_hash: str,
    after_hash: str,
    commit_subjects: tuple[str, ...],
    changed_paths: tuple[str, ...],
    summary: str,
    impact: str,
    verification: str,
    next_step: str,
    timestamp: datetime | None = None,
) -> None:
    """在远端推送成功后记录功能、能力变化和后续边界。"""
    moment = timestamp or datetime.now().astimezone()
    marker = f"push:{remote}/{branch}:{after_hash}"
    areas = "、".join(classify_changed_areas(changed_paths))
    completed = summary.strip()
    capability_impact = impact.strip()
    verified = verification.strip()
    boundary = next_step.strip()
    if not completed or not capability_impact or not verified or not boundary:
        raise ValueError("开发日志必须说明完成功能、能力变化、关键验证和当前边界/下一步")
    entry = (
        f"## [{moment.strftime('%Y-%m-%d %H:%M')}] 开发进展\n\n"
        f"- 完成功能：{completed}\n"
        f"- 能力变化：{capability_impact}\n"
        f"- 关键验证：{verified}\n"
        f"- 当前边界/下一步：{boundary}\n"
        f"- 影响模块：{areas}\n"
        f"- 交付状态：已同步到远端 `{remote}/{branch}`。\n"
        f"- 技术追溯：`{after_hash}`\n"
        "- 记录方式：受管推送成功后自动生成；不包含用户材料、密钥或运行日志。\n"
        f"<!-- {marker} -->\n\n"
        "---\n\n"
    )
    _insert_status_entry(report_path=report_path, entry=entry, unique_marker=marker)


def _insert_status_entry(*, report_path: Path, entry: str, unique_marker: str) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.chmod(0o700)
    if report_path.exists():
        current = report_path.read_text(encoding="utf-8")
    else:
        month = report_path.stem if re.fullmatch(r"\d{4}-\d{2}", report_path.stem) else None
        current = _new_report_header(month)
    if unique_marker in current:
        return
    marker = "\n---\n"
    marker_index = current.find(marker)
    if marker_index >= 0:
        insert_at = marker_index + len(marker)
        updated = current[:insert_at] + "\n" + entry + current[insert_at:].lstrip("\n")
    else:
        updated = _new_report_header() + entry + current
    report_path.write_text(updated, encoding="utf-8")
    report_path.chmod(0o600)


def collect_push_details(before_hash: str, after_hash: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    subjects = tuple(
        line.strip()
        for line in _run_git(
            "log",
            "--reverse",
            "--format=%s",
            f"{before_hash}..{after_hash}",
        ).stdout.splitlines()
        if line.strip()
    )
    changed_paths = tuple(
        line.strip()
        for line in _run_git("diff", "--name-only", before_hash, after_hash).stdout.splitlines()
        if line.strip()
    )
    return subjects, changed_paths


def push_and_record(
    *,
    remote: str,
    branch: str,
    summary: str,
    impact: str,
    verification: str,
    next_step: str,
) -> None:
    if not summary.strip() or not impact.strip() or not verification.strip() or not next_step.strip():
        raise RuntimeError("开发日志必须说明完成功能、能力变化、关键验证和当前边界/下一步")
    if _run_git("status", "--porcelain").stdout.strip():
        raise RuntimeError("工作区存在未提交变更，请先完成测试和提交")
    current_branch = _run_git("branch", "--show-current").stdout.strip()
    if current_branch != branch:
        raise RuntimeError(f"当前分支是 {current_branch or 'detached HEAD'}，要求推送 {branch}")

    fetched = _run_git("fetch", remote, branch, check=False)
    if fetched.returncode != 0:
        raise RuntimeError(fetched.stderr.strip() or "获取远端状态失败")

    remote_ref = f"{remote}/{branch}"
    before_hash = _run_git("rev-parse", remote_ref).stdout.strip()
    after_hash = _run_git("rev-parse", branch).stdout.strip()
    if before_hash == after_hash:
        print("本地分支与远端已同步，没有需要推送的新提交。")
        return
    ancestor = _run_git("merge-base", "--is-ancestor", before_hash, after_hash, check=False)
    if ancestor.returncode != 0:
        raise RuntimeError("远端包含本地尚未合并的提交，禁止自动推送或强推")

    commit_subjects, changed_paths = collect_push_details(before_hash, after_hash)
    environment = dict(os.environ)
    environment["M_AGENT_MANAGED_PUSH"] = "1"
    pushed = subprocess.run(
        ("git", "push", remote, f"{branch}:{branch}"),
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )
    if pushed.stdout.strip():
        print(pushed.stdout.strip())
    if pushed.stderr.strip():
        print(pushed.stderr.strip(), file=sys.stderr)
    if pushed.returncode != 0:
        raise RuntimeError("Git 推送失败，状态报告未写入推送记录")

    confirmed_hash = _run_git("rev-parse", remote_ref).stdout.strip()
    if confirmed_hash != after_hash:
        raise RuntimeError("远端推送已返回成功，但本地远端引用未同步；请人工核对")
    moment = datetime.now().astimezone()
    report_dir = status_report_directory()
    report_path = monthly_report_path(report_dir, moment)
    record_push_status(
        report_path=report_path,
        remote=remote,
        branch=branch,
        before_hash=before_hash,
        after_hash=after_hash,
        commit_subjects=commit_subjects,
        changed_paths=changed_paths,
        summary=summary,
        impact=impact,
        verification=verification,
        next_step=next_step,
        timestamp=moment,
    )
    write_status_index(index_path=STATUS_REPORT_INDEX_PATH, report_dir=report_dir)
    print(f"推送成功，已写入本机月度开发日志 {report_path.name}。")


def check_repository(*, staged: bool = False) -> list[str]:
    errors: list[str] = []
    if staged:
        repository_paths = {
            line.strip()
            for line in _run_git("ls-files").stdout.splitlines()
            if line.strip()
        }
        document_paths = {
            path for path in repository_paths if path.startswith("docs/")
        }
    else:
        repository_paths = {
            path.name for path in ROOT.iterdir() if path.is_file()
        }
        document_paths = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "docs").rglob("*")
            if path.is_file()
        }
    errors.extend(root_layout_errors(repository_paths))
    errors.extend(document_tree_errors(document_paths))
    todo_text = _repository_text("docs/development/TODO.md", staged=staged)
    if not todo_text:
        errors.append("docs/development/TODO.md 不得删除或留空")
    else:
        errors.extend(validate_todo_document(todo_text))

    ignore_lines = _repository_text(".gitignore", staged=staged).splitlines()
    for local_path, ignore_rule in LOCAL_ONLY_PATHS.items():
        if ignore_rule not in ignore_lines:
            errors.append(f"{local_path} 必须作为本地文件写入 .gitignore")

    for local_path in LOCAL_ONLY_PATHS:
        tracked = _run_git("ls-files", "--error-unmatch", local_path, check=False)
        if tracked.returncode == 0:
            errors.append(f"{local_path} 仍被 Git 跟踪，请执行 git rm --cached {local_path}")

    if staged:
        changes = _staged_changes()
        paths = [path for _, path in changes]
        for local_path in LOCAL_ONLY_PATHS:
            if any(path == local_path and status != "D" for status, path in changes):
                errors.append(f"{local_path} 是本地文件，不允许暂存")
        errors.extend(documentation_sync_errors(paths))
        errors.extend(_staged_content_errors(changes))
    return errors


def _repository_text(path: str, *, staged: bool) -> str:
    if staged:
        result = _run_git("show", f":{path}", check=False)
        return result.stdout if result.returncode == 0 else ""
    target = ROOT / path
    return target.read_text(encoding="utf-8") if target.exists() else ""


def _staged_content_errors(changes: Iterable[tuple[str, str]]) -> list[str]:
    errors: list[str] = []
    binary_suffixes = {
        ".docx",
        ".pdf",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".mp4",
        ".mp3",
        ".zip",
        ".tar",
        ".gz",
    }
    for status, path in changes:
        if status == "D" or path.startswith("archive/"):
            continue
        if any(path.lower().endswith(suffix) for suffix in binary_suffixes):
            continue
        result = _run_git("show", f":{path}", check=False)
        if result.returncode != 0 or "\x00" in result.stdout:
            continue
        if MAC_HOME_PREFIX in result.stdout:
            errors.append(f"{path} 含 Mac 本机绝对路径，请改为相对路径或通用占位符")
    return errors


def install_hooks() -> None:
    _run_git("config", "core.hooksPath", ".githooks")


def _staged_changes() -> list[tuple[str, str]]:
    output = _run_git("diff", "--cached", "--name-status", "--diff-filter=ACMRD").stdout
    changes: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            changes.append((parts[0][0], parts[-1]))
    return changes


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _new_report_header(month: str | None = None) -> str:
    title = f"M-Agent 开发日志 {month}" if month else "M-Agent 开发日志"
    return (
        f"# {title}\n\n"
        "> 本文件只保留在本机，记录开发完成的功能、能力变化、关键验证和后续边界；不进入版本库。\n\n"
        "---\n\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M-Agent 核心文档同步与本地状态记录")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="检查核心文档和 TODO")
    check_parser.add_argument("--staged", action="store_true", help="同时检查暂存区文档同步")
    sync_parser = subparsers.add_parser("check-sync", help="检查当前分支是否与远端跟踪分支同步")
    sync_parser.add_argument("--warn-only", action="store_true", help="仅告警，不阻止提交后流程")
    sync_parser.add_argument("--upstream", default="@{upstream}", help="要比较的远端引用")
    push_parser = subparsers.add_parser("push", help="安全推送并在本机状态报告记录改动")
    push_parser.add_argument("--remote", default="origin")
    push_parser.add_argument("--branch", default="main")
    push_parser.add_argument("--summary", required=True, help="本次完成了什么功能")
    push_parser.add_argument("--impact", required=True, help="实际改变了什么能力或用户体验")
    push_parser.add_argument("--verification", required=True, help="完成了哪些关键验证")
    push_parser.add_argument("--next-step", required=True, help="当前边界、遗留问题或下一步")
    record_push_parser = subparsers.add_parser("record-push", help="补记一次已完成的推送")
    record_push_parser.add_argument("--remote", default="origin")
    record_push_parser.add_argument("--branch", default="main")
    record_push_parser.add_argument("--before", required=True)
    record_push_parser.add_argument("--after", required=True)
    record_push_parser.add_argument("--summary", required=True)
    record_push_parser.add_argument("--impact", required=True)
    record_push_parser.add_argument("--verification", required=True)
    record_push_parser.add_argument("--next-step", required=True)
    migrate_parser = subparsers.add_parser("migrate-status-report", help="把旧根目录开发日志按月迁移")
    migrate_parser.add_argument("--source", type=Path, default=STATUS_REPORT_INDEX_PATH)
    migrate_parser.add_argument("--report-dir", type=Path, default=None)
    subparsers.add_parser("install-hooks", help="启用仓库内 Git hooks")
    args = parser.parse_args(argv)

    if args.command == "check":
        errors = check_repository(staged=args.staged)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("核心文档检查通过。")
        return 0
    if args.command == "check-sync":
        return check_sync(warn_only=args.warn_only, upstream_ref=args.upstream)
    if args.command == "push":
        try:
            push_and_record(
                remote=args.remote,
                branch=args.branch,
                summary=args.summary,
                impact=args.impact,
                verification=args.verification,
                next_step=args.next_step,
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "record-push":
        commit_subjects, changed_paths = collect_push_details(args.before, args.after)
        moment = datetime.now().astimezone()
        report_dir = status_report_directory()
        report_path = monthly_report_path(report_dir, moment)
        record_push_status(
            report_path=report_path,
            remote=args.remote,
            branch=args.branch,
            before_hash=args.before,
            after_hash=args.after,
            commit_subjects=commit_subjects,
            changed_paths=changed_paths,
            summary=args.summary,
            impact=args.impact,
            verification=args.verification,
            next_step=args.next_step,
            timestamp=moment,
        )
        write_status_index(index_path=STATUS_REPORT_INDEX_PATH, report_dir=report_dir)
        print(f"已补记到月度开发日志 {report_path.name}。")
        return 0
    if args.command == "migrate-status-report":
        report_dir = args.report_dir or status_report_directory()
        try:
            result = migrate_status_report(source_path=args.source, report_dir=report_dir)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if result:
            detail = "、".join(f"{month} {count} 条" for month, count in result.items())
            print(f"旧开发日志迁移完成：{detail}。")
        else:
            print("开发日志已经使用月度结构，无需重复迁移。")
        return 0
    install_hooks()
    print("已启用 .githooks。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
