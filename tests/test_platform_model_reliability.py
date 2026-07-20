from __future__ import annotations

import pytest

from app.platform.model_reliability import (
    ModelCallError,
    ModelCallPolicy,
    classify_model_error,
    run_model_call,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("late"), "model_timeout"),
        (RuntimeError("429 rate limit"), "model_rate_limited"),
        (RuntimeError("401 unauthorized"), "model_auth_failed"),
        (ConnectionError("connection reset"), "model_connection_failed"),
        (RuntimeError("503 service unavailable"), "model_unavailable"),
        (ValueError("invalid json response"), "model_invalid_response"),
        (RuntimeError("unexpected"), "model_call_failed"),
    ],
)
def test_model_error_classification_is_safe_and_stable(error: Exception, expected: str) -> None:
    assert classify_model_error(error) == expected


def test_transient_model_failure_retries_with_bounded_budget() -> None:
    attempts = 0
    reports = []

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("provider detail must not escape")
        return "ok"

    result = run_model_call(
        operation,
        policy=ModelCallPolicy(max_attempts=2, backoff_seconds=0),
        observer=reports.append,
    )

    assert result == "ok"
    assert attempts == 2
    assert reports[-1].succeeded
    assert reports[-1].attempts == 2
    assert reports[-1].safe_error_code == ""


def test_auth_failure_is_not_retried_or_exposed() -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("401 secret-provider-payload")

    with pytest.raises(ModelCallError) as captured:
        run_model_call(
            operation,
            policy=ModelCallPolicy(max_attempts=3, backoff_seconds=0),
        )

    assert attempts == 1
    assert captured.value.safe_error_code == "model_auth_failed"
    assert "secret-provider-payload" not in str(captured.value)


def test_unknown_model_failure_is_not_retried() -> None:
    attempts = 0

    def operation() -> str:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("unexpected implementation detail")

    with pytest.raises(ModelCallError) as captured:
        run_model_call(
            operation,
            policy=ModelCallPolicy(max_attempts=3, backoff_seconds=0),
        )

    assert attempts == 1
    assert captured.value.safe_error_code == "model_call_failed"
    assert not captured.value.retryable
