from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import re

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
from app.platform.pydantic_runtime import PydanticAIWriter
from app.platform.registry import SkillRegistry
from app.platform.router import URL_RE, route_message
from app.platform.runtime import PlatformRuntime
from app.platform.storage import JobStore
from app.platform.user_registry import UserRegistry

SUPPORTED_UPLOAD_SUFFIXES = {".docx", ".pdf", ".pptx"}


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
        direct_report_critic_mode: str = "advisory",
    ):
        self._registry = registry
        self._runtime = PlatformRuntime(registry=registry, tools=tools)
        self._job_store = job_store
        self._conversation_store = conversation_store
        self._chat_log_store = chat_log_store
        self._user_registry = user_registry
        self._access_policy = access_policy
        self._direct_report_critic_mode = direct_report_critic_mode

    @classmethod
    def from_config(cls, config: PlatformConfig) -> "PlatformApp":
        registry = SkillRegistry.from_directory(config.skills_dir)
        tools = build_platform_tools(config)
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
            access_policy=access_policy,
            direct_report_critic_mode=config.direct_report_critic_mode,
        )

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
        sender_name = self.resolve_sender_name(sender_userid)
        job = self._job_store.create_job(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            message=text,
        )
        route = route_message(text, self._registry)
        route = self._revision_route_or_original(
            original_route=route,
            channel=channel,
            sender_userid=sender_userid,
            text=text,
        )
        return self._run_routed_job(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            route=route,
            job=job,
            user_text=text,
            ack_message=ack_message,
        )

    def classify_text_intent(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
    ) -> ConversationIntent:
        route = route_message(text, self._registry)
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
        return self._revision_route_or_original(
            original_route=route,
            channel=channel,
            sender_userid=sender_userid,
            text=text,
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
    ) -> PlatformResult:
        clean_urls = [str(url).strip() for url in list(urls or []) if str(url).strip()]
        uploaded_files = list(files or [])
        _validate_uploaded_files(uploaded_files)
        sender_name = self.resolve_sender_name(sender_userid)
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
        preview_parts = [skill_id, text.strip(), material_text.strip(), *clean_urls, *[item.filename for item in uploaded_files]]
        job = self._job_store.create_job(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            message="\n".join(part for part in preview_parts if part),
        )
        saved_files = _save_uploaded_files(job.input_dir, uploaded_files)
        route = RoutedRequest(
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
        return self._run_routed_job(
            channel=channel,
            sender_userid=sender_userid,
            sender_name=sender_name,
            route=route,
            job=job,
            user_text="\n".join(part for part in preview_parts if part),
            ack_message="",
        )

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
            raise
        self._job_store.write_result(job, result)
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


def build_platform_tools(config: PlatformConfig) -> dict[str, Callable[..., object]]:
    writer = PydanticAIWriter(
        api_key=config.anthropic_api_key,
        base_url=config.anthropic_base_url,
        model_name=config.model_name,
        skill_dir=config.skills_dir,
        model_max_tokens=config.model_max_tokens,
    )
    return {
        "web_reader": read_web_page,
        "search": lambda query, max_results=5: search_web(
            query,
            api_key=config.anthropic_api_key,
            base_url=config.anthropic_base_url,
            max_results=max_results,
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
        _cleanup_consumed_upload(item)
        used_names.add(target_name)
        saved_paths.append(str(target_path))
    return saved_paths


def _cleanup_consumed_upload(item: UploadedFile) -> None:
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
