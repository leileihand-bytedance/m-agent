from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from app.platform.intake import (
    IntakeMaterialRef,
    IntakeOutcome,
    IntakePersistence,
    IntakeTaskSubmission,
)
from app.platform.router import looks_like_inline_rewrite_task


REWRITE_DIRECTION_PROMPT = (
    "已收到原文。你希望重点怎么调整？可以直接说“更正式”“精简一些”"
    "“梳理逻辑”或“轻度润色、保留原意”。如果没有特殊要求，回复“按默认方式润色”即可。"
)
REWRITE_SOURCE_PROMPT = "请先粘贴需要润色的原文；收到原文后，我会再确认你的修改要求。"
REWRITE_CANCELLED_MESSAGE = "已取消本次润色，刚才保存的原文已清除。"
REWRITE_NOTHING_TO_CANCEL_MESSAGE = "当前没有待处理的润色原文。"
DEFAULT_REWRITE_INTAKE_TTL_SECONDS = 30 * 60
CANCEL_MESSAGES = frozenset(("取消", "取消本次", "取消润色", "不用了", "算了"))
DIRECTION_MARKERS = (
    "润色",
    "改写",
    "优化",
    "正式",
    "精简",
    "简洁",
    "梳理",
    "逻辑",
    "规范",
    "通顺",
    "保留原意",
    "轻度",
    "口语化",
    "书面化",
    "压缩",
    "扩写",
    "缩短",
    "语气",
    "风格",
    "默认方式",
)
DIRECTION_PREFIXES = ("请", "帮我", "麻烦", "按", "希望", "要求", "重点")
SOURCE_SENTENCE_MARKERS = ("。", "！", "？", "；", "\n")


@dataclass(frozen=True)
class _PendingRewrite:
    source_text: str
    created_at: float
    updated_at: float


class RewriteIntakeStore:
    """组装“原文 + 后续润色要求”，不参与其他 skill 的路由。"""

    def __init__(
        self,
        *,
        storage_dir: str | Path | None,
        ttl_seconds: int = DEFAULT_REWRITE_INTAKE_TTL_SECONDS,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._pending: dict[tuple[str, str], _PendingRewrite] = {}
        self._persistence = IntakePersistence(
            storage_dir=storage_dir,
            state_filename="rewrite-intake.json",
            ttl_seconds=ttl_seconds,
        )

    def handle_text(
        self,
        *,
        channel: str,
        sender_userid: str,
        text: str,
        is_revision: bool,
    ) -> IntakeOutcome:
        key = (channel, sender_userid)
        normalized = text.strip()
        pending = self._load(key)

        if normalized in CANCEL_MESSAGES:
            if pending is None:
                return IntakeOutcome.cancelled(REWRITE_NOTHING_TO_CANCEL_MESSAGE)
            self.clear(channel=channel, sender_userid=sender_userid)
            return IntakeOutcome.cancelled(REWRITE_CANCELLED_MESSAGE)

        if pending is not None:
            if looks_like_inline_rewrite_task(normalized):
                self.clear(channel=channel, sender_userid=sender_userid)
                return IntakeOutcome.bypass()
            return IntakeOutcome.submit(
                IntakeTaskSubmission(
                    channel=channel,
                    sender_userid=sender_userid,
                    task_type="rewrite",
                    instructions=(normalized,),
                    materials=(IntakeMaterialRef.text(pending.source_text),),
                )
            )

        if looks_like_inline_rewrite_task(normalized) or is_revision:
            return IntakeOutcome.bypass()
        if _looks_like_direction(normalized):
            return IntakeOutcome.wait(REWRITE_SOURCE_PROMPT)

        self._save(key, normalized)
        return IntakeOutcome.wait(REWRITE_DIRECTION_PROMPT)

    def clear(self, *, channel: str, sender_userid: str) -> None:
        key = (channel, sender_userid)
        self._pending.pop(key, None)
        self._persistence.clear(key, preserve_files=False)

    def _save(self, key: tuple[str, str], source_text: str) -> None:
        now = time.time()
        pending = _PendingRewrite(
            source_text=source_text,
            created_at=now,
            updated_at=now,
        )
        self._pending[key] = pending
        self._persistence.save_state(
            key,
            {
                "source_text": pending.source_text,
                "created_at": pending.created_at,
                "updated_at": pending.updated_at,
            },
        )

    def _load(self, key: tuple[str, str]) -> _PendingRewrite | None:
        pending = self._pending.get(key)
        if pending is not None:
            if time.time() - pending.updated_at <= self._ttl_seconds:
                return pending
            self.clear(channel=key[0], sender_userid=key[1])
            return None

        payload = self._persistence.load_state(key)
        if payload is None:
            return None
        try:
            pending = _PendingRewrite(
                source_text=str(payload["source_text"]).strip(),
                created_at=float(payload["created_at"]),
                updated_at=float(payload["updated_at"]),
            )
            if not pending.source_text:
                raise ValueError("source text is empty")
        except (KeyError, TypeError, ValueError):
            self.clear(channel=key[0], sender_userid=key[1])
            return None
        self._pending[key] = pending
        return pending


def _looks_like_direction(text: str) -> bool:
    if len(text) > 200:
        return False
    if not any(marker in text for marker in DIRECTION_MARKERS):
        return False
    if text.startswith(DIRECTION_PREFIXES):
        return True
    return len(text) <= 24 and not any(marker in text for marker in SOURCE_SENTENCE_MARKERS)


__all__ = [
    "DEFAULT_REWRITE_INTAKE_TTL_SECONDS",
    "REWRITE_DIRECTION_PROMPT",
    "RewriteIntakeStore",
]
