"""直报写作 Bot 入口"""

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.platform.app import PlatformApp
from app.platform.config import PlatformConfig
from app.platform.gateway.wecom import format_text_reply
from app.platform.intent import ConversationIntent
from app.platform.models import PlatformResult, UploadedFile
from app.platform.ops.events import OpsEventLogger
from app.platform.ops.heartbeat import write_heartbeat

from .config import load_config
from .intake import IntakeDecision, WritingIntakeStore
from .portal import PortalConfig, PortalService, PortalTokenStore, build_welcome_text, start_portal_server


SUPPORTED_WRITING_FILE_SUFFIXES = {".docx", ".pdf", ".pptx"}
MAX_WRITING_FILE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class WeComFilePayload:
    url: str
    aes_key: str
    filename: str
    content_type: str = ""
    size: int | None = None


def build_platform_config(config) -> PlatformConfig:
    return PlatformConfig(
        model_name=config.model_name,
        anthropic_api_key=config.anthropic_api_key,
        anthropic_base_url=config.anthropic_base_url,
        skills_dir=config.skills_dir,
        jobs_dir=config.jobs_dir,
        policy_db_path=config.policy_db_path,
        bank_db_path=config.bank_db_path,
        conversation_dir=config.conversation_dir,
        model_max_tokens=config.model_max_tokens,
        direct_report_critic_mode=config.direct_report_critic_mode,
        chat_log_enabled=config.chat_log_enabled,
        chat_log_dir=config.chat_log_dir,
        access_policy_path=config.access_policy_path,
        user_registry_path=config.user_registry_path,
        document_max_bytes=config.document_max_bytes,
    )


def mask_config_value(value: str) -> str:
    if len(value) < 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


async def handle_text_with_platform(
    *,
    frame,
    ws_client,
    platform_app,
    req_id_factory,
    ops_event_logger: OpsEventLogger | None = None,
    intake_store: WritingIntakeStore | None = None,
) -> None:
    content = frame.get("body", {}).get("text", {}).get("content", "")
    sender = frame.get("body", {}).get("from", {}).get("userid", "unknown")
    sender_userid = str(sender or "unknown")
    sender_name = _resolve_sender_name(platform_app, sender_userid)
    stream_id = req_id_factory("writing-platform")

    if not content.strip():
        await ws_client.reply_stream(
            frame,
            stream_id,
            "请发送网页链接或文字素材，我会根据需求选择对应写作流程处理。",
            True,
        )
        return

    if intake_store is not None:
        decision = intake_store.handle_text(channel="wecom", sender_userid=sender_userid, text=content)
        if decision.action == "wait":
            await _reply_stream_safely(ws_client, frame, stream_id, decision.reply, True)
            return
        if decision.action == "run":
            await _run_structured_decision(
                decision=decision,
                frame=frame,
                ws_client=ws_client,
                platform_app=platform_app,
                req_id_factory=req_id_factory,
                sender_userid=sender_userid,
                sender_name=sender_name,
                ops_event_logger=ops_event_logger,
                intake_store=intake_store,
            )
            return

    ack_message = _ack_message_for_text(
        platform_app=platform_app,
        sender_userid=sender_userid,
        content=content.strip(),
    )
    await _reply_stream_safely(
        ws_client,
        frame,
        stream_id,
        ack_message,
        True,
    )

    try:
        print(f"直报底座开始处理: user={sender_name}|userid={sender_userid}", flush=True)
        result = await asyncio.to_thread(
            platform_app.handle_text_message,
            channel="wecom",
            sender_userid=sender_userid,
            text=content.strip(),
            ack_message=ack_message,
        )
        print(
            f"直报底座处理完成: user={sender_name}|userid={sender_userid} skill={result.skill_id or 'none'} clarification={result.needs_clarification}",
            flush=True,
        )
        reply = format_text_reply(result)
        if ops_event_logger and _is_link_read_failure_result(result):
            _record_ops_event(
                ops_event_logger,
                severity="warning",
                subject="链接读取失败待用户确认",
                detail=result.message,
                sender_userid=sender_userid,
                sender_name=sender_name,
                skill_id=result.skill_id or "",
            )
    except Exception as exc:
        print(f"直报底座处理失败:{type(exc).__name__}: {exc}", flush=True)
        if ops_event_logger:
            _record_ops_event(
                ops_event_logger,
                severity="error",
                subject="写作处理失败",
                detail=f"{type(exc).__name__}: {exc}",
                sender_userid=sender_userid,
                sender_name=sender_name,
                skill_id=_preview_skill_id(platform_app, sender_userid=sender_userid, content=content.strip()),
            )
        reply = "处理失败，请稍后重试。"
    sent = await _reply_stream_safely(
        ws_client,
        frame,
        req_id_factory("writing-result"),
        reply,
        True,
    )
    if not sent and ops_event_logger:
        _record_ops_event(
            ops_event_logger,
            severity="error",
            subject="写作结果发送失败",
            detail="企业微信最终回复发送失败。",
            sender_userid=sender_userid,
            sender_name=sender_name,
        )


