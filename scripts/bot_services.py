from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from typing import Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.platform.config import DEFAULT_ENV_PATH, parse_env_file
from app.platform.data_paths import DataPaths


@dataclass(frozen=True)
class BotService:
    key: str
    label: str
    module: str
    log_prefix: str


@dataclass(frozen=True)
class BotServiceStatus:
    key: str
    label: str
    loaded: bool
    state: str
    pid: int | None


BOT_SERVICES = {
    "writing": BotService(
        key="writing",
        label="com.magent.writing-bot",
        module="app.writing.bot",
        log_prefix="writing-bot-service",
    ),
    "review": BotService(
        key="review",
        label="com.magent.review-bot",
        module="app.review.main",
        log_prefix="review-bot-service",
    ),
}


class ServiceManagerError(RuntimeError):
    pass


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def build_launch_agent(
    service: BotService,
    *,
    project_root: Path,
    uv_path: Path,
    logs_dir: Path,
) -> dict[str, object]:
    """Build a secret-free LaunchAgent definition for one production Bot."""
    return {
        "Label": service.label,
        "ProgramArguments": [
            str(uv_path),
            "run",
            "--locked",
            "python",
            "-m",
            service.module,
        ],
        "WorkingDirectory": str(project_root),
        "RunAtLoad": True,
        # Restart crashes, but do not loop forever when startup validation exits cleanly.
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "Umask": 0o077,
        "StandardOutPath": str(logs_dir / f"{service.log_prefix}.out.log"),
        "StandardErrorPath": str(logs_dir / f"{service.log_prefix}.err.log"),
    }


def parse_launchctl_status(output: str) -> tuple[str, int | None]:
    state_match = re.search(r"^\s*state\s*=\s*([^\s]+)", output, re.MULTILINE)
    pid_match = re.search(r"^\s*pid\s*=\s*(\d+)", output, re.MULTILINE)
    state = state_match.group(1) if state_match else "unknown"
    pid = int(pid_match.group(1)) if pid_match else None
    return state, pid


