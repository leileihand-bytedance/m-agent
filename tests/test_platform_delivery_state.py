from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.platform.delivery_state import (  # noqa: E402
    CONFIRMED_DELIVERED,
    CONFIRMED_NOT_DELIVERED,
    DELIVERY_UNKNOWN,
    DeliveryOutcome,
    capture_wecom_delivery,
    normalize_delivery_outcome,
)


@pytest.mark.anyio
async def test_wecom_success_ack_is_confirmed_and_req_id_is_desensitized():
    async def send():
        return {
            "headers": {"req_id": "sensitive-request-id"},
            "errcode": 0,
            "errmsg": "ok",
        }

    outcome = await capture_wecom_delivery(send, timeout_seconds=1)

    assert outcome.status == CONFIRMED_DELIVERED
    assert outcome.evidence == "sdk_ack_success"
    assert outcome.correlation_id
    assert "sensitive-request-id" not in outcome.correlation_id
    assert outcome.safe_error_code == ""


@pytest.mark.anyio
async def test_wecom_rejection_ack_is_confirmed_not_delivered():
    async def send():
        return {
            "headers": {"req_id": "request-id"},
            "errcode": 40013,
            "errmsg": "invalid recipient",
        }

    outcome = await capture_wecom_delivery(send, timeout_seconds=1)

    assert outcome.status == CONFIRMED_NOT_DELIVERED
    assert outcome.evidence == "sdk_ack_rejected"
    assert outcome.safe_error_code == "wecom_rejected"


@pytest.mark.anyio
async def test_wecom_ack_timeout_is_delivery_unknown_and_is_not_retried():
    calls = 0

    async def send():
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)

    outcome = await capture_wecom_delivery(send, timeout_seconds=0.01)

    assert outcome.status == DELIVERY_UNKNOWN
    assert outcome.evidence == "local_wait_timeout"
    assert outcome.safe_error_code == "delivery_ack_timeout"
    assert calls == 1


@pytest.mark.anyio
async def test_sdk_explicit_ack_error_exception_is_not_delivered():
    async def send():
        raise RuntimeError("Reply ack error: errcode=45009, errmsg=rate limited")

    outcome = await capture_wecom_delivery(send, timeout_seconds=1)

    assert outcome.status == CONFIRMED_NOT_DELIVERED
    assert outcome.evidence == "sdk_ack_rejected"
    assert outcome.safe_error_code == "wecom_rejected"


@pytest.mark.anyio
async def test_sdk_ack_timeout_exception_is_delivery_unknown():
    async def send():
        raise RuntimeError("Reply ack timeout (5.0s) for reqId: hidden")

    outcome = await capture_wecom_delivery(send, timeout_seconds=1)

    assert outcome.status == DELIVERY_UNKNOWN
    assert outcome.evidence == "sdk_ack_timeout"
    assert outcome.safe_error_code == "delivery_ack_timeout"
    assert "hidden" not in outcome.correlation_id


def test_legacy_boolean_sender_results_are_normalized_for_compatibility():
    delivered = normalize_delivery_outcome(True)
    failed = normalize_delivery_outcome(False)

    assert delivered.status == CONFIRMED_DELIVERED
    assert delivered.evidence == "legacy_sender_success"
    assert failed.status == CONFIRMED_NOT_DELIVERED
    assert failed.evidence == "legacy_sender_failure"


def test_delivery_outcome_rejects_unrecognized_status():
    with pytest.raises(ValueError, match="交付状态"):
        DeliveryOutcome(status="failed", evidence="test")
