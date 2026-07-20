from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from typing import Any, Final
from uuid import uuid4


PENDING: Final = "pending"
SENDING: Final = "sending"
CONFIRMED_DELIVERED: Final = "confirmed_delivered"
CONFIRMED_NOT_DELIVERED: Final = "confirmed_not_delivered"
DELIVERY_UNKNOWN: Final = "delivery_unknown"
DELIVERY_UNKNOWN_CLOSED: Final = "delivery_unknown_closed"

FINAL_DELIVERY_STATUSES = frozenset(
    {CONFIRMED_DELIVERED, CONFIRMED_NOT_DELIVERED, DELIVERY_UNKNOWN}
)
CHECKPOINT_DELIVERY_STATUSES = frozenset(
    {
        PENDING,
        SENDING,
        CONFIRMED_DELIVERED,
        CONFIRMED_NOT_DELIVERED,
        DELIVERY_UNKNOWN,
        DELIVERY_UNKNOWN_CLOSED,
    }
)


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str
    evidence: str
    safe_error_code: str = ""
    occurred_at: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )
    attempt_id: str = field(default_factory=lambda: f"delivery-{uuid4().hex}")
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if self.status not in FINAL_DELIVERY_STATUSES:
            raise ValueError(f"不支持的交付状态：{self.status}")
        if not re.fullmatch(r"[a-z0-9_]{1,64}", self.evidence):
            raise ValueError("交付判断依据格式无效")
        if self.safe_error_code and not re.fullmatch(
            r"[a-z0-9_]{1,64}", self.safe_error_code
        ):
            raise ValueError("交付安全错误码格式无效")
        if not re.fullmatch(r"delivery-[a-f0-9]{32}", self.attempt_id):
            raise ValueError("交付尝试编号格式无效")
        if self.correlation_id and not re.fullmatch(
            r"ack-[a-f0-9]{16}", self.correlation_id
        ):
            raise ValueError("交付关联标识格式无效")

    @property
    def delivered(self) -> bool:
        return self.status == CONFIRMED_DELIVERED

    def checkpoint_fields(self) -> dict[str, str]:
        return {
            "status": self.status,
            "attempt_id": self.attempt_id,
            "attempted_at": self.occurred_at,
            "evidence": self.evidence,
            "safe_error_code": self.safe_error_code,
            "correlation_id": self.correlation_id,
        }


