from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import re
import time
from typing import TypeVar


T = TypeVar("T")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCallPolicy:
    timeout_seconds: float = 180.0
    max_attempts: int = 2
    backoff_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("模型超时必须大于 0")
        if self.max_attempts < 1 or self.max_attempts > 3:
            raise ValueError("模型调用次数必须在 1 到 3 之间")
        if self.backoff_seconds < 0 or self.backoff_seconds > 30:
            raise ValueError("模型重试间隔必须在 0 到 30 秒之间")


@dataclass(frozen=True)
class ModelCallReport:
    succeeded: bool
    attempts: int
    elapsed_seconds: float
    safe_error_code: str = ""


class ModelCallError(RuntimeError):
    def __init__(self, safe_error_code: str, *, attempts: int, retryable: bool) -> None:
        super().__init__(f"模型调用失败（{safe_error_code}）")
        self.safe_error_code = safe_error_code
        self.attempts = attempts
        self.retryable = retryable


def classify_model_error(error: BaseException) -> str:
    """Collapse provider-specific failures into a small, non-sensitive contract."""

    name = type(error).__name__.lower()
    message = str(error).lower()
    status_code = getattr(error, "status_code", None)
    if isinstance(error, TimeoutError) or "timeout" in name or "timed out" in message:
        return "model_timeout"
    if status_code == 429 or re.search(r"\b429\b", message) or "rate limit" in message:
        return "model_rate_limited"
    if status_code in {401, 403} or re.search(r"\b(?:401|403)\b", message):
        return "model_auth_failed"
    if isinstance(error, ConnectionError) or any(
        marker in name or marker in message
        for marker in ("connection", "connecterror", "networkerror", "dns")
    ):
        return "model_connection_failed"
    if status_code is not None and 500 <= int(status_code) <= 599:
        return "model_unavailable"
    if any(marker in message for marker in ("service unavailable", "bad gateway", "overloaded")):
        return "model_unavailable"
    if "validationerror" in name or any(
        marker in message
        for marker in ("invalid json", "invalid response", "structured output")
    ):
        return "model_invalid_response"
    return "model_call_failed"


def is_retryable_model_error(safe_error_code: str) -> bool:
    return safe_error_code in {
        "model_timeout",
        "model_rate_limited",
        "model_connection_failed",
        "model_unavailable",
        "model_invalid_response",
    }


def run_model_call(
    operation: Callable[[], T],
    *,
    policy: ModelCallPolicy,
    observer: Callable[[ModelCallReport], object] | None = None,
    sleep: Callable[[float], object] = time.sleep,
) -> T:
    """Run one logical model request with a bounded, provider-neutral retry budget."""

    started_at = time.perf_counter()
    last_error: BaseException | None = None
    last_code = "model_call_failed"
    for attempt in range(1, policy.max_attempts + 1):
        try:
            result = operation()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            last_code = classify_model_error(exc)
            retryable = is_retryable_model_error(last_code)
            logger.warning(
                "model_call_failed safe_error_code=%s attempt=%d max_attempts=%d",
                last_code,
                attempt,
                policy.max_attempts,
            )
            if not retryable or attempt >= policy.max_attempts:
                report = ModelCallReport(
                    succeeded=False,
                    attempts=attempt,
                    elapsed_seconds=time.perf_counter() - started_at,
                    safe_error_code=last_code,
                )
                _notify_observer(observer, report)
                raise ModelCallError(
                    last_code,
                    attempts=attempt,
                    retryable=retryable,
                ) from exc
            sleep(policy.backoff_seconds * attempt)
            continue

        report = ModelCallReport(
            succeeded=True,
            attempts=attempt,
            elapsed_seconds=time.perf_counter() - started_at,
        )
        _notify_observer(observer, report)
        logger.info("model_call_succeeded attempts=%d", attempt)
        return result

    raise ModelCallError(last_code, attempts=policy.max_attempts, retryable=True) from last_error


def _notify_observer(
    observer: Callable[[ModelCallReport], object] | None,
    report: ModelCallReport,
) -> None:
    if observer is None:
        return
    try:
        observer(report)
    except Exception:  # noqa: BLE001
        logger.warning("model_call_observer_failed")
