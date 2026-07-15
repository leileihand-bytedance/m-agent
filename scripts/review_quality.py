#!/usr/bin/env python3
"""运行通用审核真实文件质量基线。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.platform.config import DEFAULT_ENV_PATH, ROOT, parse_env_file
from app.platform.data_paths import DataPaths
from app.review import load_rules
from app.review.quality_evaluation import (
    discover_general_candidates,
    run_baseline,
    select_baseline_cases,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通用审核真实文件质量评测")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="选择真实文件并运行一轮基线")
    run_parser.add_argument(
        "--run-id",
        default=datetime.now().strftime("%Y%m%d-%H%M%S"),
        help="本次评测编号；默认使用当前时间",
    )
    run_parser.add_argument("--limit", type=int, default=5, help="样本数量，默认 5")
    run_parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    run_parser.add_argument("--data-root", type=Path)
    run_parser.add_argument("--source-root", type=Path)
    run_parser.add_argument("--output-root", type=Path)
    run_parser.add_argument(
        "--resume",
        action="store_true",
        help="恢复同一 run-id，只重跑未完成或失败样本",
    )
    return parser


def _absolute_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = ROOT / expanded
    return expanded.resolve(strict=False)


def _run(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise SystemExit("--limit 必须大于 0")

    values = parse_env_file(_absolute_path(args.env_file))
    values.update(os.environ)
    configured_paths = DataPaths.from_values(values, project_root=ROOT)
    data_root = _absolute_path(args.data_root) if args.data_root else configured_paths.root
    source_root = (
        _absolute_path(args.source_root)
        if args.source_root
        else data_root / "tasks" / "review"
    )
    output_root = (
        _absolute_path(args.output_root)
        if args.output_root
        else data_root / "evaluations" / "review"
    )

    candidates = discover_general_candidates(source_root)
    cases = select_baseline_cases(candidates, limit=args.limit)
    if len(cases) < args.limit:
        raise SystemExit(
            f"去重后仅找到 {len(cases)} 份通用审核文件，少于要求的 {args.limit} 份"
        )

    print(
        f"发现 {len(candidates)} 份去重通用文件，选取 {len(cases)} 份开始评测。",
        flush=True,
    )
    rules_text = load_rules(str(ROOT / "app" / "review" / "rules_general.md"))
    summary = asyncio.run(
        run_baseline(
            cases,
            run_id=args.run_id,
            data_root=data_root,
            output_root=output_root,
            rules_text=rules_text,
            resume=args.resume,
        )
    )
    print(
        "评测完成："
        f"成功 {summary.completed_cases}，失败 {summary.failed_cases}，"
        f"发现 {summary.total_findings} 条，模型请求 {summary.total_model_calls} 次，"
        f"请求失败 {summary.total_model_failures} 次，"
        f"耗时 {summary.elapsed_seconds:.1f} 秒。",
        flush=True,
    )
    print(f"结果目录：{summary.run_dir}", flush=True)
    return 0 if summary.failed_cases == 0 else 2


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "run":
        return _run(args)
    raise SystemExit(f"不支持的命令：{args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
