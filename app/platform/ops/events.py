from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path


@dataclass(frozen=True)
class OpsEvent:
    event_id: str
    created_at: str
    source: str
    severity: str
    subject: str
    detail: str
    sender_userid: str = ""
    sender_name: str = ""
    skill_id: str = ""
    job_id: str = ""


class OpsEventLogger:
    def __init__(self, root_dir: Path, *, max_detail_chars: int = 4000):
        self._root_dir = root_dir
        self._max_detail_chars = max_detail_chars

    def record(
        self,
        *,
        source: str,
        severity: str,
        subject: str,
        detail: str,
        sender_userid: str = "",
        sender_name: str = "",
        skill_id: str = "",
        job_id: str = "",
        created_at: datetime | None = None,
    ) -> OpsEvent:
        now = created_at or datetime.now()
        event = _make_event(
            created_at=now,
            source=source,
            severity=severity,
            subject=subject,
            detail=_truncate(str(detail or ""), self._max_detail_chars),
            sender_userid=sender_userid,
            sender_name=sender_name,
            skill_id=skill_id,
            job_id=job_id,
        )
        self._root_dir.mkdir(parents=True, exist_ok=True)
        path = self._root_dir / f"{now.strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event.__dict__, ensure_ascii=False) + "\n")
        return event


def read_ops_events(root_dir: Path, target_day: date) -> list[OpsEvent]:
    path = root_dir / f"{target_day.strftime('%Y%m%d')}.jsonl"
    if not path.exists():
        return []
    events: list[OpsEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            events.append(
                OpsEvent(
                    event_id=str(payload.get("event_id", "")),
                    created_at=str(payload.get("created_at", "")),
                    source=str(payload.get("source", "")),
                    severity=str(payload.get("severity", "")),
                    subject=str(payload.get("subject", "")),
                    detail=str(payload.get("detail", "")),
                    sender_userid=str(payload.get("sender_userid", "")),
                    sender_name=str(payload.get("sender_name", "")),
                    skill_id=str(payload.get("skill_id", "")),
                    job_id=str(payload.get("job_id", "")),
                )
            )
        except Exception:
            continue
    return events


def _make_event(
    *,
    created_at: datetime,
    source: str,
    severity: str,
    subject: str,
    detail: str,
    sender_userid: str,
    sender_name: str,
    skill_id: str,
    job_id: str,
) -> OpsEvent:
    created_text = created_at.strftime("%Y-%m-%d %H:%M:%S")
    raw = "|".join(
        [
            created_text,
            source,
            severity,
            subject,
            detail,
            sender_userid,
            sender_name,
            skill_id,
            job_id,
        ]
    )
    event_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return OpsEvent(
        event_id=event_id,
        created_at=created_text,
        source=source,
        severity=severity,
        subject=subject,
        detail=detail,
        sender_userid=sender_userid,
        sender_name=sender_name,
        skill_id=skill_id,
        job_id=job_id,
    )


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n[后文已截断]"
