from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import shutil
import sqlite3
from uuid import uuid4

from app.platform.builtin_tools import (
    bank_materials,
    bank_search,
    policy_materials,
    policy_research,
    policy_search,
    read_document_file,
    read_pdf_file,
    read_web_page,
    read_word_file,
    search_web,
)
from app.platform.config import PlatformConfig
from app.platform.chat_log import ChatLogStore
from app.platform.conversation import ConversationStore
from app.platform.identity import AccessPolicy
from app.platform.intent import ConversationIntent, classify_conversation_intent, select_draft_version
from app.platform.models import PlatformResult, RoutedRequest, UploadedFile
from app.platform.model_reliability import ModelCallPolicy
from app.platform.pydantic_runtime import PydanticAIWriter
from app.platform.registry import SkillRegistry
from app.platform.router import URL_RE, route_message
from app.platform.runtime import PlatformRuntime
from app.platform.storage import JobContext, JobStore
from app.platform.task_status import update_task_status
from app.platform.task_relations import (
    MaterialRole,
    PydanticTaskRelationClassifier,
    RelationAction,
    TaskCard,
    TaskCardStatus,
    TaskRelation,
    TaskRelationDecision,
    TaskRelationRepository,
    TaskRelationService,
)
from app.platform.user_registry import UserRegistry


RELATION_MODEL_TIMEOUT_SECONDS = 15.0
RELATION_MODEL_MAX_TOKENS = 1024

SUPPORTED_UPLOAD_SUFFIXES = {".docx", ".pdf", ".pptx"}


@dataclass(frozen=True)
class PreparedPlatformJob:
    channel: str
    sender_userid: str
    sender_name: str
    route: RoutedRequest
    job: JobContext
    user_text: str
    ack_message: str = ""
    logical_task_id: str = ""
    task_relation: str = TaskRelation.NEW_TASK.value
    parent_task_id: str = ""


