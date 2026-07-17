from __future__ import annotations

import argparse
import asyncio
import re

from app.platform.app import PlatformApp
from app.platform.gateway.wecom import extract_text_message, format_text_reply
from app.platform.intake import IntakeAction
from app.platform.ops.events import OpsEventLogger
from app.platform.ops.heartbeat import write_heartbeat
from app.platform.config import ROOT
from app.platform.runtime_environment import (
    RuntimeEnvironment,
    RuntimeEnvironmentError,
    validate_bot_startup,
)
from app.rewrite_bot.config import RewriteBotConfig, load_config, mask_value
from app.rewrite_bot.intake import RewriteIntakeStore


REWRITE_ACK = "收到，正在按材料润色流程处理，请稍后……"
REWRITE_ONLY_MESSAGE = "这个 Bot 只提供材料润色。请直接粘贴需要修改的原文，并说明润色要求。"
TEXT_ONLY_MESSAGE = "材料润色当前只支持直接粘贴文字，暂不读取文件或网页链接。"
URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
OTHER_SKILL_REQUEST_MARKERS = (
    "写直报",
    "写一篇直报",
    "写简报",
    "写一份简报",
    "综合调研整合",
    "帮我审核",
    "做审核",
    "检查错别字",
    "写感谢信",
    "起草感谢信",
)


async def handle_text_with_platform(
    *,
    frame,
    ws_client,
    platform_app,
    req_id_factory,
    ops_event_logger: OpsEventLogger | None = None,
    intake_store: RewriteIntakeStore | None = None,
) -> None:
    message = extract_text_message(frame)
    content = message.content.strip()
    stream_id = req_id_factory("rewrite")
    if not content:
        await ws_client.reply_stream(frame, stream_id, REWRITE_ONLY_MESSAGE, True)
        return
    if URL_PATTERN.search(content):
        await ws_client.reply_stream(frame, stream_id, TEXT_ONLY_MESSAGE, True)
        return
    if _is_explicit_other_skill_request(content):
        await ws_client.reply_stream(frame, stream_id, REWRITE_ONLY_MESSAGE, True)
        return

    try:
        route = platform_app.preview_text_route(
            channel="wecom-rewrite",
            sender_userid=message.sender_userid,
            text=content,
        )
        intake_outcome = None
        if intake_store is not None:
            intake_outcome = intake_store.handle_text(
                channel="wecom-rewrite",
                sender_userid=message.sender_userid,
                text=content,
                is_revision=bool(route.inputs.get("revision")),
            )
            if intake_outcome.action in (IntakeAction.WAIT, IntakeAction.CANCEL):
                await ws_client.reply_stream(frame, stream_id, intake_outcome.reply, True)
                return

        await ws_client.reply_stream(frame, stream_id, REWRITE_ACK, True)
        if intake_outcome is not None and intake_outcome.action is IntakeAction.SUBMIT:
            submission = intake_outcome.submission
            if submission is None:
                raise RuntimeError("润色接收状态缺少任务提交内容")
            source_text = submission.materials[0].text_value
            instruction = "\n".join(submission.instructions)
            result = await asyncio.to_thread(
                platform_app.handle_structured_request,
                channel="wecom-rewrite",
                sender_userid=message.sender_userid,
                skill_id="rewrite",
                text=instruction,
                material_text=source_text,
            )
            if not result.needs_clarification:
                intake_store.clear(
                    channel="wecom-rewrite",
                    sender_userid=message.sender_userid,
                )
        elif route.skill_id == "rewrite":
            result = await asyncio.to_thread(
                platform_app.handle_text_message,
                channel="wecom-rewrite",
                sender_userid=message.sender_userid,
                text=content,
                ack_message=REWRITE_ACK,
            )
        else:
            result = await asyncio.to_thread(
                platform_app.handle_structured_request,
                channel="wecom-rewrite",
                sender_userid=message.sender_userid,
                skill_id="rewrite",
                text=content,
            )
        if result.skill_id is None and result.needs_clarification:
            reply = REWRITE_ONLY_MESSAGE
        else:
            reply = format_text_reply(result)
    except Exception as exc:
        if ops_event_logger:
            ops_event_logger.record(
                source="rewrite_bot",
                severity="error",
                subject="材料润色处理失败",
                detail=f"{type(exc).__name__}: {exc}",
                sender_userid=message.sender_userid,
                sender_name=platform_app.resolve_sender_name(message.sender_userid),
                skill_id="rewrite",
            )
        reply = "材料润色暂时处理失败，已经提醒管理员排查，请稍后重试。"
    await ws_client.reply_stream(
        frame,
        req_id_factory("rewrite-result"),
        reply,
        True,
    )