def _cleanup_decision_files(files: tuple[UploadedFile, ...]) -> None:
    for item in files:
        if not item.delete_after_read or not item.stored_path:
            continue
        source = Path(item.stored_path)
        source.unlink(missing_ok=True)
        for directory in (source.parent, source.parent.parent):
            try:
                directory.rmdir()
            except OSError:
                break


async def handle_file_with_platform(
    *,
    frame,
    ws_client,
    platform_app,
    req_id_factory,
    intake_store: WritingIntakeStore,
    ops_event_logger: OpsEventLogger | None = None,
) -> None:
    sender = frame.get("body", {}).get("from", {}).get("userid", "unknown")
    sender_userid = str(sender or "unknown")
    sender_name = _resolve_sender_name(platform_app, sender_userid)
    stream_id = req_id_factory("writing-file")
    payload = extract_file_payload(frame)

    if payload is None:
        await _reply_stream_safely(
            ws_client,
            frame,
            stream_id,
            "文件信息不完整，请重新发送文件，或把文字素材直接粘贴给我。",
            True,
        )
        return

    announced_suffix = Path(payload.filename).suffix.lower()
    if announced_suffix and announced_suffix not in SUPPORTED_WRITING_FILE_SUFFIXES:
        await _reply_stream_safely(
            ws_client,
            frame,
            stream_id,
            "暂时只支持 Word(.docx)、PDF(.pdf) 和 PPT(.pptx) 文件。",
            True,
        )
        return

    if payload.size is not None and payload.size > MAX_WRITING_FILE_BYTES:
        await _reply_stream_safely(
            ws_client,
            frame,
            stream_id,
            f"文件过大，当前单个文件上限为 {MAX_WRITING_FILE_BYTES // 1024 // 1024}MB。",
            True,
        )
        return

    limit_message = intake_store.file_limit_message(
        channel="wecom",
        sender_userid=sender_userid,
        incoming_size=payload.size,
    )
    if limit_message:
        await _reply_stream_safely(ws_client, frame, stream_id, limit_message, True)
        return

    await _reply_stream_safely(ws_client, frame, stream_id, "收到文件，正在读取。", True)
    try:
        download = await asyncio.wait_for(ws_client.download_file(payload.url, payload.aes_key), timeout=60)
        content = _extract_download_buffer(download)
        filename = _extract_download_filename(download) or payload.filename
        if Path(filename).suffix.lower() not in SUPPORTED_WRITING_FILE_SUFFIXES:
            await _reply_stream_safely(
                ws_client,
                frame,
                req_id_factory("writing-file-reject"),
                "暂时只支持 Word(.docx)、PDF(.pdf) 和 PPT(.pptx) 文件。",
                True,
            )
            return
        if len(content) > MAX_WRITING_FILE_BYTES:
            await _reply_stream_safely(
                ws_client,
                frame,
                req_id_factory("writing-file-too-large"),
                f"文件过大，当前单个文件上限为 {MAX_WRITING_FILE_BYTES // 1024 // 1024}MB。",
                True,
            )
            return
        decision = intake_store.add_file(
            channel="wecom",
            sender_userid=sender_userid,
            file=UploadedFile(
                filename=filename,
                content=content,
                content_type=payload.content_type,
            ),
        )
        await _reply_stream_safely(
            ws_client,
            frame,
            req_id_factory("writing-file-ready"),
            decision.reply,
            True,
        )
    except Exception as exc:
        print(f"写作文件读取失败:{type(exc).__name__}: {exc}", flush=True)
        if ops_event_logger:
            _record_ops_event(
                ops_event_logger,
                severity="error",
                subject="写作文件读取失败",
                detail=f"{type(exc).__name__}: {exc}",
                sender_userid=sender_userid,
                sender_name=sender_name,
            )
        await _reply_stream_safely(
            ws_client,
            frame,
            req_id_factory("writing-file-error"),
            "文件读取失败。已经提醒管理员排查，你也可以先把文字内容粘贴给我继续处理。",
            True,
        )


