"""审核模块的大模型配置解析.

目标:
  1. 审核模块可单独切换模型供应商,不影响其他入口
  2. 继续兼容历史的 ANTHROPIC_* / MODEL_* 配置
  3. 在搜索增强误接到非 MiniMax 通道时,能明确降级
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
_DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
_DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"
_DEFAULT_MINIMAX_MODEL = "MiniMax-M2.7"


@dataclass(frozen=True)
class ReviewLLMConfig:
    api_key: str
    base_url: str
    model_name: str
    provider: str
    search_api_base_url: str | None


def _read_env_file(path: Path | None = None) -> dict[str, str]:
    env_path = path or (_ROOT / ".env")
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""


def _infer_provider(base_url: str, configured_provider: str) -> str:
    provider = configured_provider.strip().lower()
    if provider:
        return provider

    lowered = base_url.lower()
    if "deepseek.com" in lowered:
        return "deepseek-anthropic"
    if "minimaxi.com" in lowered:
        return "minimax-anthropic"
    return "anthropic-compatible"


def _derive_search_api_base_url(provider: str, base_url: str) -> str | None:
    lowered = base_url.lower()
    if provider.startswith("minimax") or "minimaxi.com" in lowered:
        if lowered.endswith("/anthropic"):
            return base_url[: -len("/anthropic")].rstrip("/")
        return base_url.rstrip("/")
    return None


def resolve_review_llm_config(
    *,
    runtime_env: Mapping[str, str] | None = None,
    env_file_values: Mapping[str, str] | None = None,
) -> ReviewLLMConfig:
    """解析审核模块的大模型配置.

    优先级:
      REVIEW_* > DEEPSEEK_* > 旧的 ANTHROPIC_* / MODEL_*
    """
    current_env = os.environ if runtime_env is None else runtime_env
    file_values = _read_env_file() if env_file_values is None else env_file_values

    configured_provider = _first_non_empty(
        current_env.get("REVIEW_MODEL_PROVIDER"),
        file_values.get("REVIEW_MODEL_PROVIDER"),
        current_env.get("MODEL_PROVIDER"),
        file_values.get("MODEL_PROVIDER"),
    )

    wants_deepseek = any(
        _first_non_empty(
            current_env.get(key),
            file_values.get(key),
        )
        for key in (
            "REVIEW_ANTHROPIC_API_KEY",
            "REVIEW_ANTHROPIC_BASE_URL",
            "REVIEW_MODEL_NAME",
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_MODEL",
        )
    ) or "deepseek" in configured_provider.lower()

    default_base_url = _DEFAULT_DEEPSEEK_BASE_URL if wants_deepseek else _DEFAULT_MINIMAX_BASE_URL
    default_model_name = _DEFAULT_DEEPSEEK_MODEL if wants_deepseek else _DEFAULT_MINIMAX_MODEL

    api_key = _first_non_empty(
        current_env.get("REVIEW_ANTHROPIC_API_KEY"),
        file_values.get("REVIEW_ANTHROPIC_API_KEY"),
        current_env.get("DEEPSEEK_API_KEY"),
        file_values.get("DEEPSEEK_API_KEY"),
        current_env.get("ANTHROPIC_API_KEY"),
        file_values.get("ANTHROPIC_API_KEY"),
        current_env.get("MODEL_API_KEY"),
        file_values.get("MODEL_API_KEY"),
    )
    base_url = _first_non_empty(
        current_env.get("REVIEW_ANTHROPIC_BASE_URL"),
        file_values.get("REVIEW_ANTHROPIC_BASE_URL"),
        current_env.get("DEEPSEEK_BASE_URL"),
        file_values.get("DEEPSEEK_BASE_URL"),
        current_env.get("ANTHROPIC_BASE_URL"),
        file_values.get("ANTHROPIC_BASE_URL"),
        current_env.get("MODEL_BASE_URL"),
        file_values.get("MODEL_BASE_URL"),
        default_base_url,
    )
    model_name = _first_non_empty(
        current_env.get("REVIEW_MODEL_NAME"),
        file_values.get("REVIEW_MODEL_NAME"),
        current_env.get("DEEPSEEK_MODEL"),
        file_values.get("DEEPSEEK_MODEL"),
        current_env.get("MODEL_NAME"),
        file_values.get("MODEL_NAME"),
        default_model_name,
    )

    if not api_key:
        raise RuntimeError(
            "审核模型 API Key 未设置(请配置 REVIEW_ANTHROPIC_API_KEY、DEEPSEEK_API_KEY 或 ANTHROPIC_API_KEY)"
        )

    provider = _infer_provider(base_url, configured_provider)
    return ReviewLLMConfig(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        provider=provider,
        search_api_base_url=_derive_search_api_base_url(provider, base_url),
    )


def build_anthropic_client():
    """返回 anthropic 兼容客户端及模型名."""
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("缺少 anthropic，无法调用审核模型。") from exc

    config = resolve_review_llm_config()
    client = anthropic.Anthropic(api_key=config.api_key, base_url=config.base_url)
    return client, config.model_name
