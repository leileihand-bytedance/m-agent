from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
from pathlib import Path

from app.platform.ops.config import OpsBotConfig, load_config, mask_value
from app.platform.ops.events import OpsEvent, read_ops_events
from app.platform.ops.heartbeat import find_stale_heartbeats, write_heartbeat
from app.platform.ops.notifier import OpsNotifier
from app.platform.ops.report import build_daily_report, previous_workday


@dataclass
class OpsBotState:
    notified_event_ids: set[str]
    last_daily_report_for: str = ""


def collect_pending_events(*, events_dir: Path, today: date, state: OpsBotState) -> list[OpsEvent]:
    days = {
        today,
        today - timedelta(days=1),
        previous_workday(today),
    }
    events: list[OpsEvent] = []
    for day in sorted(days):
        events.extend(read_ops_events(events_dir, day))
    return [
        event
        for event in events
        if event.event_id and event.event_id not in state.notified_event_ids
    ]


def should_send_daily_report(
    *,
    now: datetime,
    hour: int,
    minute: int,
    state: OpsBotState,
) -> bool:
    if now.weekday() >= 5:
        return False
    if state.last_daily_report_for == now.date().isoformat():
        return False
    return (now.hour, now.minute) >= (hour, minute)


async def run_bot(config: OpsBotConfig) -> None:
    try:
        from wecom_aibot_sdk import WSClient
    except ImportError as exc:
        raise RuntimeError("缺少依赖 wecom-aibot-sdk。请先安装后再启动运维 Bot。") from exc

    state = _load_state(config.state_path)
    ws_client = WSClient(bot_id=config.bot_id, secret=config.bot_secret)
    notifier = OpsNotifier(
        ws_client,
        admin_user_id=config.admin_user_id,
        cooldown_seconds=config.notification_cooldown,
    )

    authenticated = asyncio.Event()

    ws_client.on("connected", lambda: print("运维 Bot 已连接企业微信。", flush=True))
    ws_client.on("authenticated", lambda: _mark_authenticated(authenticated))
    ws_client.on("disconnected", lambda reason: print(f"运维 Bot 连接已断开:{reason}", flush=True))
    ws_client.on("error", lambda error: print(f"运维 Bot 连接错误:{error}", flush=True))

    await ws_client.connect()
    await authenticated.wait()
    write_heartbeat(config.heartbeat_dir, "ops_bot")
    monitor_task = asyncio.create_task(_monitor_events(config, notifier, state), name="ops-monitor-events")
    daily_task = asyncio.create_task(_daily_report_loop(config, notifier, state), name="ops-daily-report")
    heartbeat_task = asyncio.create_task(_ops_heartbeat_loop(config), name="ops-bot-heartbeat")
    service_task = asyncio.create_task(_service_heartbeat_monitor_loop(config, notifier), name="ops-service-heartbeat-monitor")
    try:
        await asyncio.Event().wait()
    finally:
        monitor_task.cancel()
        daily_task.cancel()
        heartbeat_task.cancel()
        service_task.cancel()
        _save_state(config.state_path, state)


def _mark_authenticated(authenticated: asyncio.Event) -> None:
    print("运维 Bot 认证成功，开始监控。", flush=True)
    authenticated.set()


async def _monitor_events(config: OpsBotConfig, notifier: OpsNotifier, state: OpsBotState) -> None:
    while True:
        today = datetime.now().date()
        for event in collect_pending_events(events_dir=config.ops_events_dir, today=today, state=state):
            sent = await notifier.notify(
                f"{_severity_label(event.severity)}：{event.subject}",
                _format_event_detail(event),
                cooldown_key=f"{event.source}:{event.subject}:{event.detail[:120]}",
            )
            if sent:
                state.notified_event_ids.add(event.event_id)
                _save_state(config.state_path, state)
        await asyncio.sleep(config.poll_seconds)