async def handle_file_with_platform(*, frame, ws_client, req_id_factory) -> None:
    await ws_client.reply_stream(
        frame,
        req_id_factory("rewrite-file"),
        TEXT_ONLY_MESSAGE,
        True,
    )


def _is_explicit_other_skill_request(content: str) -> bool:
    normalized = content.strip()
    if len(normalized) > 120:
        return False
    return any(marker in normalized for marker in OTHER_SKILL_REQUEST_MARKERS)


async def run_bot(config: RewriteBotConfig) -> None:
    try:
        from wecom_aibot_sdk import WSClient, generate_req_id
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖 wecom-aibot-sdk。请在项目根目录运行：uv sync --locked"
        ) from exc

    platform_app = PlatformApp.from_config(config.platform_config)
    intake_store = RewriteIntakeStore(storage_dir=config.intake_dir)
    ops_event_logger = OpsEventLogger(config.ops_events_dir)
    ws_client = WSClient(bot_id=config.bot_id, secret=config.bot_secret)

    ws_client.on("connected", lambda: print("材料润色 Bot 已连接企业微信。", flush=True))
    ws_client.on("authenticated", lambda: print("材料润色 Bot 认证成功，等待消息。", flush=True))
    ws_client.on(
        "disconnected",
        lambda reason: _record_connection_event(
            ops_event_logger,
            subject="材料润色 Bot 连接断开",
            detail=str(reason),
        ),
    )
    ws_client.on(
        "error",
        lambda error: _record_connection_event(
            ops_event_logger,
            subject="材料润色 Bot 连接错误",
            detail=str(error),
        ),
    )

    async def on_text(frame):
        await handle_text_with_platform(
            frame=frame,
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=generate_req_id,
            ops_event_logger=ops_event_logger,
            intake_store=intake_store,
        )

    async def on_file(frame):
        await handle_file_with_platform(
            frame=frame,
            ws_client=ws_client,
            req_id_factory=generate_req_id,
        )

    async def on_enter(frame):
        await ws_client.reply_welcome(
            frame,
            {"msgtype": "text", "text": {"content": REWRITE_ONLY_MESSAGE}},
        )

    ws_client.on("message.text", on_text)
    ws_client.on("message.file", on_file)
    ws_client.on("event.enter_chat", on_enter)

    await ws_client.connect()
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(config.heartbeat_dir),
        name="rewrite-bot-heartbeat",
    )
    try:
        await asyncio.Event().wait()
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        await ws_client.disconnect()


async def _heartbeat_loop(heartbeat_dir) -> None:
    while True:
        write_heartbeat(heartbeat_dir, "rewrite_bot")
        await asyncio.sleep(30)


def _record_connection_event(
    logger: OpsEventLogger,
    *,
    subject: str,
    detail: str,
) -> None:
    print(f"{subject}：{detail}", flush=True)
    logger.record(
        source="rewrite_bot",
        severity="error",
        subject=subject,
        detail=detail,
        skill_id="rewrite",
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="材料润色 Bot")
    parser.add_argument("--check-config", action="store_true", help="检查配置")
    args = parser.parse_args(argv)
    try:
        config = load_config()
        platform = config.platform_config
        validate_bot_startup(
            RuntimeEnvironment(
                mode=config.runtime_mode,
                data_root=config.data_root or config.intake_dir.parent,
                values={},
            ),
            data_paths=(
                platform.jobs_dir,
                platform.policy_db_path,
                platform.bank_db_path,
                platform.conversation_dir,
                platform.chat_log_dir,
                platform.user_registry_path,
                platform.task_queue_db_path,
                config.intake_dir,
                config.ops_events_dir,
                config.heartbeat_dir,
            ),
            project_root=ROOT,
        )
    except RuntimeEnvironmentError as exc:
        print(f"错误：{exc}")
        return

    if not config.bot_id or not config.bot_secret:
        print("错误：缺少 M_AGENT_REWRITE_BOT_ID 或 M_AGENT_REWRITE_BOT_SECRET 配置")
        return

    if args.check_config:
        platform_app = PlatformApp.from_config(config.platform_config)
        route = platform_app.preview_text_route(
            channel="config-check",
            sender_userid="config-check",
            text="帮我润色这段：配置检查示例原文。",
        )
        if route.skill_id != "rewrite":
            print("错误：rewrite Skill 未启用或入口隔离配置无效")
            return
        print("配置检查通过。")
        print(f"运行环境: {config.runtime_mode}")
        print(f"数据根目录: {config.data_root}")
        print(f"Bot ID: {mask_value(config.bot_id)}")
        print("允许的 Skill: rewrite")
        print(f"任务目录: {config.platform_config.jobs_dir}")
        print(f"会话目录: {config.platform_config.conversation_dir}")
        print(f"待处理原文目录: {config.intake_dir}")
        return

    print("正在连接企业微信材料润色 Bot...", flush=True)
    asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
