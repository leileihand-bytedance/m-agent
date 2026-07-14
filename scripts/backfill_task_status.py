from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.platform.data_paths import DataPaths
from app.platform.task_status import classify_writing_result, write_task_status


@dataclass(frozen=True)
class BackfillReport:
    writing_tasks: int
    review_tasks: int
    planned: int
    written: int
    skipped: int
    apply: bool


def backfill_task_statuses(
    *,
    writing_root: Path,
    review_root: Path,
    apply: bool,
) -> BackfillReport:
    writing_dirs = sorted({path.parent for path in writing_root.glob("**/meta.json")})
    review_dirs = {path.parent for path in review_root.glob("**/meta.json")}
    review_dirs.update(path.parent for path in review_root.glob("**/meta.md"))
    review_dirs.update(path.parent.parent for path in review_root.glob("**/output/report.md"))
    ordered_review_dirs = sorted(review_dirs)

    candidates: list[tuple[Path, str]] = []
    skipped = 0
    for task_dir in writing_dirs:
        if (task_dir / "status.json").is_file():
            skipped += 1
            continue
        result = _read_json(task_dir / "output" / "result.json")
        processing_status = classify_writing_result(result) if result else "incomplete"
        candidates.append((task_dir, processing_status))

    for task_dir in ordered_review_dirs:
        if (task_dir / "status.json").is_file():
            skipped += 1
            continue
        processing_status = (
            "completed" if (task_dir / "output" / "report.md").is_file() else "incomplete"
        )
        candidates.append((task_dir, processing_status))

    written = 0
    if apply:
        for task_dir, processing_status in candidates:
            write_task_status(
                task_dir,
                processing_status=processing_status,
                source="backfill",
            )
            written += 1

    return BackfillReport(
        writing_tasks=len(writing_dirs),
        review_tasks=len(ordered_review_dirs),
        planned=len(candidates),
        written=written,
        skipped=skipped,
        apply=apply,
    )


def _read_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="补齐 M-Agent 历史任务状态索引")
    parser.add_argument("--data-root", type=Path, help="运行数据根目录；默认使用 M-Agent-Files")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认仅预演")
    args = parser.parse_args(argv)

    values = {"M_AGENT_DATA_DIR": str(args.data_root)} if args.data_root else {}
    paths = DataPaths.from_values(values, project_root=PROJECT_ROOT)
    report = backfill_task_statuses(
        writing_root=paths.writing_jobs,
        review_root=paths.review_tasks,
        apply=args.apply,
    )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
