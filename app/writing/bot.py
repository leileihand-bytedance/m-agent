"""直报写作 Bot 入口"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from app.platform.app import PlatformApp
from app.platform.attachment_delivery import (
    AttachmentDelivery,
    AttachmentDeliveryConfig,
    DeliveryRequest,
    DeliveryResult,
)
from app.platform.config import PlatformConfig, ROOT
from app.platform.delivery_state import DeliveryOutcome, capture_wecom_delivery
from app.platform.gateway.wecom import extract_message_id, format_text_reply
from app.platform.intent import ConversationIntent
from app.platform.models import PlatformResult, UploadedFile
from app.platform.ops.events import OpsEventLogger
from app.platform.ops.heartbeat import write_heartbeat
from app.platform.task_execution import (
    ClaimLimits,
    PersistentTaskExecutor,
    TaskLifecycleObserver,
    TaskRepository,
)
from app.platform.task_relations import RelationAction, TaskCardStatus, TaskRelation
from app.platform.runtime_environment import (
    RuntimeEnvironment,
    RuntimeEnvironmentError,
    validate_bot_startup,
)

from .config import load_config
from .intake import IntakeDecision, WritingIntakeStore
from .portal import PortalConfig, PortalService, PortalTokenStore, build_welcome_text, start_portal_server
from .task_execution import (
    QUEUEABLE_WRITING_SKILLS,
    WRITING_COST_CLASS,
    WRITING_TASK_TYPES,
    WritingTaskService,
    WritingTaskWorkspace,
)


SUPPORTED_WRITING_FILE_SUFFIXES = {".docx", ".pdf", ".pptx"}
MAX_WRITING_FILE_BYTES = 20 * 1024 * 1024
MAX_WRITING_OUTPUT_FILE_BYTES = 100 * 512 * 1024


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
        model_timeout_seconds=config.model_timeout_seconds,
        model_max_attempts=config.model_max_attempts,
        model_retry_backoff_seconds=config.model_retry_backoff_seconds,
        direct_report_critic_mode=config.direct_report_critic_mode,
        chat_log_enabled=config.chat_log_enabled,
        chat_log_dir=config.chat_log_dir,
        access_policy_path=config.access_policy_path,
        user_registry_path=config.user_registry_path,
        document_max_bytes=config.document_max_bytes,
        document_ocr_enabled=config.document_ocr_enabled,
        task_queue_db_path=config.task_queue_db_path,
        task_relation_db_path=config.task_relation_db_path,
        search_api_key=config.search_api_key,
        search_api_base_url=config.search_api_base_url,
        runtime_mode=config.runtime_mode,
        data_root=config.data_root,
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
    attachment_delivery: AttachmentDelivery | None = None,
    task_service: WritingTaskService | None = None,
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

    control_reply = _handle_task_control(
        platform_app=platform_app,
        task_service=task_service,
        sender_userid=sender_userid,
        content=content.strip(),
    )
    if control_reply:
        await _reply_stream_safely(
            ws_client,
            frame,
            stream_id,
            control_reply,
            True,
        )
        return

    if (
        intake_store is not None
        and intake_store.has_materials(channel="wecom", sender_userid=sender_userid)
        and (
            _looks_like_material_relation_command(content.strip())
            or _has_pending_task_relation(platform_app, sender_userid=sender_userid)
        )
    ):
        relation = await asyncio.to_thread(
            _resolve_task_relation,
            platform_app,
            sender_userid=sender_userid,
            content=content.strip(),
            has_new_material=True,
            persist=True,
        )
        if relation is not None and relation.action is RelationAction.ASK:
            await _reply_stream_safely(ws_client, frame, stream_id, relation.question, True)
            return
        if relation is not None and relation.relation in {
            TaskRelation.ADD_MATERIAL,
            TaskRelation.DERIVE,
            TaskRelation.ANSWER_CLARIFICATION,
        }:
            decision = intake_store.apply_task_relation(
                channel="wecom",
                sender_userid=sender_userid,
                instruction=relation.effective_text or content.strip(),
                skill_id=relation.suggested_skill_id,
                task_relation=(
                    TaskRelation.ADD_MATERIAL.value
                    if relation.relation is TaskRelation.ANSWER_CLARIFICATION
                    else relation.relation.value
                ),
                target_task_id=relation.target_task_id,
                parent_task_id=relation.parent_task_id,
                material_role=relation.material_role.value,
            )
            if decision.action == "run":
                if task_service is not None and decision.skill_id in QUEUEABLE_WRITING_SKILLS:
                    await _queue_structured_decision(
                        decision=decision,
                        frame=frame,
                        ws_client=ws_client,
                        task_service=task_service,
                        intake_store=intake_store,
                        req_id_factory=req_id_factory,
                        sender_userid=sender_userid,
                        sender_name=sender_name,
                        ops_event_logger=ops_event_logger,
                    )
                else:
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
                        attachment_delivery=attachment_delivery,
                    )
                return

    direct_revision = False
    if intake_store is not None:
        direct_revision = await asyncio.to_thread(
            _prepare_direct_revision,
            platform_app,
            intake_store=intake_store,
            sender_userid=sender_userid,
            content=content.strip(),
        )
    if intake_store is not None and not direct_revision:
        decision = intake_store.handle_text(
            channel="wecom",
            sender_userid=sender_userid,
            text=content,
            message_id=extract_message_id(frame),
        )
        if decision.action in {"wait", "cancel"}:
            await _reply_stream_safely(ws_client, frame, stream_id, decision.reply, True)
            return
        if decision.action == "run":
            if task_service is not None and decision.skill_id in QUEUEABLE_WRITING_SKILLS:
                await _queue_structured_decision(
                    decision=decision,
                    frame=frame,
                    ws_client=ws_client,
                    task_service=task_service,
                    intake_store=intake_store,
                    req_id_factory=req_id_factory,
                    sender_userid=sender_userid,
                    sender_name=sender_name,
                    ops_event_logger=ops_event_logger,
                )
                return
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
                attachment_delivery=attachment_delivery,
            )
            return


    queued_route = (
        await asyncio.to_thread(
            _queueable_text_route,
            platform_app,
            sender_userid=sender_userid,
            content=content.strip(),
        )
        if task_service is not None
        else None
    )
    if task_service is not None and queued_route is not None:
        message_id = extract_message_id(frame) or f"fallback-{uuid4().hex}"
        try:
            ack_message = await asyncio.to_thread(
                _ack_message_for_text,
                platform_app=platform_app,
                sender_userid=sender_userid,
                content=content.strip(),
            )
            submission = await asyncio.to_thread(
                task_service.submit_text,
                channel="wecom",
                sender_userid=sender_userid,
                sender_name=sender_name,
                message_id=message_id,
                skill_id=str(queued_route.skill_id),
                text=content.strip(),
                ack_message=ack_message,
            )
        except Exception as exc:
            print(f"写作任务入队失败:{type(exc).__name__}: {exc}", flush=True)
            if ops_event_logger:
                _record_ops_event(
                    ops_event_logger,
                    severity="error",
                    subject="写作任务入队失败",
                    detail=f"{type(exc).__name__}: {exc}",
                    sender_userid=sender_userid,
                    sender_name=sender_name,
                    skill_id=str(queued_route.skill_id or ""),
                )
            await _reply_stream_safely(
                ws_client,
                frame,
                req_id_factory("writing-queue-error"),
                "写作任务暂时无法受理，已经提醒管理员排查，请稍后重试。",
                True,
            )
            return
        await _reply_stream_safely(
            ws_client,
            frame,
            req_id_factory("writing-queued"),
            _queued_acceptance_message(
                skill_id=str(queued_route.skill_id),
                created=submission.created,
                revision=bool(queued_route.inputs.get("revision")),
            ),
            True,
        )
        return

    ack_message = await asyncio.to_thread(
        _ack_message_for_text,
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
    task_service: WritingTaskService | None = None,
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
            message_id=extract_message_id(frame),
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
    attachment_delivery: AttachmentDelivery | None = None,
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
        request_kwargs = _relation_request_kwargs(decision)
        result = await asyncio.to_thread(
            platform_app.handle_structured_request,
            channel="wecom",
            sender_userid=sender_userid,
            skill_id=decision.skill_id or "",
            text=decision.text,
            material_text=decision.material_text,
            urls=list(decision.urls),
            files=list(decision.files),
            **request_kwargs,
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
        delivery = attachment_delivery or AttachmentDelivery(ops_event_logger=ops_event_logger)
        delivery_result = await _reply_file_safely(
            ws_client,
            frame,
            output_file,
            attachment_delivery=delivery,
            sender_userid=sender_userid,
            sender_name=sender_name,
            skill_id=decision.skill_id or "",
        )
        if not delivery_result.delivered:
            await _reply_stream_safely(
                ws_client,
                frame,
                req_id_factory("writing-file-delivery-failed"),
                delivery_result.user_message,
                True,
            )


async def _queue_structured_decision(
    *,
    decision: IntakeDecision,
    frame,
    ws_client,
    task_service: WritingTaskService,
    intake_store: WritingIntakeStore,
    req_id_factory,
    sender_userid: str,
    sender_name: str,
    ops_event_logger: OpsEventLogger | None,
) -> None:
    message_id = extract_message_id(frame) or f"fallback-{uuid4().hex}"
    try:
        submission = await asyncio.to_thread(
            task_service.submit_structured,
            channel="wecom",
            sender_userid=sender_userid,
            sender_name=sender_name,
            message_id=message_id,
            skill_id=decision.skill_id or "",
            text=decision.text,
            material_text=decision.material_text,
            urls=decision.urls,
            files=decision.files,
            **_relation_request_kwargs(decision),
        )
    except Exception as exc:
        print(f"写作组装任务入队失败:{type(exc).__name__}: {exc}", flush=True)
        if ops_event_logger:
            _record_ops_event(
                ops_event_logger,
                severity="error",
                subject="写作任务入队失败",
                detail=f"{type(exc).__name__}: {exc}",
                sender_userid=sender_userid,
                sender_name=sender_name,
                skill_id=decision.skill_id or "",
            )
        await _reply_stream_safely(
            ws_client,
            frame,
            req_id_factory("writing-queue-error"),
            "写作任务暂时无法受理，材料仍为你保留。已经提醒管理员排查，请稍后回复“开始写”重试。",
            True,
        )
        return
    intake_store.clear(channel="wecom", sender_userid=sender_userid)
    await _reply_stream_safely(
        ws_client,
        frame,
        req_id_factory("writing-queued"),
        _queued_acceptance_message(
            skill_id=decision.skill_id or "",
            created=submission.created,
            revision=decision.task_relation in {
                TaskRelation.CONTINUE.value,
                TaskRelation.ADD_MATERIAL.value,
                TaskRelation.ANSWER_CLARIFICATION.value,
            },
        ),
        True,
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
    allowed_suffixes = {
        "research_synthesis": ".docx",
        "shenyinxie_news": ".docx",
        "internal_weekly": ".md",
    }
    if result.needs_clarification or result.skill_id not in allowed_suffixes:
        return None
    raw_path = str(result.output.get("output_file", "") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).resolve()
    if path.suffix.lower() != allowed_suffixes[result.skill_id] or path.parent.name != "output":
        return None
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if not path.is_file() or size <= 0 or size > MAX_WRITING_OUTPUT_FILE_BYTES:
        return None
    return path


async def _reply_file_safely(
    ws_client,
    frame,
    path: Path,
    *,
    attachment_delivery: AttachmentDelivery,
    sender_userid: str,
    sender_name: str,
    skill_id: str,
) -> DeliveryResult:
    task_dir = path.parent.parent
    result = await attachment_delivery.deliver(
        ws_client=ws_client,
        request=DeliveryRequest(
            file_path=path,
            allowed_root=task_dir,
            frame=frame,
            task_dir=task_dir,
            source="writing_bot",
            sender_userid=sender_userid,
            sender_name=sender_name,
            skill_id=skill_id,
            job_id=task_dir.name,
        ),
    )
    if result.delivered:
        print(f"企业微信结果文件发送成功:{path.name}", flush=True)
    else:
        print(
            f"企业微信结果文件发送失败:{result.error_code or result.status}",
            flush=True,
        )
    return result


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


async def _send_active_writing_text(
    ws_client,
    recipient: str,
    text: str,
    *,
    timeout_seconds: float = 30.0,
) -> DeliveryOutcome:
    sender = getattr(ws_client, "send_message", None)
    if not callable(sender):
        raise RuntimeError("企业微信 SDK 不支持主动文本消息")
    return await capture_wecom_delivery(
        lambda: sender(
            recipient,
            {"msgtype": "markdown", "markdown": {"content": text}},
        ),
        timeout_seconds=timeout_seconds,
    )


async def _run_writing_task_worker_supervised(
    *,
    task_executor: PersistentTaskExecutor,
    stop_event: asyncio.Event,
    poll_interval: float,
    worker_count: int,
    recovery_interval: float,
    ops_event_logger: OpsEventLogger | None,
    restart_delay_seconds: float = 5.0,
) -> None:
    while not stop_event.is_set():
        try:
            await task_executor.run_forever(
                stop_event=stop_event,
                poll_interval=poll_interval,
                worker_count=worker_count,
                recovery_interval=recovery_interval,
            )
            if stop_event.is_set():
                return
            raise RuntimeError("写作后台任务 worker 意外结束")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"写作后台任务 worker 异常退出，将自动重启:{type(exc).__name__}: {exc}", flush=True)
            if ops_event_logger:
                _record_ops_event(
                    ops_event_logger,
                    severity="error",
                    subject="写作后台任务 worker 异常退出",
                    detail="持久任务 worker 已退出，系统将自动重启。",
                    skill_id="writing_task_worker",
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=restart_delay_seconds)
            except TimeoutError:
                pass


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


def _queueable_text_route(platform_app, *, sender_userid: str, content: str):
    previewer = getattr(platform_app, "preview_text_route", None)
    if not callable(previewer):
        return None
    try:
        route = previewer(channel="wecom", sender_userid=sender_userid, text=content)
    except Exception:
        return None
    if (
        route.needs_clarification
        or route.skill_id not in QUEUEABLE_WRITING_SKILLS
    ):
        return None
    return route


def _prepare_direct_revision(
    platform_app,
    *,
    intake_store: WritingIntakeStore,
    sender_userid: str,
    content: str,
) -> bool:
    classifier = getattr(platform_app, "classify_text_intent", None)
    if not callable(classifier):
        return False
    try:
        intent = classifier(
            channel="wecom",
            sender_userid=sender_userid,
            text=content,
        )
    except Exception:
        return False
    if intent != ConversationIntent.REVISE_PREVIOUS:
        return False
    return intake_store.prepare_direct_revision(
        channel="wecom",
        sender_userid=sender_userid,
    )


def _resolve_task_relation(
    platform_app,
    *,
    sender_userid: str,
    content: str,
    has_new_material: bool,
    persist: bool,
):
    resolver = getattr(platform_app, "resolve_task_relation", None)
    if not callable(resolver):
        return None
    try:
        return resolver(
            channel="wecom",
            sender_userid=sender_userid,
            text=content,
            route_skill_id=None,
            has_new_material=has_new_material,
            persist=persist,
        )
    except Exception:
        return None


def _relation_request_kwargs(decision: IntakeDecision) -> dict[str, str]:
    values = {
        "task_relation": decision.task_relation,
        "target_task_id": decision.target_task_id,
        "parent_task_id": decision.parent_task_id,
        "material_role": decision.material_role,
    }
    defaults = {"task_relation": TaskRelation.NEW_TASK.value, "material_role": "new_task"}
    return {
        key: value
        for key, value in values.items()
        if value and value != defaults.get(key)
    }


def _has_pending_task_relation(platform_app, *, sender_userid: str) -> bool:
    service = getattr(platform_app, "task_relation_service", None)
    repository = getattr(service, "repository", None)
    reader = getattr(repository, "pending_decision", None)
    if not callable(reader):
        return False
    try:
        return reader(channel="wecom", user_id=sender_userid) is not None
    except Exception:
        return False


def _handle_task_control(
    *,
    platform_app,
    task_service: WritingTaskService | None,
    sender_userid: str,
    content: str,
) -> str:
    is_list = any(marker in content for marker in ("任务列表", "有哪些任务", "我有几个任务", "我有几篇稿", "看看任务"))
    is_switch = any(marker in content for marker in ("切换到", "切到", "转到", "继续处理")) or (
        "回到" in content
        and "版" not in content
        and any(marker in content for marker in ("任务", "稿", "简报", "直报"))
    )
    is_cancel = any(marker in content for marker in ("取消", "不要做了", "不用做了", "停止这个任务", "结束这个任务"))
    if not (is_list or is_switch or is_cancel):
        return ""
    relation = _resolve_task_relation(
        platform_app,
        sender_userid=sender_userid,
        content=content,
        has_new_material=False,
        persist=is_switch or is_cancel,
    )
    if relation is None:
        return ""
    if relation.action is RelationAction.ASK:
        return relation.question
    service = getattr(platform_app, "task_relation_service", None)
    if service is None or not relation.target_task_id:
        return ""
    try:
        card = service.repository.get_task(
            relation.target_task_id,
            channel="wecom",
            user_id=sender_userid,
        )
    except (KeyError, PermissionError):
        return ""
    if relation.relation is TaskRelation.SWITCH:
        return f"已切换到《{card.title}》。你可以继续提出修改要求，或补充新材料。"
    if relation.relation is not TaskRelation.CANCEL:
        return ""
    if card.status is TaskCardStatus.RUNNING:
        return f"《{card.title}》已经开始处理，当前不能强行中断；完成后你可以继续修改。"
    if card.status is TaskCardStatus.QUEUED and task_service is not None:
        cancel = getattr(task_service, "cancel_platform_job", None)
        status = (
            cancel(sender_userid=sender_userid, platform_job_id=card.current_job_id)
            if callable(cancel)
            else "not_found"
        )
        if status == "running":
            return f"《{card.title}》已经开始处理，当前不能强行中断；完成后你可以继续修改。"
        if status not in {"cancelled", "not_found"}:
            return f"《{card.title}》当前状态不允许取消。"
    service.repository.set_status(card.task_id, TaskCardStatus.CANCELLED)
    return f"已取消《{card.title}》，这项任务不会再作为后续改稿目标。"


def _looks_like_material_relation_command(content: str) -> bool:
    material_markers = ("补到", "加到", "加入", "补充到", "替换", "作为参考", "参考材料")
    derive_markers = ("沿用", "参考上一", "基于上一", "按上一", "用原来的结构")
    return any(marker in content for marker in material_markers) or (
        any(marker in content for marker in derive_markers)
        and any(marker in content for marker in ("另写一份", "另写一篇", "重新写一份", "重新写一篇"))
    )


def _queued_acceptance_message(*, skill_id: str, created: bool, revision: bool = False) -> str:
    label = _ack_label_for_skill(skill_id)
    result_label = "修改稿" if revision else "初稿"
    if created:
        return f"已进入{label}队列，完成后会自动发送{result_label}。"
    return f"这项{label}任务已经在处理中，无需重复提交。完成后会自动发送{result_label}。"


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
        "review": "审核",
        "rewrite": "材料润色",
        "research_synthesis": "综合调研整合",
        "shenyinxie_news": "深银协动态",
        "internal_weekly": "内参周报内容核对稿",
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
    attachment_delivery = AttachmentDelivery(ops_event_logger=ops_event_logger)
    queued_attachment_delivery = AttachmentDelivery(
        config=AttachmentDeliveryConfig(max_attempts=1),
        ops_event_logger=ops_event_logger,
    )
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

    async def process_queued_writing(workspace: WritingTaskWorkspace) -> PlatformResult:
        return await asyncio.to_thread(
            platform_app.execute_prepared_job,
            workspace.prepared,
        )

    async def finalize_queued_writing(
        workspace: WritingTaskWorkspace,
        result: PlatformResult,
    ) -> None:
        if result.needs_clarification and not workspace.prepared.logical_task_id:
            route_inputs = workspace.prepared.route.inputs
            intake_store.restore_clarification(
                channel=workspace.prepared.channel,
                sender_userid=workspace.prepared.sender_userid,
                skill_id=result.skill_id or str(workspace.prepared.route.skill_id or ""),
                text=str(route_inputs.get("text", "") or ""),
                material_text=str(route_inputs.get("material_text", "") or ""),
                urls=tuple(
                    str(item)
                    for item in list(route_inputs.get("urls") or [])
                    if str(item).strip()
                ),
                files=tuple(
                    UploadedFile(
                        filename=Path(str(item)).name,
                        stored_path=str(item),
                    )
                    for item in list(route_inputs.get("files") or [])
                    if str(item).strip()
                ),
                message=result.message,
            )
        if ops_event_logger and _is_link_read_failure_result(result):
            _record_ops_event(
                ops_event_logger,
                severity="warning",
                subject="链接读取失败待用户确认",
                detail=result.message,
                sender_userid=workspace.prepared.sender_userid,
                sender_name=workspace.prepared.sender_name,
                skill_id=result.skill_id or "",
            )

    async def finalize_failed_writing(workspace: WritingTaskWorkspace) -> None:
        platform_app.mark_prepared_task_status(workspace.prepared, TaskCardStatus.FAILED)

    async def send_queued_writing_attachment(
        recipient: str,
        path: Path,
        task_dir: Path,
    ) -> DeliveryOutcome:
        resolved_path = path.resolve(strict=True)
        output_root = (task_dir / "output").resolve(strict=True)
        if not resolved_path.is_file() or not resolved_path.is_relative_to(output_root):
            raise ValueError("写作结果附件不属于当前任务输出目录")
        result = await queued_attachment_delivery.deliver(
            ws_client=ws_client,
            request=DeliveryRequest(
                file_path=resolved_path,
                allowed_root=task_dir,
                frame=None,
                chat_id=recipient,
                task_dir=task_dir,
                source="writing_task_delivery",
                sender_userid=recipient,
                sender_name=platform_app.resolve_sender_name(recipient),
                skill_id="shenyinxie_news",
                job_id=task_dir.name,
                manage_task_status=False,
            ),
        )
        return result.to_outcome()

    async def notify_queued_writing_failure(
        recipient: str,
        error_code: str,
        task_id: str,
    ) -> None:
        messages = {
            "delivery_status_uncertain": (
                "初稿已经生成，但发送状态暂时无法确认。为避免重复发送，我已暂停自动重发并提醒管理员核对。"
            ),
            "delivery_failed": "初稿已经生成，但发送失败，已经提醒管理员处理。",
            "delivery_not_delivered": "初稿已经生成，但企业微信明确未接收，已经提醒管理员处理。",
            "writing_processing_failed": "写作处理失败，已经提醒管理员排查，请稍后重试。",
            "writing_finalization_failed": "初稿处理状态异常，已经提醒管理员排查。",
            "invalid_task_payload": "写作任务状态异常，已经提醒管理员排查。",
        }
        message = messages.get(error_code)
        if message is None and error_code.startswith("model_"):
            message = "模型服务暂时不可用，任务已经安全停止并提醒管理员排查。"
        if message:
            await _send_active_writing_text(
                ws_client,
                recipient,
                f"{message}处理编号：{task_id}",
            )

    task_repository = TaskRepository(
        config.task_queue_db_path,
        on_transition=TaskLifecycleObserver(
            task_root=config.jobs_dir,
            ops_event_logger=ops_event_logger,
        ),
    )
    writing_task_service = WritingTaskService(
        repository=task_repository,
        workspace_root=config.jobs_dir,
        text_preparer=platform_app.prepare_text_message,
        structured_preparer=platform_app.prepare_structured_request,
        processor=process_queued_writing,
        text_sender=lambda recipient, text: _send_active_writing_text(
            ws_client,
            recipient,
            text,
        ),
        attachment_sender=send_queued_writing_attachment,
        result_finalizer=finalize_queued_writing,
        failure_finalizer=finalize_failed_writing,
        failure_notifier=notify_queued_writing_failure,
        task_state_observer=lambda prepared, status, execution_task_id: (
            platform_app.mark_prepared_task_status(
                prepared,
                TaskCardStatus(status),
                execution_task_id=execution_task_id,
            )
        ),
    )
    task_executor = PersistentTaskExecutor(
        repository=task_repository,
        limits=ClaimLimits(
            global_limit=config.task_worker_count,
            per_user_limit=1,
            cost_class_limits={WRITING_COST_CLASS: config.task_worker_count},
        ),
        worker_id=f"writing-bot-{uuid4().hex[:12]}",
        lease_duration=timedelta(seconds=config.task_lease_seconds),
    )
    for task_type in WRITING_TASK_TYPES:
        task_executor.register_handler(task_type, writing_task_service.handle)

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
            attachment_delivery=attachment_delivery,
            task_service=writing_task_service,
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
            task_service=writing_task_service,
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
    task_stop_event = asyncio.Event()
    task_worker = asyncio.create_task(
        _run_writing_task_worker_supervised(
            task_executor=task_executor,
            stop_event=task_stop_event,
            poll_interval=config.task_poll_seconds,
            worker_count=config.task_worker_count,
            recovery_interval=config.task_recovery_seconds,
            ops_event_logger=ops_event_logger,
        ),
        name="writing-persistent-task-worker",
    )
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        await ws_client.disconnect()
    finally:
        task_stop_event.set()
        await asyncio.gather(task_worker, return_exceptions=True)
        if heartbeat_task:
            heartbeat_task.cancel()
        portal_server.shutdown()
        portal_server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="直报写作 Bot")
    parser.add_argument("--check-config", action="store_true", help="检查配置")
    args = parser.parse_args(argv)

    try:
        config = load_config()
        validate_bot_startup(
            RuntimeEnvironment(
                mode=config.runtime_mode,
                data_root=config.data_root or config.jobs_dir.parent,
                values={},
            ),
            data_paths=(
                config.jobs_dir,
                config.policy_db_path,
                config.bank_db_path,
                config.conversation_dir,
                config.intake_dir,
                config.chat_log_dir,
                config.ops_events_dir,
                config.ops_heartbeat_dir,
                config.user_registry_path,
                config.task_queue_db_path,
                config.task_relation_db_path,
            ),
            project_root=ROOT,
        )
    except RuntimeEnvironmentError as exc:
        print(f"错误：{exc}")
        return 2

    if not config.wecom_bot_id or not config.wecom_bot_secret:
        print("错误：缺少 WRITING_BOT_ID 或 WRITING_BOT_SECRET 配置")
        return 2

    if args.check_config:
        platform_config = build_platform_config(config)
        print("配置检查通过。")
        print(f"运行环境: {config.runtime_mode}")
        print(f"数据根目录: {config.data_root}")
        print(f"Bot ID: {mask_config_value(config.wecom_bot_id)}")
        print(f"模型: {config.model_name}")
        print(f"模型输出上限: {config.model_max_tokens}")
        print(f"模型调用超时: {config.model_timeout_seconds:g} 秒")
        print(f"模型单次阶段最多调用: {config.model_max_attempts} 次")
        print(f"直报 critic 模式: {config.direct_report_critic_mode}")
        print(f"Skills 目录: {config.skills_dir}")
        print(f"任务目录: {config.jobs_dir}")
        print(f"对话日志: {'开启' if platform_config.chat_log_enabled else '关闭'}")
        print(f"对话日志目录: {platform_config.chat_log_dir or platform_config.jobs_dir.parent / 'chat_logs'}")
        print(f"用户名称表: {platform_config.user_registry_path or '未配置'}")
        print(f"素材入口: {config.portal_base_url}")
        print(f"多消息任务暂存: {config.intake_ttl_seconds} 秒")
        print(f"写作持久队列: {config.task_queue_db_path}")
        print(f"任务关系库: {config.task_relation_db_path}")
        print(f"写作 worker: {config.task_worker_count} 个")
        print(f"权限配置: {config.access_policy_path or '未配置，本地开发默认允许已启用 skill'}")
        return 0

    print("正在连接企业微信写作 Bot...", flush=True)
    asyncio.run(run_bot(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
