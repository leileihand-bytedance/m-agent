"""Shared review model invocation and bounded retry primitives."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Generic, TypeVar

from .metrics import ReviewRunMetrics
from app.platform.model_reliability import (
    ModelCallError,
    classify_model_error,
    is_retryable_model_error,
)


T = TypeVar("T")


@dataclass(frozen=True)
class RetryOutcome(Generic[T]):
    value: T | None
    errors: tuple[str, ...]
    attempts: int
    succeeded: bool


def create_model_message(
    client,
    *,
    metrics: ReviewRunMetrics | None,
    stage: str,
    **kwargs,
):
    """Call one model request without changing its provider-specific arguments."""
    if metrics is not None:
        metrics.record_model_call(stage)
    started_at = perf_counter()
    try:
        return client.messages.create(**kwargs)
    except Exception as exc:
        if metrics is not None:
            metrics.record_model_failure(stage)
        safe_error_code = classify_model_error(exc)
        raise ModelCallError(
            safe_error_code,
            attempts=1,
            retryable=is_retryable_model_error(safe_error_code),
        ) from exc
    finally:
        if metrics is not None:
            metrics.record_model_elapsed(stage, (perf_counter() - started_at) * 1000)


async def run_with_retries(
    operation: Callable[[int], Awaitable[tuple[T, str | None]]],
    *,
    max_attempts: int,
) -> RetryOutcome[T]:
    """Run a stage until its explicit error clears or its budget is exhausted."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    errors: list[str] = []
    for attempt in range(max_attempts):
        try:
            value, error = await operation(attempt)
        except ModelCallError as exc:
            value, error = None, exc.safe_error_code
            if not exc.retryable:
                errors.append(error)
                return RetryOutcome(
                    value=None,
                    errors=tuple(errors),
                    attempts=attempt + 1,
                    succeeded=False,
                )
        except Exception as exc:
            value, error = None, classify_model_error(exc)
            if not is_retryable_model_error(error):
                errors.append(error)
                return RetryOutcome(
                    value=None,
                    errors=tuple(errors),
                    attempts=attempt + 1,
                    succeeded=False,
                )
        if error is None:
            return RetryOutcome(
                value=value,
                errors=tuple(errors),
                attempts=attempt + 1,
                succeeded=True,
            )
        errors.append(error)
    return RetryOutcome(
        value=None,
        errors=tuple(errors),
        attempts=max_attempts,
        succeeded=False,
    )