class PlatformApp:
    def __init__(
        self,
        *,
        registry: SkillRegistry,
        tools: dict[str, Callable[..., object]],
        job_store: JobStore,
        access_policy: AccessPolicy,
        conversation_store: ConversationStore | None = None,
        chat_log_store: ChatLogStore | None = None,
        user_registry: UserRegistry | None = None,
        task_relation_service: TaskRelationService | None = None,
        direct_report_critic_mode: str = "advisory",
    ):
        self._registry = registry
        self._runtime = PlatformRuntime(registry=registry, tools=tools)
        self._job_store = job_store
        self._conversation_store = conversation_store
        self._chat_log_store = chat_log_store
        self._user_registry = user_registry
        self._task_relation_service = task_relation_service
        self._access_policy = access_policy
        self._direct_report_critic_mode = direct_report_critic_mode

    @classmethod
    def from_config(cls, config: PlatformConfig) -> "PlatformApp":
        registry = SkillRegistry.from_directory(
            config.skills_dir,
            include_skill_ids=config.skill_allowlist,
        )
        writer = PydanticAIWriter(
            api_key=config.anthropic_api_key,
            base_url=config.anthropic_base_url,
            model_name=config.model_name,
            skill_dir=config.skills_dir,
            model_max_tokens=config.model_max_tokens,
            model_timeout_seconds=config.model_timeout_seconds,
            model_max_attempts=config.model_max_attempts,
            model_retry_backoff_seconds=config.model_retry_backoff_seconds,
        )
        relation_writer = PydanticAIWriter(
            api_key=config.anthropic_api_key,
            base_url=config.anthropic_base_url,
            model_name=config.model_name,
            skill_dir=config.skills_dir,
            model_max_tokens=min(config.model_max_tokens, RELATION_MODEL_MAX_TOKENS),
            model_timeout_seconds=min(
                config.model_timeout_seconds,
                RELATION_MODEL_TIMEOUT_SECONDS,
            ),
            model_max_attempts=1,
            model_retry_backoff_seconds=0,
        )
        tools = build_platform_tools(config, writer=writer)
        enabled_skill_ids = [skill.id for skill in registry.list_enabled()]
        if config.access_policy_path and config.access_policy_path.exists():
            access_policy = AccessPolicy.from_file(config.access_policy_path)
        else:
            access_policy = AccessPolicy.allow_all_for_skills(enabled_skill_ids)

        return cls(
            registry=registry,
            tools=tools,
            job_store=JobStore(config.jobs_dir),
            conversation_store=ConversationStore(
                config.conversation_dir or (config.jobs_dir.parent / "conversations")
            ),
            chat_log_store=ChatLogStore(
                config.chat_log_dir or (config.jobs_dir.parent / "chat_logs"),
                enabled=config.chat_log_enabled,
            ),
            user_registry=UserRegistry(config.user_registry_path) if config.user_registry_path else None,
            task_relation_service=TaskRelationService(
                TaskRelationRepository(
                    config.task_relation_db_path
                    or (
                        (config.conversation_dir or config.jobs_dir.parent / "conversations").parent
                        / "task-relations"
                        / "task-relations.sqlite3"
                    )
                ),
                semantic_classifier=PydanticTaskRelationClassifier(
                    relation_writer.run_structured
                ),
            ),
            access_policy=access_policy,
            direct_report_critic_mode=config.direct_report_critic_mode,
        )

    @property
    def task_relation_service(self) -> TaskRelationService | None:
        return self._task_relation_service

    def resolve_sender_name(self, sender_userid: str) -> str:
        """把企业微信 userid 转为可读用户名, 未登记时回退到 userid."""
        if not self._user_registry:
            return sender_userid
        return self._user_registry.get_name(sender_userid) or sender_userid

    def handle_text_message(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
        ack_message: str = "",
    ) -> PlatformResult:
        prepared = self.prepare_text_message(
            channel=channel,
            sender_userid=sender_userid,
            text=text,
            ack_message=ack_message,
        )
        return self.execute_prepared_job(prepared)

    def prepare_text_message(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
        ack_message: str = "",
        sender_name: str | None = None,
    ) -> PreparedPlatformJob:
        resolved_sender_name = sender_name or self.resolve_sender_name(sender_userid)
        job = self._job_store.create_job(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=resolved_sender_name,
            message=text,
            processing_status="queued",
        )
        original_route = route_message(text, self._registry)
        if self._task_relation_service:
            relation = self.resolve_task_relation(
                channel=channel,
                sender_userid=sender_userid,
                text=text,
                route_skill_id=original_route.skill_id,
                has_new_material=bool(URL_RE.search(text)),
                persist=True,
            )
            route = self._route_for_task_relation(
                original_route=original_route,
                relation=relation,
                channel=channel,
                sender_userid=sender_userid,
                text=text,
                job=job,
            )
            logical_task_id, parent_task_id = self._bind_prepared_relation(
                relation=relation,
                route=route,
                job=job,
                channel=channel,
                sender_userid=sender_userid,
                text=text,
            )
        else:
            route = self._revision_route_or_original(
                original_route=original_route,
                channel=channel,
                sender_userid=sender_userid,
                text=text,
            )
            relation = TaskRelationDecision(
                relation=(
                    TaskRelation.CONTINUE
                    if route.inputs.get("revision")
                    else TaskRelation.NEW_TASK
                )
            )
            logical_task_id = ""
            parent_task_id = ""
        return PreparedPlatformJob(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=resolved_sender_name,
            route=route,
            job=job,
            user_text=text,
            ack_message=ack_message,
            logical_task_id=logical_task_id,
            task_relation=relation.relation.value,
            parent_task_id=parent_task_id,
        )

    def classify_text_intent(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
    ) -> ConversationIntent:
        route = route_message(text, self._registry)
        if self._task_relation_service:
            relation = self.resolve_task_relation(
                channel=channel,
                sender_userid=sender_userid,
                text=text,
                route_skill_id=route.skill_id,
                has_new_material=bool(URL_RE.search(text)),
                persist=False,
            )
            if relation.relation in {
                TaskRelation.CONTINUE,
                TaskRelation.ADD_MATERIAL,
                TaskRelation.ANSWER_CLARIFICATION,
            }:
                return ConversationIntent.REVISE_PREVIOUS
            if relation.relation in {TaskRelation.NEW_TASK, TaskRelation.DERIVE}:
                return ConversationIntent.NEW_TASK
            return ConversationIntent.CLARIFY
        return self._classify_route_intent(
            route=route,
            channel=channel,
            sender_userid=sender_userid,
            text=text,
        )

    def preview_text_route(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
    ) -> RoutedRequest:
        route = route_message(text, self._registry)
        if self._task_relation_service:
            relation = self.resolve_task_relation(
                channel=channel,
                sender_userid=sender_userid,
                text=text,
                route_skill_id=route.skill_id,
                has_new_material=bool(URL_RE.search(text)),
                persist=False,
            )
            return self._route_for_task_relation(
                original_route=route,
                relation=relation,
                channel=channel,
                sender_userid=sender_userid,
                text=text,
                job=None,
            )
        return self._revision_route_or_original(
            original_route=route,
            channel=channel,
            sender_userid=sender_userid,
            text=text,
        )

    def resolve_task_relation(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
        route_skill_id: str | None = None,
        has_new_material: bool = False,
        persist: bool = True,
    ) -> TaskRelationDecision:
        if self._task_relation_service:
            return self._task_relation_service.resolve_text(
                channel=channel,
                user_id=sender_userid,
                text=text,
                route_skill_id=route_skill_id,
                has_new_material=has_new_material,
                persist=persist,
            )
        return TaskRelationDecision(
            relation=TaskRelation.NEW_TASK,
            material_role=MaterialRole.NEW_TASK if has_new_material else MaterialRole.NONE,
            suggested_skill_id=route_skill_id or "",
            action=RelationAction.EXECUTE,
            effective_text=text.strip(),
        )

    def handle_structured_request(
        self,
        *,
        channel: str,
        sender_userid: str,
        skill_id: str,
        text: str = "",
        material_text: str = "",
        urls: list[str] | None = None,
        files: list[UploadedFile] | None = None,
        task_relation: str = "",
        target_task_id: str = "",
        parent_task_id: str = "",
        material_role: str = "",
    ) -> PlatformResult:
        clean_urls = [str(url).strip() for url in list(urls or []) if str(url).strip()]
        uploaded_files = list(files or [])
        _validate_uploaded_files(uploaded_files)
        routed_skill_id = _resolve_structured_skill_id(
            requested_skill_id=skill_id,
            urls=clean_urls,
            files=[item.filename for item in uploaded_files],
            material_text=material_text,
        )
        if routed_skill_id and not self._access_policy.can_use_skill(sender_userid, routed_skill_id):
            return PlatformResult(
                skill_id=routed_skill_id,
                output={},
                needs_clarification=False,
                message="你没有使用该能力的权限。",
            )
        prepared = self.prepare_structured_request(
            channel=channel,
            sender_userid=sender_userid,
            skill_id=skill_id,
            text=text,
            material_text=material_text,
            urls=clean_urls,
            files=uploaded_files,
            task_relation=task_relation,
            target_task_id=target_task_id,
            parent_task_id=parent_task_id,
            material_role=material_role,
        )
        result: PlatformResult | None = None
        try:
            result = self.execute_prepared_job(prepared)
            return result
        finally:
            if result is None or not result.needs_clarification:
                for item in uploaded_files:
                    _cleanup_uploaded_file(item)

    def prepare_structured_request(
        self,
        *,
        channel: str,
        sender_userid: str,
        skill_id: str,
        text: str = "",
        material_text: str = "",
        urls: list[str] | None = None,
        files: list[UploadedFile] | None = None,
        sender_name: str | None = None,
        task_relation: str = "",
        target_task_id: str = "",
        parent_task_id: str = "",
        material_role: str = "",
    ) -> PreparedPlatformJob:
        clean_urls = [str(url).strip() for url in list(urls or []) if str(url).strip()]
        uploaded_files = list(files or [])
        _validate_uploaded_files(uploaded_files)
        resolved_sender_name = sender_name or self.resolve_sender_name(sender_userid)
        routed_skill_id = _resolve_structured_skill_id(
            requested_skill_id=skill_id,
            urls=clean_urls,
            files=[item.filename for item in uploaded_files],
            material_text=material_text,
        )
        if routed_skill_id and not self._access_policy.can_use_skill(sender_userid, routed_skill_id):
            raise PermissionError("你没有使用该能力的权限。")
        preview_parts = [skill_id, text.strip(), material_text.strip(), *clean_urls, *[item.filename for item in uploaded_files]]
        job = self._job_store.create_job(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=resolved_sender_name,
            message="\n".join(part for part in preview_parts if part),
            processing_status="queued",
        )
        saved_files = _save_uploaded_files(job.input_dir, uploaded_files)
        base_route = RoutedRequest(
            skill_id=routed_skill_id,
            confidence=1.0,
            needs_clarification=False,
            message=f"已识别为{routed_skill_id}。",
            inputs={
                "text": text.strip(),
                "material_text": material_text.strip(),
                "urls": clean_urls,
                "files": saved_files,
            },
        )
        relation_kind = _parse_task_relation(task_relation)
        role = _parse_material_role(material_role, relation=relation_kind)
        relation = TaskRelationDecision(
            relation=relation_kind,
            target_task_id=target_task_id.strip(),
            parent_task_id=parent_task_id.strip(),
            material_role=role,
            suggested_skill_id=routed_skill_id,
            action=RelationAction.EXECUTE,
            effective_text=text.strip(),
        )
        route = base_route
        if self._task_relation_service and relation_kind is TaskRelation.ADD_MATERIAL:
            route = self._route_for_task_relation(
                original_route=base_route,
                relation=relation,
                channel=channel,
                sender_userid=sender_userid,
                text=text,
                job=job,
            )
        elif self._task_relation_service and relation_kind is TaskRelation.DERIVE:
            source_task_id = parent_task_id.strip() or target_task_id.strip()
            relation = replace(
                relation,
                target_task_id=source_task_id,
                parent_task_id=source_task_id,
            )
            route = self._route_for_task_relation(
                original_route=base_route,
                relation=relation,
                channel=channel,
                sender_userid=sender_userid,
                text=text,
                job=job,
            )
        logical_task_id, effective_parent_task_id = self._bind_prepared_relation(
            relation=relation,
            route=route,
            job=job,
            channel=channel,
            sender_userid=sender_userid,
            text=text or material_text,
        )
        return PreparedPlatformJob(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=resolved_sender_name,
            route=route,
            job=job,
            user_text="\n".join(part for part in preview_parts if part),
            ack_message="",
            logical_task_id=logical_task_id,
            task_relation=relation.relation.value,
            parent_task_id=effective_parent_task_id,
        )

    def execute_prepared_job(self, prepared: PreparedPlatformJob) -> PlatformResult:
        marker_path = prepared.job.work_dir / "platform-execution.json"
        marker = _read_json_file(marker_path)
        if marker.get("status") == "completed":
            return self._job_store.read_result(prepared.job)

        update_task_status(
            prepared.job.job_dir,
            processing_status="processing",
            source="platform_runtime",
        )
        result = self._run_routed_job(
            channel=prepared.channel,
            sender_userid=prepared.sender_userid,
            sender_name=prepared.sender_name,
            route=prepared.route,
            job=prepared.job,
            user_text=prepared.user_text,
            ack_message=prepared.ack_message,
            logical_task_id=prepared.logical_task_id,
            task_relation=prepared.task_relation,
        )
        _write_json_atomic(
            marker_path,
            {
                "schema_version": 1,
                "status": "completed",
                "job_id": prepared.job.job_id,
            },
        )
        return result

    def _run_routed_job(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        route: RoutedRequest,
        job,
        user_text: str,
        ack_message: str,
        logical_task_id: str = "",
        task_relation: str = TaskRelation.NEW_TASK.value,
    ) -> PlatformResult:
        if route.skill_id and not self._access_policy.can_use_skill(sender_userid, route.skill_id):
            result = PlatformResult(
                skill_id=route.skill_id,
                output={},
                needs_clarification=False,
                message="你没有使用该能力的权限。",
            )
            self._job_store.write_result(job, result)
            self._record_chat_log(
                channel=channel,
                sender_userid=sender_userid,
                sender_name=sender_name,
                job_id=job.job_id,
                user_text=user_text,
                ack_message=ack_message,
                final_reply=result.message,
                route=route,
                result=result,
                error=None,
            )
            return result

        route_with_context = replace(
            route,
            inputs={
                **route.inputs,
                "job_id": job.job_id,
                "job_dir": str(job.job_dir),
                "input_dir": str(job.input_dir),
                "work_dir": str(job.work_dir),
                "output_dir": str(job.output_dir),
                "channel": channel,
                "sender_userid": sender_userid,
                "direct_report_critic_mode": self._direct_report_critic_mode,
            },
        )
        print(
            f"平台任务开始: job_id={job.job_id} skill={route.skill_id or 'clarify'} user={sender_name}|userid={sender_userid}",
            flush=True,
        )
        error_text = None
        try:
            result = self._runtime.run(route_with_context)
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            result = PlatformResult(
                skill_id=route.skill_id,
                output={},
                needs_clarification=False,
                message="处理失败，请稍后重试。",
            )
            self._job_store.write_result(job, result)
            self._record_chat_log(
                channel=channel,
                sender_userid=sender_userid,
                sender_name=sender_name,
                job_id=job.job_id,
                user_text=user_text,
                ack_message=ack_message,
                final_reply=result.message,
                route=route,
                result=result,
                error=error_text,
            )
            self._set_task_card_status(logical_task_id, TaskCardStatus.FAILED)
            raise
        self._job_store.write_result(job, result)
        self._record_task_card_result(
            logical_task_id=logical_task_id,
            job_id=job.job_id,
            route=route,
            result=result,
        )
        if self._conversation_store:
            self._conversation_store.record_result(
                channel=channel,
                sender_userid=sender_userid,
                sender_name=sender_name,
                job_id=job.job_id,
                result=result,
                revision_request=str(route.inputs.get("revision_request", "") or "")
                if route.inputs.get("revision")
                else "",
                previous_job_id=str(route.inputs.get("previous_job_id", "") or ""),
            )
        self._record_chat_log(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            job_id=job.job_id,
            user_text=user_text,
            ack_message=ack_message,
            final_reply=_format_result_for_log(result),
            route=route,
            result=result,
            error=error_text,
        )
        print(
            f"平台任务完成: job_id={job.job_id} user={sender_name}|userid={sender_userid} skill={result.skill_id or 'none'} clarification={result.needs_clarification}",
            flush=True,
        )
        return result

    def _record_chat_log(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str,
        job_id: str,
        user_text: str,
        ack_message: str,
        final_reply: str,
        route: RoutedRequest,
        result: PlatformResult,
        error: str | None,
    ) -> None:
        if not self._chat_log_store:
            return
        try:
            intent = self._classify_route_intent(
                route=route,
                channel=channel,
                sender_userid=sender_userid,
                text=user_text,
            )
            draft_version = self._current_draft_version(channel=channel, sender_userid=sender_userid)
            self._chat_log_store.record_turn(
                channel=channel,
                sender_userid=sender_userid,
                sender_name=sender_name,
                job_id=job_id,
                user_text=user_text,
                ack_message=ack_message,
                final_reply=final_reply,
                intent=intent,
                route_skill_id=route.skill_id,
                result=result,
                draft_version=draft_version,
                previous_job_id=str(route.inputs.get("previous_job_id", "") or ""),
                error=error,
            )
        except Exception as exc:
            print(f"写作对话日志记录失败:{type(exc).__name__}: {exc}", flush=True)

    def mark_prepared_task_status(
        self,
        prepared: PreparedPlatformJob,
        status: TaskCardStatus,
        *,
        execution_task_id: str = "",
    ) -> None:
        if not prepared.logical_task_id or not self._task_relation_service:
            return
        try:
            self._task_relation_service.repository.set_status(
                prepared.logical_task_id,
                status,
                execution_task_id=execution_task_id,
            )
        except (KeyError, OSError, sqlite3.Error):
            return

    def _route_for_task_relation(
        self,
        *,
        original_route: RoutedRequest,
        relation: TaskRelationDecision,
        channel: str,
        sender_userid: str,
        text: str,
        job: JobContext | None,
    ) -> RoutedRequest:
        if not self._task_relation_service:
            if relation.relation is TaskRelation.NEW_TASK:
                return original_route
            return self._revision_route_or_original(
                original_route=original_route,
                channel=channel,
                sender_userid=sender_userid,
                text=text,
            )

        if relation.action is RelationAction.ASK:
            return RoutedRequest(
                skill_id=None,
                confidence=relation.confidence,
                needs_clarification=True,
                message=relation.question,
                inputs={
                    "task_relation": relation.relation.value,
                    "relation_reason": relation.reason,
                },
            )
        if relation.relation is TaskRelation.SWITCH:
            card = self._relation_card(relation.target_task_id, channel, sender_userid)
            return RoutedRequest(
                skill_id=None,
                confidence=1.0,
                needs_clarification=True,
                message=(
                    f"已切换到《{card.title}》。你可以继续提出修改要求，"
                    "也可以补充新材料。"
                ),
                inputs={"task_relation": relation.relation.value, "target_task_id": card.task_id},
            )
        if relation.relation is TaskRelation.CANCEL:
            card = self._relation_card(relation.target_task_id, channel, sender_userid)
            if job is not None and card.status not in {TaskCardStatus.QUEUED, TaskCardStatus.RUNNING}:
                self._task_relation_service.repository.set_status(
                    card.task_id,
                    TaskCardStatus.CANCELLED,
                )
                message = f"已结束《{card.title}》的后续处理。"
            else:
                message = f"《{card.title}》已经进入后台处理，暂时不能直接取消。"
            return RoutedRequest(
                skill_id=None,
                confidence=1.0,
                needs_clarification=True,
                message=message,
                inputs={"task_relation": relation.relation.value, "target_task_id": card.task_id},
            )
        if relation.relation in {
            TaskRelation.CONTINUE,
            TaskRelation.ADD_MATERIAL,
            TaskRelation.ANSWER_CLARIFICATION,
        }:
            card = self._relation_card(relation.target_task_id, channel, sender_userid)
            if relation.relation is TaskRelation.ANSWER_CLARIFICATION and card.pending_question:
                return self._resume_pending_task_route(
                    card=card,
                    answer=relation.effective_text or text,
                    job=job,
                )
            if card.status in {TaskCardStatus.QUEUED, TaskCardStatus.RUNNING}:
                return RoutedRequest(
                    skill_id=None,
                    confidence=1.0,
                    needs_clarification=True,
                    message=(
                        f"《{card.title}》还在生成中。等当前版本完成后，"
                        "我再按这项要求继续修改。"
                    ),
                    inputs={
                        "task_relation": relation.relation.value,
                        "target_task_id": card.task_id,
                    },
                )
            return self._revision_route_from_card(
                card=card,
                original_route=original_route,
                text=relation.effective_text or text,
                material_relation=relation.relation is TaskRelation.ADD_MATERIAL,
                material_role=relation.material_role,
            )
        if relation.relation is TaskRelation.DERIVE and relation.target_task_id:
            card = self._relation_card(relation.target_task_id, channel, sender_userid)
            reference = self._task_revision_target(card)
            inputs = dict(original_route.inputs)
            if reference:
                structure = _extract_structure(str(reference.get("body", "")))
                if structure:
                    instruction = str(inputs.get("text", text) or text).strip()
                    inputs["text"] = (
                        f"{instruction}\n\n仅沿用旧任务的结构层级，不自动沿用旧稿事实：\n{structure}"
                    ).strip()
            inputs.update(
                {
                    "task_relation": TaskRelation.DERIVE.value,
                    "parent_task_id": card.task_id,
                    "parent_job_id": card.current_job_id,
                }
            )
            return replace(original_route, inputs=inputs)
        return replace(
            original_route,
            inputs={**original_route.inputs, "task_relation": TaskRelation.NEW_TASK.value},
        )

    def _bind_prepared_relation(
        self,
        *,
        relation: TaskRelationDecision,
        route: RoutedRequest,
        job: JobContext,
        channel: str,
        sender_userid: str,
        text: str,
    ) -> tuple[str, str]:
        if (
            not self._task_relation_service
            or not route.skill_id
            or route.needs_clarification
        ):
            return "", ""
        repository = self._task_relation_service.repository
        materials = _task_materials_from_inputs(route.inputs, relation.material_role)
        if relation.target_task_id and relation.relation in {
            TaskRelation.CONTINUE,
            TaskRelation.ADD_MATERIAL,
            TaskRelation.ANSWER_CLARIFICATION,
        }:
            repository.bind_job(
                task_id=relation.target_task_id,
                job_id=job.job_id,
                relation=relation.relation,
                status=TaskCardStatus.QUEUED,
                materials=materials,
            )
            return relation.target_task_id, ""

        logical_task_id = job.job_id
        parent_task_id = relation.parent_task_id or (
            relation.target_task_id if relation.relation is TaskRelation.DERIVE else ""
        )
        repository.create_task(
            task_id=logical_task_id,
            channel=channel,
            user_id=sender_userid,
            skill_id=route.skill_id,
            title=_initial_task_title(text=text, skill_id=route.skill_id),
            status=TaskCardStatus.QUEUED,
            current_job_id=job.job_id,
            parent_task_id=parent_task_id,
            materials=materials,
        )
        return logical_task_id, parent_task_id

    def _relation_card(self, task_id: str, channel: str, sender_userid: str) -> TaskCard:
        if not self._task_relation_service:
            raise KeyError(task_id)
        return self._task_relation_service.repository.get_task(
            task_id,
            channel=channel,
            user_id=sender_userid,
        )

    def _revision_route_from_card(
        self,
        *,
        card: TaskCard,
        original_route: RoutedRequest,
        text: str,
        material_relation: bool,
        material_role: MaterialRole = MaterialRole.NONE,
    ) -> RoutedRequest:
        selected_version = select_draft_version(text, current_version=card.current_version)
        previous = self._task_revision_target(card, version=selected_version)
        if previous is None or not previous.get("skill_id"):
            return original_route
        try:
            skill = self._registry.get(str(previous["skill_id"]))
        except KeyError:
            return original_route
        if not skill.enabled or not skill.supports_revision:
            return original_route
        title = str(previous.get("title", "")).strip()
        body = str(previous.get("body", "")).strip()
        if not (title or body):
            return original_route
        extra_inputs = dict(original_route.inputs) if material_relation else {}
        revision_text = text.strip()
        if material_relation:
            revision_text = _material_revision_instruction(
                revision_text,
                role=material_role,
            )
        return RoutedRequest(
            skill_id=str(previous["skill_id"]),
            confidence=0.95,
            needs_clarification=False,
            message=f"已定位《{card.title}》并继续处理。",
            inputs={
                **extra_inputs,
                "text": _build_revision_instruction(revision_text),
                "revision": True,
                "supplement_materials": material_relation,
                "revision_request": text.strip(),
                "previous_job_id": str(previous["job_id"]),
                "previous_title": title,
                "previous_body": body,
                "previous_sources": list(previous["sources"]),
                "urls": list(extra_inputs.get("urls") or []),
                "files": list(extra_inputs.get("files") or []),
                "material_text": str(extra_inputs.get("material_text", "") or ""),
                "material_role": material_role.value,
                "task_relation": (
                    TaskRelation.ADD_MATERIAL.value
                    if material_relation
                    else TaskRelation.CONTINUE.value
                ),
                "target_task_id": card.task_id,
            },
        )

    def _resume_pending_task_route(
        self,
        *,
        card: TaskCard,
        answer: str,
        job: JobContext | None,
    ) -> RoutedRequest:
        context = dict(card.resume_context)
        skill_id = str(context.pop("skill_id", card.skill_id) or card.skill_id)
        previous_text = str(context.get("text", "") or "").strip()
        context["text"] = "\n".join(item for item in (previous_text, answer.strip()) if item).strip()
        if job is not None:
            context["files"] = self._copy_resume_files(
                list(context.get("files") or []),
                target_job=job,
            )
        context.update(
            {
                "task_relation": TaskRelation.ANSWER_CLARIFICATION.value,
                "target_task_id": card.task_id,
            }
        )
        return RoutedRequest(
            skill_id=skill_id,
            confidence=1.0,
            needs_clarification=False,
            message=f"已收到对《{card.title}》的补充说明。",
            inputs=context,
        )

    def _copy_resume_files(self, files: list[object], *, target_job: JobContext) -> list[str]:
        copied: list[str] = []
        root = self._job_store.root_dir.resolve(strict=False)
        for raw in files:
            try:
                source = Path(str(raw)).resolve(strict=True)
            except OSError:
                continue
            if not source.is_file() or not source.is_relative_to(root):
                continue
            target = target_job.input_dir / f"{uuid4().hex[:8]}-{source.name}"
            shutil.copy2(source, target)
            copied.append(str(target))
        return copied

    def _task_revision_target(
        self,
        card: TaskCard,
        *,
        version: int | None = None,
    ) -> dict[str, object] | None:
        if not self._task_relation_service:
            return None
        job_id = self._task_relation_service.repository.version_job_id(card.task_id, version)
        if not job_id:
            return None
        previous = self._job_store.find_result_by_job_id(
            job_id,
            sender_userid=card.user_id,
            channel=card.channel,
        )
        if previous is None or not previous.skill_id:
            return None
        return {
            "skill_id": previous.skill_id,
            "job_id": previous.job_id,
            "title": str(previous.output.get("title", "") or ""),
            "body": str(previous.output.get("body", "") or ""),
            "sources": [
                str(item).strip()
                for item in list(previous.output.get("sources") or [])
                if str(item).strip()
            ],
        }

    def _record_task_card_result(
        self,
        *,
        logical_task_id: str,
        job_id: str,
        route: RoutedRequest,
        result: PlatformResult,
    ) -> None:
        if not logical_task_id or not self._task_relation_service:
            return
        status = (
            TaskCardStatus.NEEDS_INPUT
            if result.needs_clarification
            else TaskCardStatus.COMPLETED
        )
        resume_context = _resume_context(route) if result.needs_clarification else {}
        try:
            self._task_relation_service.repository.record_result(
                task_id=logical_task_id,
                job_id=job_id,
                title=str(result.output.get("title", "") or ""),
                body=str(result.output.get("body", "") or ""),
                status=status,
                pending_question=result.message if result.needs_clarification else "",
                resume_context=resume_context,
            )
        except (KeyError, OSError, sqlite3.Error):
            return

    def _set_task_card_status(self, logical_task_id: str, status: TaskCardStatus) -> None:
        if not logical_task_id or not self._task_relation_service:
            return
        try:
            self._task_relation_service.repository.set_status(logical_task_id, status)
        except (KeyError, OSError, sqlite3.Error):
            return

    def _revision_route_or_original(
        self,
        *,
        original_route: RoutedRequest,
        channel: str,
        sender_userid: str,
        text: str,
    ) -> RoutedRequest:
        intent = self._classify_route_intent(
            route=original_route,
            channel=channel,
            sender_userid=sender_userid,
            text=text,
        )
        if intent != ConversationIntent.REVISE_PREVIOUS:
            return original_route

        conversation = None
        if self._conversation_store:
            conversation = self._conversation_store.get_active_conversation(
                channel=channel,
                sender_userid=sender_userid,
            )
        selected_version = None
        if conversation:
            selected_version = select_draft_version(
                text,
                current_version=conversation.current_draft.version,
            )
        previous = self._latest_revision_target(
            channel=channel,
            sender_userid=sender_userid,
            version=selected_version,
        )
        if previous is None or not previous["skill_id"]:
            return original_route

        try:
            skill = self._registry.get(str(previous["skill_id"]))
        except KeyError:
            return original_route
        if not skill.enabled or not skill.supports_revision:
            return original_route

        title = str(previous["title"]).strip()
        body = str(previous["body"]).strip()
        if not (title or body):
            return original_route

        return RoutedRequest(
            skill_id=str(previous["skill_id"]),
            confidence=0.8,
            needs_clarification=False,
            message=f"已识别为基于上一稿修改：{skill.name}。",
            inputs={
                "text": _build_revision_instruction(text),
                "revision": True,
                "revision_request": text.strip(),
                "previous_job_id": str(previous["job_id"]),
                "previous_title": title,
                "previous_body": body,
                "previous_sources": list(previous["sources"]),
                "urls": [],
                "files": [],
                "material_text": "",
            },
        )

    def _classify_route_intent(
        self,
        *,
        route: RoutedRequest,
        channel: str,
        sender_userid: str,
        text: str,
    ) -> ConversationIntent:
        has_active_conversation = self._latest_revision_target(
            channel=channel,
            sender_userid=sender_userid,
        ) is not None
        return classify_conversation_intent(
            text=text,
            has_active_conversation=has_active_conversation,
            route_skill_id=route.skill_id,
            route_needs_clarification=route.needs_clarification,
        )

    def _current_draft_version(self, *, channel: str, sender_userid: str) -> int | None:
        if self._task_relation_service:
            card = self._task_relation_service.repository.selected_task(
                channel=channel,
                user_id=sender_userid,
            )
            if card is not None and card.current_version > 0:
                return card.current_version
        if not self._conversation_store:
            return None
        conversation = self._conversation_store.get_active_conversation(
            channel=channel,
            sender_userid=sender_userid,
        )
        if not conversation:
            return None
        return conversation.current_draft.version

    def _latest_revision_target(
        self,
        *,
        channel: str,
        sender_userid: str,
        version: int | None = None,
    ) -> dict[str, object] | None:
        if self._conversation_store:
            conversation = self._conversation_store.get_active_conversation(
                channel=channel,
                sender_userid=sender_userid,
            )
            if conversation:
                draft = conversation.current_draft
                if version is not None:
                    draft = next(
                        (item for item in conversation.draft_versions if item.version == version),
                        conversation.current_draft,
                    )
                return {
                    "skill_id": conversation.active_skill_id,
                    "job_id": draft.job_id,
                    "title": draft.title,
                    "body": draft.body,
                    "sources": list(draft.sources),
                }

        previous = self._job_store.find_latest_result_for_user(
            sender_userid=sender_userid,
            channel=channel,
            successful_only=True,
        )
        if previous is None or not previous.skill_id:
            return None
        return {
            "skill_id": previous.skill_id,
            "job_id": previous.job_id,
            "title": str(previous.output.get("title", "")).strip(),
            "body": str(previous.output.get("body", "")).strip(),
            "sources": [
                str(item).strip()
                for item in list(previous.output.get("sources") or [])
                if str(item).strip()
            ],
        }


