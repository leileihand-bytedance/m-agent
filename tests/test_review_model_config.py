from __future__ import annotations

from app.review.search_tools import search_web


def test_resolve_review_llm_config_prefers_review_specific_values():
    from app.review.model_config import resolve_review_llm_config

    config = resolve_review_llm_config(
        runtime_env={
            "REVIEW_ANTHROPIC_API_KEY": "review-key",
            "REVIEW_ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
            "REVIEW_MODEL_NAME": "deepseek-v4-flash",
            "ANTHROPIC_API_KEY": "legacy-key",
            "ANTHROPIC_BASE_URL": "https://api.minimaxi.com/anthropic",
            "MODEL_NAME": "MiniMax-M2.7",
        },
        env_file_values={},
    )

    assert config.api_key == "review-key"
    assert config.base_url == "https://api.deepseek.com/anthropic"
    assert config.model_name == "deepseek-v4-flash"
    assert config.provider == "deepseek-anthropic"
    assert config.search_api_base_url is None


def test_resolve_review_llm_config_falls_back_to_legacy_values(monkeypatch):
    from app.review.model_config import resolve_review_llm_config

    # 避免真实环境变量干扰测试
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "MODEL_NAME",
        "REVIEW_ANTHROPIC_API_KEY",
        "REVIEW_ANTHROPIC_BASE_URL",
        "REVIEW_MODEL_NAME",
    ):
        monkeypatch.delenv(key, raising=False)

    config = resolve_review_llm_config(
        runtime_env={},
        env_file_values={
            "ANTHROPIC_API_KEY": "legacy-key",
            "ANTHROPIC_BASE_URL": "https://api.minimaxi.com/anthropic",
            "MODEL_NAME": "MiniMax-M2.7",
        },
    )

    assert config.api_key == "legacy-key"
    assert config.base_url == "https://api.minimaxi.com/anthropic"
    assert config.model_name == "MiniMax-M2.7"
    assert config.provider == "minimax-anthropic"
    assert config.search_api_base_url == "https://api.minimaxi.com"


def test_search_web_skips_minimax_only_endpoint_when_review_model_is_deepseek(monkeypatch):
    from app.review.model_config import ReviewLLMConfig

    monkeypatch.setattr(
        "app.review.search_tools.resolve_review_llm_config",
        lambda: ReviewLLMConfig(
            api_key="review-key",
            base_url="https://api.deepseek.com/anthropic",
            model_name="deepseek-v4-flash",
            provider="deepseek-anthropic",
            search_api_base_url=None,
        ),
    )

    assert search_web("金融监管总局", max_results=3) == []