async def _run_structured_decision(
    *,
    decision: IntakeDecision,
    frame,
    ws_client,
    platform_app,
    req_id_factory,
    sender_userid: str,
    sender_name: str,
    ops_event_logger: OpsEventLogger | None,
    intake_store: WritingIntakeStore | None = None,
) -> None:
    ack_message = decision.ack_message or "收到，正在按写作流程处理，请稍后……"
    await _reply_stream_safely(ws_client, frame, req_id_factory("writing-platform"), ack_message, True)
    preserve_files = False
    output_file: Path | None = None
    try:
        print(
            f"写作组装任务开始: user={sender_name}|userid={sender_userid} skill={decision.skill_id or 'none'}",
            flush=True,
        )
        result = await asyncio.to_thread(
            platform_app.handle_structured_request,
            channel="wecom",
            sender_userid=sender_userid,
            skill_id=decision.skill_id or "",
            text=decision.text,
            material_text=decision.material_text,
            urls=list(decision.urls),
            files=list(decision.files),
        )
        print(
            f"写作组装任务完成: user={sender_name}|userid={sender_userid} skill={result.skill_id or 'none'} clarification={result.needs_clarification}",
            flush=True,
        )
        output_file = _result_output_file(result)
        reply = (result.message or "已生成综合调研 Word 初稿。").strip() if output_file else format_text_reply(result)
        if result.needs_clarification and intake_store is not None:
            intake_store.mark_clarification(
                channel="wecom",
                sender_userid=sender_userid,
                message=result.message,
            )
            preserve_files = True
        elif intake_store is not None:
            intake_store.clear(channel="wecom", sender_userid=sender_userid)
        if ops_event_logger and _is_link_read_failure_result(result):
            _record_ops_event(
                ops_event_logger,
                severity="warning",
                subject="链接读取失败待用户确认",
                detail=result.message,
                sender_userid=sender_userid,
                sender_name=sender_name,
                skill_id=result.skill_id or "",
            )
    except Exception as exc:
        print(f"写作组装任务失败:{type(exc).__name__}: {exc}", flush=True)
        if ops_event_logger:
            _record_ops_event(
                ops_event_logger,
                severity="error",
                subject="写作处理失败",
                detail=f"{type(exc).__name__}: {exc}",
                sender_userid=sender_userid,
                sender_name=sender_name,
                skill_id=decision.skill_id or "",
            )
        reply = "处理失败，请稍后重试。"
        if intake_store is not None:
            intake_store.clear(channel="wecom", sender_userid=sender_userid)
    finally:
        if not preserve_files:
            _cleanup_decision_files(decision.files)

    sent = await _reply_stream_safely(
        ws_client,
        frame,
        req_id_factory("writing-result"),
        reply,
        True,
    )
    if not sent and ops_event_logger:
        _record_ops_event(
            ops_event_logger,
            severity="error",
            subject="写作结果发送失败",
            detail="企业微信最终回复发送失败。",
            sender_userid=sender_userid,
            sender_name=sender_name,
        )
    if output_file is not None:
        file_sent = await _reply_file_safely(ws_client, frame, output_file)
        if not file_sent and ops_event_logger:
            _record_ops_event(
                ops_event_logger,
                severity="error",
                subject="写作结果文件发送失败",
                detail=f"生成结果未能作为企业微信文件发送：{output_file.name}",
                sender_userid=sender_userid,
                sender_name=sender_name,
                skill_id=decision.skill_id or "",
            )