def build_platform_tools(
    config: PlatformConfig,
    *,
    writer: PydanticAIWriter | None = None,
) -> dict[str, Callable[..., object]]:
    writer = writer or PydanticAIWriter(
        api_key=config.anthropic_api_key,
        base_url=config.anthropic_base_url,
        model_name=config.model_name,
        skill_dir=config.skills_dir,
        model_max_tokens=config.model_max_tokens,
        model_timeout_seconds=config.model_timeout_seconds,
        model_max_attempts=config.model_max_attempts,
        model_retry_backoff_seconds=config.model_retry_backoff_seconds,
    )
    return {
        "web_reader": read_web_page,
        "search": lambda query, max_results=5: search_web(
            query,
            api_key=config.search_api_key or config.anthropic_api_key,
            base_url=config.search_api_base_url or config.anthropic_base_url,
            model_name=config.model_name,
            max_results=max_results,
            model_policy=ModelCallPolicy(
                timeout_seconds=min(config.model_timeout_seconds, 30),
                max_attempts=config.model_max_attempts,
                backoff_seconds=config.model_retry_backoff_seconds,
            ),
        ),
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
        "word_reader": read_word_file,
        "pdf_reader": read_pdf_file,
        "document_reader": lambda path, *, allowed_root, work_dir: read_document_file(
            path,
            allowed_root=allowed_root,
            work_dir=work_dir,
            max_file_bytes=config.document_max_bytes,
            ocr_scanned_pages=config.document_ocr_enabled,
        ),
        "llm_writer": writer.write,
    }


