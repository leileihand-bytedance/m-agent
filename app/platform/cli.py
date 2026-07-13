from __future__ import annotations

import argparse
import json

from app.platform.app import PlatformApp
from app.platform.config import PlatformConfig, load_config
from app.platform.gateway.wecom import format_text_reply


def check_config(config: PlatformConfig) -> dict[str, object]:
    return {
        "model_name": config.model_name,
        "has_api_key": bool(config.anthropic_api_key),
        "base_url": config.anthropic_base_url,
        "model_max_tokens": config.model_max_tokens,
        "direct_report_critic_mode": config.direct_report_critic_mode,
        "chat_log_enabled": config.chat_log_enabled,
        "chat_log_dir": str(config.chat_log_dir or ""),
        "user_registry_path": str(config.user_registry_path) if config.user_registry_path else "",
        "user_registry_exists": bool(config.user_registry_path and config.user_registry_path.exists()),
        "skills_dir": str(config.skills_dir),
        "skills_dir_exists": config.skills_dir.exists(),
        "jobs_dir": str(config.jobs_dir),
        "policy_db_path": str(config.policy_db_path),
        "policy_db_exists": config.policy_db_path.exists(),
        "bank_db_path": str(config.bank_db_path),
        "bank_db_exists": config.bank_db_path.exists(),
        "access_policy_path": str(config.access_policy_path) if config.access_policy_path else "",
        "access_policy_exists": bool(config.access_policy_path and config.access_policy_path.exists()),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M-Agent 新底座入口")
    parser.add_argument("message", nargs="?", help="本地测试消息，例如：帮我根据这个链接写直报：https://...")
    parser.add_argument("--check-config", action="store_true", help="检查底座配置")
    parser.add_argument("--sender", default="local-user", help="本地测试用户 ID")
    args = parser.parse_args(argv)

    config = load_config()
    if args.check_config:
        print(json.dumps(check_config(config), ensure_ascii=False, indent=2))
        return

    if not args.message:
        parser.error("请提供 message，或使用 --check-config")

    app = PlatformApp.from_config(config)
    result = app.handle_text_message(
        channel="local-cli",
        sender_userid=args.sender,
        text=args.message,
    )
    print(format_text_reply(result))


if __name__ == "__main__":
    main()
