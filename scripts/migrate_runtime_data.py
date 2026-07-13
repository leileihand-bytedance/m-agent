from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.platform.data_paths import DataPaths


_TASK_DATE_RE = re.compile(r"^(?P<year>\d{4})(?P<month>\d{2})\d{2}-")


class MigrationConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationReport:
    source_root: str
    target_root: str
    apply: bool
    planned_files: int
    copied_files: int
    skipped_identical_files: int
    verified_files: int


def migrate_runtime_data(
    *,
    source_root: Path,
    paths: DataPaths,
    apply: bool,
) -> MigrationReport:
    source_root = source_root.resolve(strict=False)
    mappings = _collect_mappings(source_root=source_root, paths=paths)
    _validate_mappings(mappings)

    copied = 0
    skipped = 0
    verified = 0
    if apply:
        paths.prepare()
        for source, target in mappings:
            if target.exists():
                skipped += 1
                verified += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1
            if _sha256(source) != _sha256(target):
                raise RuntimeError(f"迁移后校验失败：{source} -> {target}")
            verified += 1

    report = MigrationReport(
        source_root=str(source_root),
        target_root=str(paths.root),
        apply=apply,
        planned_files=len(mappings),
        copied_files=copied,
        skipped_identical_files=skipped,
        verified_files=verified,
    )
    if apply:
        _write_manifest(paths, report, mappings)
    return report


def _collect_mappings(*, source_root: Path, paths: DataPaths) -> list[tuple[Path, Path]]:
    mappings: list[tuple[Path, Path]] = []

    review_root = source_root / "data" / "reviews"
    for source in _files(review_root):
        relative = source.relative_to(review_root)
        if not relative.parts or relative.name == ".gitkeep":
            continue
        task_id = relative.parts[0]
        partition = _task_partition(task_id)
        if partition is None:
            target = paths.legacy / "unpartitioned-reviews" / relative
        else:
            task_root = paths.review_tasks / partition[0] / partition[1] / task_id
            inner = Path(*relative.parts[1:])
            target = _review_target(task_root, inner)
        mappings.append((source, target))

    writing_root = source_root / "data" / "platform" / "jobs"
    for source in _files(writing_root):
        relative = source.relative_to(writing_root)
        if not relative.parts:
            continue
        task_id = relative.parts[0]
        partition = _task_partition(task_id)
        if partition is None:
            target = paths.legacy / "unpartitioned-writing-jobs" / relative
        else:
            target = paths.writing_jobs / partition[0] / partition[1] / relative
        mappings.append((source, target))

    directory_mappings = (
        (source_root / "data" / "policy_knowledge", paths.policy_db.parent),
        (source_root / "data" / "bank_knowledge", paths.bank_db.parent),
        (source_root / "data" / "policy_wiki_vault", paths.policy_wiki),
        (source_root / "data" / "platform" / "chat_logs", paths.chat_logs),
        (source_root / "data" / "platform" / "conversations", paths.conversations),
        (source_root / "data" / "platform" / "ops_events", paths.ops_events),
        (source_root / "data" / "platform" / "heartbeats", paths.heartbeats),
        (source_root / "data" / "logs", paths.logs),
        (
            source_root / "archive" / "inactive-2026-07-04" / "data",
            paths.legacy / "inactive-2026-07-04" / "data",
        ),
        (
            source_root / "archive" / "inactive-2026-07-04" / "app" / "data",
            paths.legacy / "inactive-2026-07-04" / "app-data",
        ),
    )
    for source_dir, target_dir in directory_mappings:
        for source in _files(source_dir):
            mappings.append((source, target_dir / source.relative_to(source_dir)))

    single_files = (
        (source_root / "data" / "platform" / "ops_state.json", paths.ops_state),
        (source_root / "data" / "review_users.yaml", paths.user_registry),
        (source_root / "app.log", paths.logs / "legacy-app.log"),
    )
    for source, target in single_files:
        if source.is_file():
            mappings.append((source, target))

    return sorted(mappings, key=lambda item: str(item[0]))


def _review_target(task_root: Path, inner: Path) -> Path:
    if not inner.parts:
        return task_root
    if inner.parts[0] == "source":
        filename = inner.name
        if filename.startswith("marked_") or filename.lower().endswith("_marked.docx"):
            return task_root / "output" / filename
        return task_root / "input" / filename
    if inner == Path("report.md"):
        return task_root / "output" / "report.md"
    return task_root / inner


def _task_partition(task_id: str) -> tuple[str, str] | None:
    match = _TASK_DATE_RE.match(task_id)
    if not match:
        return None
    return match.group("year"), match.group("month")


def _files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]


def _validate_mappings(mappings: list[tuple[Path, Path]]) -> None:
    seen_targets: dict[Path, Path] = {}
    for source, target in mappings:
        previous = seen_targets.get(target)
        if previous is not None and previous != source:
            raise MigrationConflictError(f"多个源文件映射到同一目标：{previous} / {source} -> {target}")
        seen_targets[target] = source
        if target.exists() and _sha256(source) != _sha256(target):
            raise MigrationConflictError(f"目标文件已存在且内容不同：{target}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_manifest(
    paths: DataPaths,
    report: MigrationReport,
    mappings: list[tuple[Path, Path]],
) -> None:
    migration_dir = paths.root / "runtime" / "migrations"
    migration_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {
        "report": asdict(report),
        "files": [
            {
                "source": str(source),
                "target": str(target),
                "sha256": _sha256(source),
                "size": source.stat().st_size,
            }
            for source, target in mappings
        ],
    }
    (migration_dir / f"migration-{timestamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="迁移 M-Agent 非 Git 运行数据")
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--target-root", type=Path)
    parser.add_argument("--apply", action="store_true", help="实际复制；默认仅预演")
    args = parser.parse_args(argv)

    values = {"M_AGENT_DATA_DIR": str(args.target_root)} if args.target_root else {}
    paths = DataPaths.from_values(values, project_root=args.source_root.resolve(strict=False))
    try:
        report = migrate_runtime_data(
            source_root=args.source_root,
            paths=paths,
            apply=args.apply,
        )
    except MigrationConflictError as exc:
        print(f"迁移停止：{exc}")
        return 2
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
