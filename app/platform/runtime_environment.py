from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable, Mapping


RUNTIME_ENV_KEY = "M_AGENT_RUNTIME_ENV"
PRODUCTION_DATA_ROOT_KEY = "M_AGENT_DATA_DIR"
TEST_DATA_ROOT_KEY = "M_AGENT_TEST_DATA_DIR"
VALID_RUNTIME_MODES = {"production", "test"}


class RuntimeEnvironmentError(RuntimeError):
    """运行环境不满足生产/测试隔离要求。"""


@dataclass(frozen=True)
class RuntimeEnvironment:
    mode: str
    data_root: Path
    values: Mapping[str, str]


def prepare_runtime_environment(
    values: Mapping[str, str],
    *,
    project_root: Path,
) -> RuntimeEnvironment:
    """解析运行模式，并把测试模式的数据根目录强制切到独立目录。"""
    effective_values = dict(values)
    mode = str(effective_values.get(RUNTIME_ENV_KEY, "production") or "production").strip().lower()
    if mode not in VALID_RUNTIME_MODES:
        raise RuntimeEnvironmentError(
            f"{RUNTIME_ENV_KEY} 只能是 production 或 test，当前值为 {mode or '空'}"
        )

    production_root = _resolve_path(
        effective_values.get(PRODUCTION_DATA_ROOT_KEY),
        project_root=project_root,
        default=project_root.parent / "M-Agent-Files",
    )
    if mode == "production":
        data_root = production_root
    else:
        test_root_raw = str(effective_values.get(TEST_DATA_ROOT_KEY, "") or "").strip()
        if not test_root_raw:
            raise RuntimeEnvironmentError(
                f"测试模式必须显式配置 {TEST_DATA_ROOT_KEY}，且不得使用生产数据目录"
            )
        data_root = _resolve_path(test_root_raw, project_root=project_root)
        if data_root == production_root:
            raise RuntimeEnvironmentError("测试数据目录不能与生产数据目录相同")

    effective_values[RUNTIME_ENV_KEY] = mode
    effective_values[PRODUCTION_DATA_ROOT_KEY] = str(data_root)
    return RuntimeEnvironment(mode=mode, data_root=data_root, values=effective_values)


def bot_credentials(
    runtime: RuntimeEnvironment,
    *,
    production_keys: tuple[str, str],
    test_keys: tuple[str, str],
) -> tuple[str, str]:
    """按运行模式选凭据；测试模式绝不回退到生产凭据。"""
    selected_keys = production_keys if runtime.mode == "production" else test_keys
    bot_id = str(runtime.values.get(selected_keys[0], "") or "").strip()
    bot_secret = str(runtime.values.get(selected_keys[1], "") or "").strip()
    if bot_id and bot_secret:
        return bot_id, bot_secret
    if runtime.mode == "test":
        raise RuntimeEnvironmentError(
            "测试模式必须配置专用测试 Bot 凭据："
            f"{test_keys[0]} 和 {test_keys[1]}；不会回退使用生产 Bot"
        )
    return bot_id, bot_secret


def validate_bot_startup(
    runtime: RuntimeEnvironment,
    *,
    data_paths: Iterable[Path | None],
    project_root: Path,
    current_branch: str | None = None,
) -> None:
    """在连接企业微信前校验 Git 分支和运行数据边界。"""
    branch = current_branch if current_branch is not None else git_branch(project_root)
    if runtime.mode == "production" and branch != "main":
        raise RuntimeEnvironmentError(
            f"生产 Bot 只能从 main 分支启动；当前为 {branch or 'detached HEAD'}。"
            "请先合并回 main，或改用独立测试 Bot 和测试数据目录"
        )
    if runtime.mode != "test":
        return

    outside_paths = []
    for raw_path in data_paths:
        if raw_path is None:
            continue
        path = Path(raw_path).expanduser().resolve(strict=False)
        if not path.is_relative_to(runtime.data_root):
            outside_paths.append(path)
    if outside_paths:
        joined = "、".join(str(path) for path in outside_paths)
        raise RuntimeEnvironmentError(
            f"测试运行路径越过测试数据目录 {runtime.data_root}：{joined}"
        )


def git_branch(project_root: Path) -> str:
    result = subprocess.run(
        ("git", "branch", "--show-current"),
        cwd=project_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _resolve_path(
    raw_path: str | None,
    *,
    project_root: Path,
    default: Path | None = None,
) -> Path:
    value = str(raw_path or "").strip()
    path = Path(value).expanduser() if value else default
    if path is None:
        raise RuntimeEnvironmentError("运行数据目录不能为空")
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)
