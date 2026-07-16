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
STATUS_REPORT_PATH = ROOT / "STATUS-REPORT.md"
LOCAL_ONLY_PATHS = {
    "STATUS-REPORT.md": "/STATUS-REPORT.md",
    "config/platform-policy.yaml": "/config/platform-policy.yaml",
}
VALID_TODO_STATUSES = {"未开始", "进行中", "已暂缓", "已完成", "已取消"}
TODO_HEADING_RE = re.compile(r"^### TODO-(\d+)：(.+)$", re.MULTILINE)
TODO_STATUS_RE = re.compile(r"^状态：([^\n]+)$", re.MULTILINE)
CORE_DOCUMENTS = {
    "AGENTS.md",
    "CLAUDE.md",
    "docs/README.md",
    "docs/agent-platform/README.md",
    "docs/capabilities/README.md",
    "docs/development/README.md",
    "docs/development/TODO.md",
    "docs/development/admin-console.md",
    "docs/development/architecture.md",
    "docs/development/bank-knowledge-base.md",
    "docs/development/codex-claude-workflow.md",
    "docs/development/status-report.md",
    "docs/development/testing-and-delivery.md",
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
    return errors


def is_core_document(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized in CORE_DOCUMENTS:
        return True
    if normalized.startswith("docs/development/direct-report-") and normalized.endswith(".md"):
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
            "docs/development/TODO.md",
        },
    )
    require(
        "写作入口",
        any(path.startswith("app/writing/") for path in behavior_paths),
        {
            "app/writing/README.md",
            "docs/agent-platform/README.md",
            "docs/development/TODO.md",
        },
    )
    require(
        "审核入口",
        any(path.startswith("app/review/") for path in behavior_paths),
        {
            "app/review/README.md",
            "docs/capabilities/README.md",
            "docs/development/TODO.md",
        },
    )
    require(
        "配置",
        any(path.startswith("config/") or path == "app/config.example.env" for path in behavior_paths),
        {
            "docs/development/admin-console.md",
            "docs/development/architecture.md",
            "docs/development/TODO.md",
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
                "docs/capabilities/README.md",
                "docs/development/TODO.md",
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
        require(
            f"模块 app/{module}",
            True,
            {
                f"app/{module}/README.md",
                "docs/capabilities/README.md",
                "docs/development/architecture.md",
                "docs/development/TODO.md",
            },
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
    next_step: str,
    timestamp: datetime | None = None,
) -> None:
    """在远端推送成功后记录功能、能力变化和后续边界。"""
    moment = timestamp or datetime.now().astimezone()
    marker = f"push:{remote}/{branch}:{after_hash}"
    areas = "、".join(classify_changed_areas(changed_paths))
    completed = summary.strip()
    capability_impact = impact.strip()
    boundary = next_step.strip()
    if not completed or not capability_impact or not boundary:
        raise ValueError("开发日志必须说明完成功能、能力变化和当前边界/下一步")
    entry = (
        f"## [{moment.strftime('%Y-%m-%d %H:%M')}] 开发进展\n\n"
        f"- 完成功能：{completed}\n"
        f"- 能力变化：{capability_impact}\n"
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
    current = report_path.read_text(encoding="utf-8") if report_path.exists() else _new_report_header()
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


def push_and_record(*, remote: str, branch: str, summary: str, impact: str, next_step: str) -> None:
    if not summary.strip() or not impact.strip() or not next_step.strip():
        raise RuntimeError("开发日志必须说明完成功能、能力变化和当前边界/下一步")
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
    record_push_status(
        report_path=STATUS_REPORT_PATH,
        remote=remote,
        branch=branch,
        before_hash=before_hash,
        after_hash=after_hash,
        commit_subjects=commit_subjects,
        changed_paths=changed_paths,
        summary=summary,
        impact=impact,
        next_step=next_step,
    )
    print("推送成功，已同步写入本机 STATUS-REPORT.md。")


def check_repository(*, staged: bool = False) -> list[str]:
    errors: list[str] = []
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


def _new_report_header() -> str:
    return (
        "# M-Agent 状态报告\n\n"
        "> 本文件只保留在本机，记录开发完成的功能、能力变化和后续边界；不进入版本库。\n\n"
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
    push_parser.add_argument("--next-step", required=True, help="当前边界、遗留问题或下一步")
    record_push_parser = subparsers.add_parser("record-push", help="补记一次已完成的推送")
    record_push_parser.add_argument("--remote", default="origin")
    record_push_parser.add_argument("--branch", default="main")
    record_push_parser.add_argument("--before", required=True)
    record_push_parser.add_argument("--after", required=True)
    record_push_parser.add_argument("--summary", required=True)
    record_push_parser.add_argument("--impact", required=True)
    record_push_parser.add_argument("--next-step", required=True)
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
                next_step=args.next_step,
            )
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "record-push":
        commit_subjects, changed_paths = collect_push_details(args.before, args.after)
        record_push_status(
            report_path=STATUS_REPORT_PATH,
            remote=args.remote,
            branch=args.branch,
            before_hash=args.before,
            after_hash=args.after,
            commit_subjects=commit_subjects,
            changed_paths=changed_paths,
            summary=args.summary,
            impact=args.impact,
            next_step=args.next_step,
        )
        print("已补记推送记录。")
        return 0
    install_hooks()
    print("已启用 .githooks。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
