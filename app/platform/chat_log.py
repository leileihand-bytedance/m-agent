from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from app.platform.intent import ConversationIntent
from app.platform.models import PlatformResult


class ChatLogStore:
    def __init__(
        self,
        root_dir: Path,
        *,
        enabled: bool = True,
        max_text_chars: int = 20000,
        max_reply_chars: int = 20000,
    ):
        self._root_dir = root_dir
        self._enabled = enabled
        self._max_text_chars = max_text_chars
        self._max_reply_chars = max_reply_chars

    def record_turn(
        self,
        *,
        channel: str,
        sender_userid: str,
        sender_name: str = "",
        job_id: str,
        user_text: str,
        ack_message: str,
        final_reply: str,
        intent: ConversationIntent,
        route_skill_id: str | None,
        result: PlatformResult,
        draft_version: int | None = None,
        previous_job_id: str = "",
        error: str | None = None,
    ) -> None:
        if not self._enabled:
            return

        self._root_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        entry = {
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "channel": channel,
            "sender_userid": sender_userid,
            "sender_name": sender_name or sender_userid,
            "thread_id": _thread_id(channel=channel, sender_userid=sender_userid),
            "job_id": job_id,
            "user_text": _truncate(user_text, self._max_text_chars),
            "ack_message": _truncate(ack_message, self._max_reply_chars),
            "final_reply": _truncate(final_reply, self._max_reply_chars),
            "intent": intent.value,
            "route_skill_id": route_skill_id,
            "result_skill_id": result.skill_id,
            "needs_clarification": result.needs_clarification,
            "result_message": result.message,
            "draft_version": draft_version,
            "previous_job_id": previous_job_id,
            "output": _safe_output(result.output),
            "error": error,
        }
        path = self._root_dir / f"{now.strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _thread_id(*, channel: str, sender_userid: str) -> str:
    raw = f"{channel}:{sender_userid}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _truncate(value: str, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[后文已截断]"


def _safe_output(output: dict[str, object]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in output.items():
        if isinstance(value, str):
            safe[key] = _truncate(value, 20000)
        elif isinstance(value, list):
            safe[key] = [_truncate(str(item), 2000) for item in value]
        elif isinstance(value, (int, float, bool)) or value is None:
            safe[key] = value
        else:
            safe[key] = _truncate(str(value), 2000)
    return safe