async def _reply_stream_safely(ws_client, frame, stream_id: str, message: str, finish: bool) -> bool:
    try:
        await asyncio.wait_for(
            ws_client.reply_stream(frame, stream_id, message, finish),
            timeout=15,
        )
        print(f"企业微信回复发送成功:{stream_id}", flush=True)
        return True
    except Exception as exc:
        print(f"企业微信回复发送失败:{type(exc).__name__}: {exc}", flush=True)
        return False


def _result_output_file(result: PlatformResult) -> Path | None:
    if result.needs_clarification or result.skill_id != "research_synthesis":
        return None
    raw_path = str(result.output.get("output_file", "") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).resolve()
    if path.suffix.lower() != ".docx" or path.parent.name != "output":
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if not path.is_file() or size <= 0 or size > MAX_WRITING_FILE_BYTES:
        return None
    return path


async def _reply_file_safely(ws_client, frame, path: Path) -> bool:
    try:
        upload_result = await asyncio.wait_for(
            ws_client.upload_media(path.read_bytes(), type="file", filename=path.name),
            timeout=60,
        )
        media_id = str(upload_result.get("media_id", "") or "")
        if not media_id:
            raise ValueError("企业微信上传结果缺少 media_id")
        await asyncio.wait_for(ws_client.reply_media(frame, "file", media_id), timeout=15)
        print(f"企业微信结果文件发送成功:{path.name}", flush=True)
        return True
    except Exception as exc:
        print(f"企业微信结果文件发送失败:{type(exc).__name__}: {exc}", flush=True)
        return False


def _record_ops_event(
    ops_event_logger: OpsEventLogger,
    *,
    severity: str,
    subject: str,
    detail: str,
    sender_userid: str = "",
    sender_name: str = "",
    skill_id: str = "",
) -> None:
    try:
        ops_event_logger.record(
            source="writing_bot",
            severity=severity,
            subject=subject,
            detail=detail,
            sender_userid=sender_userid,
            sender_name=sender_name,
            skill_id=skill_id,
        )
    except Exception as exc:
        print(f"写作运维事件记录失败:{type(exc).__name__}: {exc}", flush=True)