async def _daily_report_loop(config: OpsBotConfig, notifier: OpsNotifier, state: OpsBotState) -> None:
    while True:
        now = datetime.now()
        if should_send_daily_report(
            now=now,
            hour=config.daily_report_hour,
            minute=config.daily_report_minute,
            state=state,
        ):
            target_day = previous_workday(now.date())
            report = build_daily_report(
                target_day=target_day,
                chat_log_dir=config.chat_log_dir,
                ops_events_dir=config.ops_events_dir,
            )
            sent = await notifier.notify(
                f"M-Agent 工作日报 {target_day.isoformat()}",
                report,
                cooldown_key=f"daily:{now.date().isoformat()}",
                force=True,
            )
            if sent:
                state.last_daily_report_for = now.date().isoformat()
                _save_state(config.state_path, state)
        await asyncio.sleep(config.poll_seconds)


async def _ops_heartbeat_loop(config: OpsBotConfig) -> None:
    while True:
        try:
            write_heartbeat(config.heartbeat_dir, "ops_bot")
        except Exception as exc:
            print(f"运维 Bot 心跳写入失败:{type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(30)


async def _service_heartbeat_monitor_loop(config: OpsBotConfig, notifier: OpsNotifier) -> None:
    while True:
        stale_items = find_stale_heartbeats(
            config.heartbeat_dir,
            monitored_services=config.monitored_services,
            max_age_seconds=config.heartbeat_max_age_seconds,
        )
        for item in stale_items:
            if item.reason == "missing":
                detail = f"{item.service} 尚未写入心跳，可能未启动或尚未接入心跳。"
            elif item.reason == "invalid":
                detail = f"{item.service} 心跳文件不可读，请检查本地状态文件。"
            else:
                detail = f"{item.service} 心跳已超过 {item.age_seconds} 秒未更新，最后更新时间：{item.updated_at}。"
            await notifier.notify(
                f"服务心跳异常：{item.service}",
                detail,
                cooldown_key=f"heartbeat:{item.service}:{item.reason}",
            )
        await asyncio.sleep(config.poll_seconds)


def _format_event_detail(event: OpsEvent) -> str:
    parts = [
        f"时间：{event.created_at}",
        f"来源：{event.source}",
    ]
    if event.sender_name or event.sender_userid:
        parts.append(f"用户：{event.sender_name or event.sender_userid}")
    if event.skill_id:
        parts.append(f"Skill：{event.skill_id}")
    if event.job_id:
        parts.append(f"Job：{event.job_id}")
    parts.append("")
    parts.append(event.detail)
    return "\n".join(parts)


def _severity_label(severity: str) -> str:
    return {"error": "异常", "warning": "提醒"}.get(severity, "通知")


def _load_state(path: Path) -> OpsBotState:
    if not path.exists():
        return OpsBotState(notified_event_ids=set(), last_daily_report_for="")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return OpsBotState(notified_event_ids=set(), last_daily_report_for="")
    ids = payload.get("notified_event_ids", [])
    return OpsBotState(
        notified_event_ids={str(item) for item in ids},
        last_daily_report_for=str(payload.get("last_daily_report_for", "")),
    )


def _save_state(path: Path, state: OpsBotState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ids = sorted(state.notified_event_ids)[-2000:]
    payload = {
        "notified_event_ids": ids,
        "last_daily_report_for": state.last_daily_report_for,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="M-Agent 运维 Bot")
    parser.add_argument("--check-config", action="store_true", help="检查配置")
    args = parser.parse_args(argv)
    config = load_config()

    if args.check_config:
        print("运维 Bot 配置检查：")
        print(f"Bot ID: {mask_value(config.bot_id)}")
        print(f"接收人: {config.admin_user_id or '未配置'}")
        print(f"事件目录: {config.ops_events_dir}")
        print(f"对话日志目录: {config.chat_log_dir}")
        print(f"状态文件: {config.state_path}")
        print(f"心跳目录: {config.heartbeat_dir}")
        print(f"监控服务: {', '.join(config.monitored_services)}")
        print(f"心跳超时: {config.heartbeat_max_age_seconds} 秒")
        print(f"日报时间: 工作日 {config.daily_report_hour:02d}:{config.daily_report_minute:02d}")
        return

    if not config.bot_id or not config.bot_secret:
        print("错误：缺少 M_AGENT_OPS_BOT_ID 或 M_AGENT_OPS_BOT_SECRET 配置")
        return

    print("正在连接企业微信运维 Bot...", flush=True)
    asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