def _save_uploaded_files(input_dir: Path, files: list[UploadedFile]) -> list[str]:
    saved_paths: list[str] = []
    used_names: set[str] = set()
    for item in files:
        target_name = _unique_filename(_sanitize_filename(item.filename), used_names)
        target_path = input_dir / target_name
        target_path.write_bytes(item.read_bytes())
        used_names.add(target_name)
        saved_paths.append(str(target_path))
    return saved_paths


def _cleanup_uploaded_file(item: UploadedFile) -> None:
    if not item.delete_after_read or not item.stored_path:
        return
    source = Path(item.stored_path)
    source.unlink(missing_ok=True)
    for directory in (source.parent, source.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            break


def _build_revision_instruction(user_request: str) -> str:
    return (
        "请基于上一稿进行修改，不要把这次任务当作重新写作。\n"
        f"用户新的修改要求：{user_request.strip()}\n"
        "除非用户明确要求新增内容，否则保留上一稿中的事实、口径和来源，不编造新事实。"
    )


def _material_revision_instruction(user_request: str, *, role: MaterialRole) -> str:
    guidance = {
        MaterialRole.SUPPLEMENT: "把新材料作为事实补充；与上一稿不冲突的内容继续保留。",
        MaterialRole.REPLACE: "以新材料替换上一稿中对应的旧事实；冲突的旧内容不得继续保留。",
        MaterialRole.REFERENCE: "新材料只作参考；仅采用与修改要求直接相关且有依据的内容。",
    }.get(role, "结合新材料修改上一稿，并明确处理新旧材料之间的关系。")
    return f"{user_request}\n材料处理要求：{guidance}".strip()


def _initial_task_title(*, text: str, skill_id: str) -> str:
    labels = {
        "direct_report": "直报写作任务",
        "writer1": "简报写作任务",
        "writer2": "多素材简报任务",
        "rewrite": "材料润色任务",
        "research_synthesis": "综合调研整合任务",
        "shenyinxie_news": "深银协动态任务",
        "internal_weekly": "内参周报任务",
    }
    clean = URL_RE.sub("", " ".join(text.split())).strip(" ：:，,。")
    if clean:
        return clean[:80]
    return labels.get(skill_id, "内容处理任务")


def _task_materials_from_inputs(
    inputs: dict[str, object],
    default_role: MaterialRole,
) -> list[tuple[str, str, MaterialRole]]:
    role = default_role if default_role is not MaterialRole.NONE else MaterialRole.NEW_TASK
    materials: list[tuple[str, str, MaterialRole]] = []
    materials.extend(
        ("url", str(url).strip(), role)
        for url in list(inputs.get("urls") or [])
        if str(url).strip()
    )
    materials.extend(
        ("file", Path(str(path)).name, role)
        for path in list(inputs.get("files") or [])
        if str(path).strip()
    )
    if str(inputs.get("material_text", "") or "").strip():
        materials.append(("text", "用户文字素材", role))
    return materials


def _resume_context(route: RoutedRequest) -> dict[str, object]:
    allowed = {
        "skill_id": route.skill_id or "",
        "text": str(route.inputs.get("text", "") or ""),
        "material_text": str(route.inputs.get("material_text", "") or ""),
        "urls": [str(item) for item in list(route.inputs.get("urls") or [])],
        "files": [str(item) for item in list(route.inputs.get("files") or [])],
    }
    for key in (
        "revision",
        "supplement_materials",
        "revision_request",
        "previous_job_id",
        "previous_title",
        "previous_body",
        "previous_sources",
        "task_relation",
        "target_task_id",
        "parent_task_id",
        "material_role",
    ):
        if key in route.inputs:
            allowed[key] = route.inputs[key]
    return allowed


def _extract_structure(body: str) -> str:
    lines = [" ".join(line.split()) for line in body.splitlines() if line.strip()]
    headings = [
        line[:120]
        for line in lines
        if re.match(r"^(?:[一二三四五六七八九十]+、|\d+[.、]|（[一二三四五六七八九十]+）)", line)
    ]
    if headings:
        return "\n".join(headings[:12])
    return "\n".join(lines[:4])[:500]


def _parse_task_relation(value: str) -> TaskRelation:
    try:
        relation = TaskRelation(str(value or TaskRelation.NEW_TASK.value))
    except ValueError:
        relation = TaskRelation.NEW_TASK
    if relation in {TaskRelation.NEEDS_CLARIFICATION, TaskRelation.SWITCH, TaskRelation.CANCEL}:
        return TaskRelation.NEW_TASK
    return relation


def _parse_material_role(value: str, *, relation: TaskRelation) -> MaterialRole:
    try:
        role = MaterialRole(str(value or ""))
    except ValueError:
        role = MaterialRole.NONE
    if role is not MaterialRole.NONE:
        return role
    if relation is TaskRelation.ADD_MATERIAL:
        return MaterialRole.SUPPLEMENT
    return MaterialRole.NEW_TASK


def _format_result_for_log(result: PlatformResult) -> str:
    title = str(result.output.get("title", "") or "").strip()
    body = str(result.output.get("body", "") or "").strip()
    revision_note = str(result.output.get("revision_note", "") or "").strip()
    parts = [item for item in (title, body) if item]
    if revision_note:
        parts.append(f"修改说明：{revision_note}")
    if parts:
        return "\n\n".join(parts)
    return result.message


def _read_json_file(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_uploaded_files(files: list[UploadedFile]) -> None:
    invalid_names = [
        item.filename
        for item in files
        if Path(item.filename or "").suffix.lower() not in SUPPORTED_UPLOAD_SUFFIXES
    ]
    if invalid_names:
        raise ValueError("暂时只支持上传 Word(.docx)、PDF(.pdf) 和 PPT(.pptx) 文件。")


def _sanitize_filename(filename: str) -> str:
    candidate = Path(filename or "").name.strip() or "uploaded.bin"
    cleaned = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", candidate)
    return cleaned.strip("._") or "uploaded.bin"


def _unique_filename(filename: str, used_names: set[str]) -> str:
    if filename not in used_names:
        return filename
    stem = Path(filename).stem or "uploaded"
    suffix = Path(filename).suffix
    index = 2
    while True:
        candidate = f"{stem}-{index}{suffix}"
        if candidate not in used_names:
            return candidate
        index += 1


def _resolve_structured_skill_id(
    *,
    requested_skill_id: str,
    urls: list[str],
    files: list[str],
    material_text: str,
) -> str:
    if requested_skill_id != "brief":
        return requested_skill_id
    source_count = len(urls) + len(files)
    stripped_material_text = material_text.strip()
    if stripped_material_text:
        split_count = len(_split_inline_materials(stripped_material_text))
        source_count += split_count if split_count >= 2 else 1
    return "writer2" if source_count >= 2 else "writer1"


def _split_inline_materials(text: str) -> list[str]:
    markers = ("素材一", "素材二", "素材三", "素材四", "材料一", "材料二", "材料三", "材料四")
    pattern = "|".join(markers)
    pieces: list[str] = []
    current = ""
    for chunk in re.split(f"({pattern})[，,:：、\\s]*", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk in markers:
            if current.strip():
                pieces.append(current.strip())
            current = ""
            continue
        current = f"{current}\n{chunk}".strip() if current else chunk
    if current.strip():
        pieces.append(current.strip())
    return [piece for piece in pieces if len(piece) >= 8]
