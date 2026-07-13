from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path


@dataclass(frozen=True)
class StaleHeartbeat:
    service: str
    reason: str
    updated_at: str = ""
    age_seconds: int | None = None


def write_heartbeat(root_dir: Path, service: str, *, now: datetime | None = None) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)
    current = now or datetime.now()
    payload = {
        "service": service,
        "updated_at": current.strftime("%Y-%m-%d %H:%M:%S"),
    }
    (root_dir / f"{service}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def find_stale_heartbeats(
    root_dir: Path,
    *,
    monitored_services: list[str] | tuple[str, ...],
    now: datetime | None = None,
    max_age_seconds: int,
) -> list[StaleHeartbeat]:
    current = now or datetime.now()
    stale: list[StaleHeartbeat] = []
    for service in monitored_services:
        path = root_dir / f"{service}.json"
        if not path.exists():
            stale.append(StaleHeartbeat(service=service, reason="missing"))
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            updated_text = str(payload.get("updated_at", ""))
            updated_at = datetime.strptime(updated_text, "%Y-%m-%d %H:%M:%S")
        except Exception:
            stale.append(StaleHeartbeat(service=service, reason="invalid"))
            continue
        age = int((current - updated_at).total_seconds())
        if age > max_age_seconds:
            stale.append(
                StaleHeartbeat(
                    service=service,
                    reason="stale",
                    updated_at=updated_text,
                    age_seconds=age,
                )
            )
    return stale
