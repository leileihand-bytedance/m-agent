from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_uv_project_pins_python_and_declares_dependencies():
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert python_version == "3.13.14"
    assert project["project"]["requires-python"] == ">=3.13,<3.14"
    assert project["tool"]["uv"]["package"] is False

    dependencies = project["project"]["dependencies"]
    dependency_groups = project["dependency-groups"]
    assert any(item.startswith("pydantic-ai-slim") for item in dependencies)
    assert any(item.startswith("wecom-aibot-sdk") for item in dependencies)
    assert any(item.startswith("pytest") for item in dependency_groups["dev"])


def test_project_environment_files_are_present_and_venv_is_ignored():
    assert (ROOT / "uv.lock").is_file()
    assert ".venv/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert not (ROOT / "app/requirements.txt").exists()
    assert not (ROOT / "app/requirements-dev.txt").exists()


def test_pydantic_ai_model_provider_dependencies_are_available():
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models.openai import OpenAIChatModel

    assert AnthropicModel is not None
    assert OpenAIChatModel is not None