async def _heartbeat_loop(root_dir, service: str) -> None:
    while True:
        try:
            write_heartbeat(root_dir, service)
        except Exception as exc:
            print(f"写作心跳写入失败:{type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(30)


def _is_link_read_failure_result(result: PlatformResult) -> bool:
    if not result.needs_clarification:
        return False
    text = f"{result.message}\n{result.output}".lower()
    return "链接读取失败" in text or "读取失败" in text or "read_error" in text


def _preview_skill_id(platform_app, *, sender_userid: str, content: str) -> str:
    previewer = getattr(platform_app, "preview_text_route", None)
    if not callable(previewer):
        return ""
    try:
        route = previewer(channel="wecom", sender_userid=sender_userid, text=content)
    except Exception:
        return ""
    return str(route.skill_id or "")


def _ack_message_for_text(*, platform_app, sender_userid: str, content: str) -> str:
    previewer = getattr(platform_app, "preview_text_route", None)
    if callable(previewer):
        try:
            route = previewer(channel="wecom", sender_userid=sender_userid, text=content)
        except Exception:
            route = None
        if route is not None:
            if route.inputs.get("revision"):
                return "收到，我沿着上一稿继续改。"
            if route.needs_clarification or not route.skill_id:
                return "收到，我先判断一下你的需求。"
            return f"收到，正在按{_ack_label_for_skill(route.skill_id)}流程处理，请稍后……"

    classifier = getattr(platform_app, "classify_text_intent", None)
    if callable(classifier):
        try:
            intent = classifier(channel="wecom", sender_userid=sender_userid, text=content)
        except Exception:
            intent = ConversationIntent.NEW_TASK
        if intent == ConversationIntent.REVISE_PREVIOUS:
            return "收到，我沿着上一稿继续改。"
        if intent == ConversationIntent.CLARIFY:
            return "收到，我先判断一下你的需求。"
    return "收到，正在按写作流程处理，请稍后……"


def _ack_label_for_skill(skill_id: str | None) -> str:
    labels = {
        "direct_report": "直报写作",
        "writer1": "简报写作",
        "writer2": "多素材简报写作",
        "review": "审核",
        "rewrite": "材料润色",
        "research_synthesis": "综合调研整合",
    }
    return labels.get(str(skill_id or ""), "写作")


def _resolve_sender_name(platform_app, sender_userid: str) -> str:
    resolver = getattr(platform_app, "resolve_sender_name", None)
    if not callable(resolver):
        return sender_userid
    try:
        return str(resolver(sender_userid) or sender_userid)
    except Exception:
        return sender_userid


def extract_file_payload(frame) -> WeComFilePayload | None:
    body = frame.get("body", {})
    file_info = body.get("file") or body.get("attachment") or body.get("media") or {}
    url = _first_present(file_info, ("url", "download_url", "downloadUrl", "file_url", "fileUrl"))
    aes_key = _first_present(file_info, ("aeskey", "aes_key", "aesKey"))
    filename = _first_present(file_info, ("filename", "file_name", "name")) or "upload"
    content_type = _first_present(file_info, ("content_type", "contentType", "mime_type", "mimeType")) or ""
    size = _nonnegative_int(_first_present(file_info, ("size", "file_size", "fileSize", "length")))
    if not url or not aes_key:
        return None
    return WeComFilePayload(
        url=str(url),
        aes_key=str(aes_key),
        filename=str(filename),
        content_type=str(content_type),
        size=size,
    )


def _first_present(data: dict[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = data.get(key)
        if value:
            return value
    return None


def _nonnegative_int(value: object | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _extract_download_buffer(download) -> bytes:
    if isinstance(download, bytes):
        return download
    if isinstance(download, dict):
        buffer = download.get("buffer") or download.get("content") or download.get("data")
        if isinstance(buffer, bytes):
            return buffer
    raise ValueError("download_file 未返回文件内容")


def _extract_download_filename(download) -> str:
    if isinstance(download, dict):
        filename = download.get("filename") or download.get("file_name") or download.get("name")
        return str(filename or "")
    return ""


async def run_bot(config) -> None:
    try:
        from wecom_aibot_sdk import WSClient, generate_req_id
    except ImportError as exc:
        raise RuntimeError(
            "缺少依赖 wecom-aibot-sdk。请在项目根目录运行：uv sync --locked"
        ) from exc

    platform_app = PlatformApp.from_config(build_platform_config(config))
    ops_event_logger = OpsEventLogger(config.ops_events_dir) if config.ops_events_dir else None
    intake_store = WritingIntakeStore(
        ttl_seconds=config.intake_ttl_seconds,
        storage_dir=config.intake_dir,
    )
    heartbeat_task = None
    if config.ops_heartbeat_dir:
        heartbeat_task = asyncio.create_task(
            _heartbeat_loop(config.ops_heartbeat_dir, "writing_bot"),
            name="writing-bot-heartbeat",
        )
    token_store = PortalTokenStore(ttl_seconds=config.portal_token_ttl_seconds)

    ws_client = WSClient(
        bot_id=config.wecom_bot_id,
        secret=config.wecom_bot_secret,
    )
    loop = asyncio.get_running_loop()

    def send_portal_message(chatid: str, body: dict[str, object]) -> None:
        future = asyncio.run_coroutine_threadsafe(ws_client.send_message(chatid, body), loop)
        future.result(timeout=10)

    portal_server = start_portal_server(
        PortalConfig(
            host=config.portal_host,
            port=config.portal_port,
            base_url=config.portal_base_url,
            token_ttl_seconds=config.portal_token_ttl_seconds,
        ),
        PortalService(
            platform_app=platform_app,
            message_sender=send_portal_message,
            token_store=token_store,
            ops_event_logger=ops_event_logger,
        ),
    )

    ws_client.on("connected", lambda: print("写作 Bot 已连接企业微信。", flush=True))
    ws_client.on("authenticated", lambda: print("认证成功，等待消息。", flush=True))
    ws_client.on(
        "disconnected",
        lambda reason: (
            print(f"连接已断开：{reason}", flush=True),
            _record_ops_event(
                ops_event_logger,
                severity="error",
                subject="写作 Bot 连接断开",
                detail=str(reason),
            )
            if ops_event_logger
            else None,
        ),
    )
    ws_client.on(
        "error",
        lambda error: (
            print(f"连接错误：{error}", flush=True),
            _record_ops_event(
                ops_event_logger,
                severity="error",
                subject="写作 Bot 连接错误",
                detail=str(error),
            )
            if ops_event_logger
            else None,
        ),
    )

    async def on_text(frame):
        sender = frame.get("body", {}).get("from", {}).get("userid", "unknown")
        sender_userid = str(sender or "unknown")
        sender_name = platform_app.resolve_sender_name(sender_userid)
        content = frame.get("body", {}).get("text", {}).get("content", "")
        print(f"收到直报消息 from {sender_name}|userid={sender_userid}: {content[:50]}...", flush=True)
        await handle_text_with_platform(
            frame=frame,
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=generate_req_id,
            ops_event_logger=ops_event_logger,
            intake_store=intake_store,
        )

    async def on_file(frame):
        sender = frame.get("body", {}).get("from", {}).get("userid", "unknown")
        sender_userid = str(sender or "unknown")
        sender_name = platform_app.resolve_sender_name(sender_userid)
        file_info = frame.get("body", {}).get("file") or {}
        filename = file_info.get("filename") or file_info.get("file_name") or file_info.get("name") or "upload"
        print(f"收到写作文件 from {sender_name}|userid={sender_userid}: {filename}", flush=True)
        await handle_file_with_platform(
            frame=frame,
            ws_client=ws_client,
            platform_app=platform_app,
            req_id_factory=generate_req_id,
            intake_store=intake_store,
            ops_event_logger=ops_event_logger,
        )

    async def on_enter(frame):
        sender = frame.get("body", {}).get("from", {}).get("userid", "unknown")
        token = token_store.issue(str(sender or "unknown"))
        await ws_client.reply_welcome(
            frame,
            {
                "msgtype": "text",
                "text": {"content": build_welcome_text(base_url=config.portal_base_url, token=token)},
            },
        )

    ws_client.on("message.text", on_text)
    ws_client.on("message.file", on_file)
    ws_client.on("event.enter_chat", on_enter)

    await ws_client.connect()
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await ws_client.disconnect()
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
        portal_server.shutdown()
        portal_server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="直报写作 Bot")
    parser.add_argument("--check-config", action="store_true", help="检查配置")
    args = parser.parse_args(argv)

    config = load_config()

    if not config.wecom_bot_id or not config.wecom_bot_secret:
        print("错误：缺少 WRITING_BOT_ID 或 WRITING_BOT_SECRET 配置")
        return

    if args.check_config:
        platform_config = build_platform_config(config)
        print("配置检查通过。")
        print(f"Bot ID: {mask_config_value(config.wecom_bot_id)}")
        print(f"模型: {config.model_name}")
        print(f"模型输出上限: {config.model_max_tokens}")
        print(f"直报 critic 模式: {config.direct_report_critic_mode}")
        print(f"Skills 目录: {config.skills_dir}")
        print(f"任务目录: {config.jobs_dir}")
        print(f"对话日志: {'开启' if platform_config.chat_log_enabled else '关闭'}")
        print(f"对话日志目录: {platform_config.chat_log_dir or platform_config.jobs_dir.parent / 'chat_logs'}")
        print(f"用户名称表: {platform_config.user_registry_path or '未配置'}")
        print(f"素材入口: {config.portal_base_url}")
        print(f"多消息任务暂存: {config.intake_ttl_seconds} 秒")
        print(f"权限配置: {config.access_policy_path or '未配置，本地开发默认允许已启用 skill'}")
        return

    print("正在连接企业微信写作 Bot...", flush=True)
    asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
