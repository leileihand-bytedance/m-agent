from pathlib import Path
import plistlib
import subprocess
import sys

import pytest

from scripts.bot_services import (
    BOT_SERVICES,
    BotServiceManager,
    ServiceManagerError,
    build_launch_agent,
    parse_launchctl_status,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self, *, loaded_labels: set[str] | None = None):
        self.loaded_labels = loaded_labels or set()
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, args, **_kwargs):
        command = tuple(str(item) for item in args)
        self.calls.append(command)
        if command[:2] == ("launchctl", "print"):
            label = command[-1].rsplit("/", 1)[-1]
            if label in self.loaded_labels:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="state = running\n\tpid = 321\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(command, 113, stdout="", stderr="not found")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_launch_agent_runs_locked_project_command_without_secrets(tmp_path: Path):
    project_root = tmp_path / "M-Agent"
    logs_dir = tmp_path / "M-Agent-Files" / "runtime" / "logs"
    payload = build_launch_agent(
        BOT_SERVICES["writing"],
        project_root=project_root,
        uv_path=Path("/opt/local/bin/uv"),
        logs_dir=logs_dir,
    )

    assert payload["Label"] == "com.magent.writing-bot"
    assert payload["ProgramArguments"] == [
        "/opt/local/bin/uv",
        "run",
        "--locked",
        "python",
        "-m",
        "app.writing.bot",
    ]
    assert payload["WorkingDirectory"] == str(project_root)
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert payload["ThrottleInterval"] == 10
    assert payload["Umask"] == 0o077
    assert payload["StandardOutPath"].endswith("writing-bot-service.out.log")
    assert payload["StandardErrorPath"].endswith("writing-bot-service.err.log")
    assert "SECRET" not in plistlib.dumps(payload).decode("utf-8")


def test_install_validates_config_and_bootstraps_only_selected_service(tmp_path: Path):
    runner = FakeRunner()
    manager = BotServiceManager(
        project_root=tmp_path / "M-Agent",
        data_root=tmp_path / "M-Agent-Files",
        launch_agents_dir=tmp_path / "LaunchAgents",
        uv_path=Path("/opt/local/bin/uv"),
        uid=502,
        branch_name="main",
        platform_name="Darwin",
        runner=runner,
    )

    manager.install((BOT_SERVICES["review"],))

    plist_path = tmp_path / "LaunchAgents" / "com.magent.review-bot.plist"
    assert plist_path.is_file()
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["ProgramArguments"][-1] == "app.review.main"
    assert (
        "/opt/local/bin/uv",
        "run",
        "--locked",
        "python",
        "-m",
        "app.review.main",
        "--check-config",
    ) in runner.calls
    assert ("launchctl", "enable", "gui/502/com.magent.review-bot") in runner.calls
    assert (
        "launchctl",
        "bootstrap",
        "gui/502",
        str(plist_path),
    ) in runner.calls
    assert not any("writing-bot" in " ".join(call) for call in runner.calls)


def test_install_refuses_non_main_or_non_macos(tmp_path: Path):
    common = {
        "project_root": tmp_path / "M-Agent",
        "data_root": tmp_path / "M-Agent-Files",
        "launch_agents_dir": tmp_path / "LaunchAgents",
        "uv_path": Path("/opt/local/bin/uv"),
        "uid": 502,
        "runner": FakeRunner(),
    }
    with pytest.raises(ServiceManagerError, match="main"):
        BotServiceManager(
            **common,
            branch_name="codex/change-bot",
            platform_name="Darwin",
        ).install((BOT_SERVICES["writing"],))

    with pytest.raises(ServiceManagerError, match="macOS"):
        BotServiceManager(
            **common,
            branch_name="main",
            platform_name="Linux",
        ).install((BOT_SERVICES["writing"],))


def test_restart_and_stop_target_only_requested_service(tmp_path: Path):
    runner = FakeRunner(loaded_labels={"com.magent.writing-bot"})
    manager = BotServiceManager(
        project_root=tmp_path / "M-Agent",
        data_root=tmp_path / "M-Agent-Files",
        launch_agents_dir=tmp_path / "LaunchAgents",
        uv_path=Path("/opt/local/bin/uv"),
        uid=502,
        branch_name="main",
        platform_name="Darwin",
        runner=runner,
    )
    manager.launch_agents_dir.mkdir(parents=True)
    manager.plist_path(BOT_SERVICES["writing"]).write_bytes(
        plistlib.dumps(
            build_launch_agent(
                BOT_SERVICES["writing"],
                project_root=manager.project_root,
                uv_path=manager.uv_path,
                logs_dir=manager.logs_dir,
            )
        )
    )

    manager.restart((BOT_SERVICES["writing"],))
    manager.stop((BOT_SERVICES["writing"],))

    target = "gui/502/com.magent.writing-bot"
    assert ("launchctl", "kickstart", "-k", target) in runner.calls
    assert ("launchctl", "disable", target) in runner.calls
    assert ("launchctl", "bootout", target) in runner.calls
    assert not any("review-bot" in " ".join(call) for call in runner.calls)


def test_parse_launchctl_status_is_concise():
    assert parse_launchctl_status("state = running\n\tpid = 987\n") == ("running", 987)
    assert parse_launchctl_status("state = waiting\n") == ("waiting", None)


def test_script_can_run_directly_without_pythonpath():
    result = subprocess.run(
        (sys.executable, str(ROOT / "scripts" / "bot_services.py"), "--help"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={},
    )

    assert result.returncode == 0, result.stderr
    assert "管理写作和审核 Bot" in result.stdout
