import argparse
from collections.abc import Callable
from pathlib import Path

from app.platform.builtin_tools import (
    bank_materials,
    bank_search,
    policy_materials,
    policy_research,
    policy_search,
    read_web_page,
)
from app.platform.config import PlatformConfig, load_config
from app.platform.models import PlatformResult
from app.platform.pydantic_runtime import PydanticAIWriter
from app.platform.registry import SkillRegistry
from app.platform.router import route_message
from app.platform.runtime import PlatformRuntime


def run_message(
    message: str,
    tools: dict[str, Callable[..., object]],
    skills_dir: Path,
) -> PlatformResult:
    registry = SkillRegistry.from_directory(skills_dir)
    route = route_message(message, registry)
    runtime = PlatformRuntime(registry=registry, tools=tools)
    return runtime.run(route)


def build_builtin_tools(config: PlatformConfig) -> dict[str, Callable[..., object]]:
    writer = PydanticAIWriter(
        api_key=config.anthropic_api_key,
        base_url=config.anthropic_base_url,
        model_name=config.model_name,
        skill_dir=config.skills_dir,
        model_max_tokens=config.model_max_tokens,
    )
    return {
        "web_reader": read_web_page,
        "policy_search": lambda query, limit=5, category=None: policy_search(
            query,
            db_path=config.policy_db_path,
            limit=limit,
            category=category,
        ),
        "policy_materials": lambda user_instruction, materials, limit=3: policy_materials(
            user_instruction=user_instruction,
            materials=materials,
            db_path=config.policy_db_path,
            limit=limit,
        ),
        "policy_research": lambda user_instruction, materials, usage_profile, limit=3: policy_research(
            user_instruction=user_instruction,
            materials=materials,
            db_path=config.policy_db_path,
            usage_profile=usage_profile,
            limit=limit,
        ),
        "bank_search": lambda query, limit=5, themes=None: bank_search(
            query,
            db_path=config.bank_db_path,
            limit=limit,
            themes=themes,
        ),
        "bank_materials": lambda user_instruction, materials, limit=3: bank_materials(
            user_instruction=user_instruction,
            materials=materials,
            db_path=config.bank_db_path,
            limit=limit,
        ),
        "llm_writer": writer.write,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M-Agent 平台本地 demo")
    parser.add_argument("message", help="用户自然语言请求，例如：帮我根据这个链接写直报：https://...")
    args = parser.parse_args(argv)

    config = load_config()
    result = run_message(args.message, build_builtin_tools(config), config.skills_dir)
    if result.needs_clarification:
        print(result.message)
        return

    title = result.output.get("title", "")
    body = result.output.get("body", "")
    sources = result.output.get("sources", [])
    print(f"{title}\n")
    print(body)
    if sources:
        print("\n来源：")
        for source in sources:
            print(f"- {source}")


if __name__ == "__main__":
    main()