async def capture_wecom_delivery(
    operation: Callable[[], Awaitable[object]],
    *,
    timeout_seconds: float,
    attempt_id: str | None = None,
) -> DeliveryOutcome:
    """执行一次企业微信发送，并把 SDK 回执收敛为可持久化状态。"""

    resolved_attempt_id = attempt_id or f"delivery-{uuid4().hex}"
    try:
        response = await asyncio.wait_for(operation(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        return DeliveryOutcome(
            status=DELIVERY_UNKNOWN,
            evidence="local_wait_timeout",
            safe_error_code="delivery_ack_timeout",
            attempt_id=resolved_attempt_id,
        )
    except Exception as exc:
        return classify_wecom_exception(exc, attempt_id=resolved_attempt_id)
    return classify_wecom_response(response, attempt_id=resolved_attempt_id)


def classify_wecom_response(
    response: object,
    *,
    attempt_id: str | None = None,
) -> DeliveryOutcome:
    resolved_attempt_id = attempt_id or f"delivery-{uuid4().hex}"
    if not isinstance(response, Mapping):
        return DeliveryOutcome(
            status=DELIVERY_UNKNOWN,
            evidence="sdk_response_unrecognized",
            safe_error_code="delivery_ack_unrecognized",
            attempt_id=resolved_attempt_id,
        )

    raw_errcode = response.get("errcode")
    if isinstance(raw_errcode, bool) or not isinstance(raw_errcode, int):
        return DeliveryOutcome(
            status=DELIVERY_UNKNOWN,
            evidence="sdk_response_unrecognized",
            safe_error_code="delivery_ack_unrecognized",
            attempt_id=resolved_attempt_id,
            correlation_id=_correlation_id(response),
        )
    if raw_errcode == 0:
        return DeliveryOutcome(
            status=CONFIRMED_DELIVERED,
            evidence="sdk_ack_success",
            attempt_id=resolved_attempt_id,
            correlation_id=_correlation_id(response),
        )
    return DeliveryOutcome(
        status=CONFIRMED_NOT_DELIVERED,
        evidence="sdk_ack_rejected",
        safe_error_code="wecom_rejected",
        attempt_id=resolved_attempt_id,
        correlation_id=_correlation_id(response),
    )


def classify_wecom_exception(
    error: BaseException,
    *,
    attempt_id: str | None = None,
) -> DeliveryOutcome:
    resolved_attempt_id = attempt_id or f"delivery-{uuid4().hex}"
    message = str(error or "").lower()
    if "reply ack error" in message and "errcode=" in message:
        return DeliveryOutcome(
            status=CONFIRMED_NOT_DELIVERED,
            evidence="sdk_ack_rejected",
            safe_error_code="wecom_rejected",
            attempt_id=resolved_attempt_id,
        )
    if "reply ack timeout" in message:
        return DeliveryOutcome(
            status=DELIVERY_UNKNOWN,
            evidence="sdk_ack_timeout",
            safe_error_code="delivery_ack_timeout",
            attempt_id=resolved_attempt_id,
        )
    if "websocket not connected" in message or "reply queue" in message:
        return DeliveryOutcome(
            status=CONFIRMED_NOT_DELIVERED,
            evidence="sdk_local_rejection",
            safe_error_code="delivery_not_sent",
            attempt_id=resolved_attempt_id,
        )
    return DeliveryOutcome(
        status=DELIVERY_UNKNOWN,
        evidence="sdk_send_exception",
        safe_error_code="delivery_ack_unknown",
        attempt_id=resolved_attempt_id,
    )


def normalize_delivery_outcome(value: DeliveryOutcome | bool) -> DeliveryOutcome:
    if isinstance(value, DeliveryOutcome):
        return value
    if value is True:
        return DeliveryOutcome(
            status=CONFIRMED_DELIVERED,
            evidence="legacy_sender_success",
        )
    if value is False:
        return DeliveryOutcome(
            status=CONFIRMED_NOT_DELIVERED,
            evidence="legacy_sender_failure",
            safe_error_code="delivery_not_delivered",
        )
    raise TypeError("交付发送器必须返回 DeliveryOutcome 或 bool")


def begin_delivery_attempt(item: dict[str, object]) -> str:
    attempt_id = f"delivery-{uuid4().hex}"
    item.update(
        {
            "status": SENDING,
            "attempt_id": attempt_id,
            "attempted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "evidence": "attempt_started",
            "safe_error_code": "",
            "correlation_id": "",
        }
    )
    return attempt_id


def apply_delivery_outcome(
    item: dict[str, object],
    outcome: DeliveryOutcome,
) -> None:
    item.update(outcome.checkpoint_fields())


def normalize_checkpoint_status(value: object) -> str:
    status = str(value or "")
    legacy = {
        "delivered": CONFIRMED_DELIVERED,
        "failed": CONFIRMED_NOT_DELIVERED,
        "sending": DELIVERY_UNKNOWN,
    }
    return legacy.get(status, status)


def aggregate_delivery_status(items: list[object]) -> str:
    statuses = [
        normalize_checkpoint_status(item.get("status"))
        for item in items
        if isinstance(item, Mapping)
    ]
    if not statuses:
        return PENDING
    if all(status == CONFIRMED_DELIVERED for status in statuses):
        return CONFIRMED_DELIVERED
    if DELIVERY_UNKNOWN in statuses:
        return DELIVERY_UNKNOWN
    if CONFIRMED_NOT_DELIVERED in statuses:
        return CONFIRMED_NOT_DELIVERED
    if SENDING in statuses:
        return DELIVERY_UNKNOWN
    return PENDING


def _correlation_id(response: Mapping[str, Any]) -> str:
    headers = response.get("headers")
    if not isinstance(headers, Mapping):
        return ""
    req_id = str(headers.get("req_id", "") or "").strip()
    if not req_id:
        return ""
    digest = hashlib.sha256(req_id.encode("utf-8")).hexdigest()[:16]
    return f"ack-{digest}"
