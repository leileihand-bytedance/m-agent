from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path

from app.platform.models import PlatformResult
from app.platform.skill_ids import canonical_skill_id


@dataclass(frozen=True)
class DraftVersion:
    version: int
    job_id: str
    skill_id: str
    title: str
    body: str
    sources: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class RevisionRequest:
    request: str
    previous_job_id: str
    new_job_id: str
    created_at: str


@dataclass(frozen=True)
class ConversationState:
    thread_id: str
    channel: str
    sender_userid: str
    sender_name: str
    active_skill_id: str
    draft_versions: tuple[DraftVersion, ...]
    revision_requests: tuple[RevisionRequest, ...]
    last_updated_at: str

    @property
    def current_draft(self) -> DraftVersion:
        return self.draft_versions[-1]


class ConversationStore:
    def __init__(self, root_dir: Path):
        self._root_dir = root_dir

    def get_active_conversation(self, *, channel: str, sender_userid: str) -> ConversationState | None:
        payload = _read_json(self._conversation_path(channel=channel, sender_userid=sender_userid))
        if not payload:
            return None
        return _conversation_from_payload(payload)

    def record_result(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str = "",
        job_id: str,
        result: PlatformResult,
        revision_request: str = "",
        previous_job_id: str = "",
    ) -> None:
        if result.needs_clarification or not result.skill_id:
            return
        skill_id = canonical_skill_id(result.skill_id) or result.skill_id

        title = str(result.output.get("title", "") or "").strip()
        body = str(result.output.get("body", "") or "").strip()
        if not (title or body):
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sources = tuple(
            str(item).strip()
            for item in list(result.output.get("sources") or [])
            if str(item).strip()
        )
        path = self._conversation_path(channel=channel, sender_userid=sender_userid)
        existing = _conversation_from_payload(_read_json(path))
        if existing and any(item.job_id == job_id for item in existing.draft_versions):
            return
        effective_sender_name = sender_name or (existing.sender_name if existing else sender_userid)

        is_revision = bool(str(revision_request or "").strip())
        if existing and is_revision and existing.active_skill_id == skill_id:
            draft_versions = list(existing.draft_versions)
            revision_requests = list(existing.revision_requests)
        else:
            draft_versions = []
            revision_requests = []

        version = len(draft_versions) + 1
        draft_versions.append(
            DraftVersion(
                version=version,
                job_id=job_id,
                skill_id=skill_id,
                title=title,
                body=body,
                sources=sources,
                created_at=now,
            )
        )
        if is_revision:
            revision_requests.append(
                RevisionRequest(
                    request=str(revision_request or "").strip(),
                    previous_job_id=str(previous_job_id or "").strip(),
                    new_job_id=job_id,
                    created_at=now,
                )
            )

        state = ConversationState(
            thread_id=_thread_id(channel=channel, sender_userid=sender_userid),
            channel=channel,
            sender_userid=sender_userid,
            sender_name=effective_sender_name,
            active_skill_id=skill_id,
            draft_versions=tuple(draft_versions),
            revision_requests=tuple(revision_requests),
            last_updated_at=now,
        )
        self._root_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_conversation_to_payload(state), ensure_ascii=False, indent=2), encoding="utf-8")

    def _conversation_path(self, *, channel: str, sender_userid: str) -> Path:
        return self._root_dir / f"{_thread_id(channel=channel, sender_userid=sender_userid)}.json"


def _thread_id(*, channel: str, sender_userid: str) -> str:
    raw = f"{channel}:{sender_userid}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _conversation_to_payload(state: ConversationState) -> dict[str, object]:
    return {
        "thread_id": state.thread_id,
        "channel": state.channel,
        "sender_userid": state.sender_userid,
        "sender_name": state.sender_name,
        "active_skill_id": state.active_skill_id,
        "last_updated_at": state.last_updated_at,
        "draft_versions": [
            {
                "version": item.version,
                "job_id": item.job_id,
                "skill_id": item.skill_id,
                "title": item.title,
                "body": item.body,
                "sources": list(item.sources),
                "created_at": item.created_at,
            }
            for item in state.draft_versions
        ],
        "revision_requests": [
            {
                "request": item.request,
                "previous_job_id": item.previous_job_id,
                "new_job_id": item.new_job_id,
                "created_at": item.created_at,
            }
            for item in state.revision_requests
        ],
    }


def _conversation_from_payload(payload: dict[str, object]) -> ConversationState | None:
    draft_payloads = payload.get("draft_versions", [])
    if not isinstance(draft_payloads, list) or not draft_payloads:
        return None

    drafts: list[DraftVersion] = []
    for raw in draft_payloads:
        if not isinstance(raw, dict):
            continue
        drafts.append(
            DraftVersion(
                version=int(raw.get("version", len(drafts) + 1) or len(drafts) + 1),
                job_id=str(raw.get("job_id", "")),
                skill_id=canonical_skill_id(str(raw.get("skill_id", ""))) or "",
                title=str(raw.get("title", "")),
                body=str(raw.get("body", "")),
                sources=tuple(str(item) for item in list(raw.get("sources") or []) if str(item).strip()),
                created_at=str(raw.get("created_at", "")),
            )
        )
    if not drafts:
        return None

    revision_payloads = payload.get("revision_requests", [])
    revisions: list[RevisionRequest] = []
    if isinstance(revision_payloads, list):
        for raw in revision_payloads:
            if not isinstance(raw, dict):
                continue
            revisions.append(
                RevisionRequest(
                    request=str(raw.get("request", "")),
                    previous_job_id=str(raw.get("previous_job_id", "")),
                    new_job_id=str(raw.get("new_job_id", "")),
                    created_at=str(raw.get("created_at", "")),
                )
            )

    return ConversationState(
        thread_id=str(payload.get("thread_id", "")),
        channel=str(payload.get("channel", "")),
        sender_userid=str(payload.get("sender_userid", "")),
        sender_name=str(payload.get("sender_name", payload.get("sender_userid", ""))),
        active_skill_id=(
            canonical_skill_id(str(payload.get("active_skill_id", drafts[-1].skill_id)))
            or drafts[-1].skill_id
        ),
        draft_versions=tuple(drafts),
        revision_requests=tuple(revisions),
        last_updated_at=str(payload.get("last_updated_at", "")),
    )