class BotServiceManager:
    def __init__(
        self,
        *,
        project_root: Path,
        data_root: Path,
        launch_agents_dir: Path,
        uv_path: Path,
        uid: int,
        branch_name: str,
        platform_name: str,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        self.project_root = project_root.resolve(strict=False)
        self.data_root = data_root.resolve(strict=False)
        self.launch_agents_dir = launch_agents_dir.resolve(strict=False)
        self.uv_path = uv_path.resolve(strict=False)
        self.uid = uid
        self.branch_name = branch_name
        self.platform_name = platform_name
        self.runner = runner

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "runtime" / "logs"

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    def target(self, service: BotService) -> str:
        return f"{self.domain}/{service.label}"

    def plist_path(self, service: BotService) -> Path:
        return self.launch_agents_dir / f"{service.label}.plist"

    def install(self, services: Sequence[BotService]) -> None:
        self._require_production_control()
        self.launch_agents_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        for service in services:
            self._check_config(service)
            if self._is_loaded(service):
                self._run(("launchctl", "bootout", self.target(service)), check=True)
            self._write_plist(service)
            self._run(("launchctl", "enable", self.target(service)), check=True)
            self._run(
                (
                    "launchctl",
                    "bootstrap",
                    self.domain,
                    str(self.plist_path(service)),
                ),
                check=True,
            )

    def start(self, services: Sequence[BotService]) -> None:
        self._require_production_control()
        for service in services:
            self._require_plist(service)
            self._check_config(service)
            self._run(("launchctl", "enable", self.target(service)), check=True)
            if self._is_loaded(service):
                self._run(("launchctl", "kickstart", self.target(service)), check=True)
            else:
                self._run(
                    (
                        "launchctl",
                        "bootstrap",
                        self.domain,
                        str(self.plist_path(service)),
                    ),
                    check=True,
                )

    def restart(self, services: Sequence[BotService]) -> None:
        self._require_production_control()
        for service in services:
            self._require_plist(service)
            self._check_config(service)
            self._run(("launchctl", "enable", self.target(service)), check=True)
            if self._is_loaded(service):
                self._run(
                    ("launchctl", "kickstart", "-k", self.target(service)),
                    check=True,
                )
            else:
                self._run(
                    (
                        "launchctl",
                        "bootstrap",
                        self.domain,
                        str(self.plist_path(service)),
                    ),
                    check=True,
                )

    def stop(self, services: Sequence[BotService]) -> None:
        self._require_macos()
        for service in services:
            self._run(("launchctl", "disable", self.target(service)), check=True)
            if self._is_loaded(service):
                self._run(("launchctl", "bootout", self.target(service)), check=True)

    def uninstall(self, services: Sequence[BotService]) -> None:
        self.stop(services)
        for service in services:
            self.plist_path(service).unlink(missing_ok=True)

    def status(self, services: Sequence[BotService]) -> tuple[BotServiceStatus, ...]:
        self._require_macos()
        statuses: list[BotServiceStatus] = []
        for service in services:
            result = self._run(
                ("launchctl", "print", self.target(service)),
                check=False,
            )
            if result.returncode != 0:
                statuses.append(
                    BotServiceStatus(
                        key=service.key,
                        label=service.label,
                        loaded=False,
                        state="not-loaded",
                        pid=None,
                    )
                )
                continue
            state, pid = parse_launchctl_status(result.stdout or "")
            statuses.append(
                BotServiceStatus(
                    key=service.key,
                    label=service.label,
                    loaded=True,
                    state=state,
                    pid=pid,
                )
            )
        return tuple(statuses)

    def _write_plist(self, service: BotService) -> None:
        path = self.plist_path(service)
        temporary = path.with_suffix(".plist.tmp")
        payload = build_launch_agent(
            service,
            project_root=self.project_root,
            uv_path=self.uv_path,
            logs_dir=self.logs_dir,
        )
        temporary.write_bytes(plistlib.dumps(payload, sort_keys=False))
        temporary.chmod(0o600)
        temporary.replace(path)

    def _require_plist(self, service: BotService) -> None:
        if not self.plist_path(service).is_file():
            raise ServiceManagerError(
                f"{service.key} 常驻服务尚未安装，请先执行 install。"
            )

    def _check_config(self, service: BotService) -> None:
        self._run(
            (
                str(self.uv_path),
                "run",
                "--locked",
                "python",
                "-m",
                service.module,
                "--check-config",
            ),
            check=True,
        )

    def _is_loaded(self, service: BotService) -> bool:
        result = self._run(
            ("launchctl", "print", self.target(service)),
            check=False,
        )
        return result.returncode == 0

    def _require_production_control(self) -> None:
        self._require_macos()
        if self.branch_name != "main":
            raise ServiceManagerError(
                f"生产 Bot 常驻服务只能从 main 安装、启动或重启；当前分支为 {self.branch_name}。"
            )

    def _require_macos(self) -> None:
        if self.platform_name != "Darwin":
            raise ServiceManagerError("Bot 常驻服务当前只支持 macOS LaunchAgent。")

    def _run(
        self,
        command: Sequence[str],
        *,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        result = self.runner(
            tuple(command),
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "命令执行失败").strip()
            raise ServiceManagerError(f"{' '.join(command)}：{detail}")
        return result


def _current_branch(project_root: Path) -> str:
    result = subprocess.run(
        ("git", "branch", "--show-current"),
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ServiceManagerError("无法读取当前 Git 分支。")
    return result.stdout.strip()


def default_manager() -> BotServiceManager:
    uv_executable = shutil.which("uv")
    if not uv_executable:
        raise ServiceManagerError("找不到 uv，请先安装并确保当前终端可以执行 uv。")
    values = parse_env_file(DEFAULT_ENV_PATH)
    data_root = DataPaths.from_values(values, project_root=ROOT).root
    return BotServiceManager(
        project_root=ROOT,
        data_root=data_root,
        launch_agents_dir=Path.home() / "Library" / "LaunchAgents",
        uv_path=Path(uv_executable),
        uid=os.getuid(),
        branch_name=_current_branch(ROOT),
        platform_name=platform.system(),
    )


def selected_services(value: str) -> tuple[BotService, ...]:
    if value == "all":
        return tuple(BOT_SERVICES.values())
    return (BOT_SERVICES[value],)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="管理写作和审核 Bot 的 macOS 常驻服务"
    )
    parser.add_argument(
        "command",
        choices=("install", "start", "stop", "restart", "status", "uninstall"),
    )
    parser.add_argument(
        "service",
        nargs="?",
        choices=("writing", "review", "all"),
        default="all",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    services = selected_services(args.service)
    try:
        manager = default_manager()
        if args.command == "status":
            for status in manager.status(services):
                if not status.loaded:
                    print(f"{status.key}: 未加载")
                elif status.pid is None:
                    print(f"{status.key}: {status.state}")
                else:
                    print(f"{status.key}: {status.state} (pid {status.pid})")
            return 0
        getattr(manager, args.command)(services)
    except ServiceManagerError as exc:
        print(f"错误：{exc}")
        return 1
    print(f"{args.command} 完成：{', '.join(item.key for item in services)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
