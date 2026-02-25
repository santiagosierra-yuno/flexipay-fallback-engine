"""Unit tests for the FlexiPay Fallback Engine.

All tests exercise the engine and circuit-breaker logic directly —
no HTTP server is needed.  Processor responses are controlled through
lightweight mock objects that return a fixed ProcessorResult.
"""

from decimal import Decimal

import pytest

from app.circuit_breaker.breaker import CircuitBreaker
from app.circuit_breaker.registry import CircuitBreakerRegistry
from app.config import Settings
from app.engine.fallback_engine import FallbackEngine
from app.models.processor import (
    CircuitBreakerState,
    DeclineType,
    ProcessorResult,
    ProcessorResultStatus,
)
from app.models.transaction import TransactionRequest
from app.processors.base import AbstractProcessor
from app.services.stats_service import StatsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request(txn_id: str = "test-txn") -> TransactionRequest:
    return TransactionRequest(
        transaction_id=txn_id,
        amount=Decimal("100.00"),
        currency="BRL",
        merchant_id="test-merchant",
        card_last_four="4242",
    )


def _success(name: str, fee_rate: float = 0.025) -> ProcessorResult:
    amount = Decimal("100.00")
    return ProcessorResult(
        processor_name=name,
        status=ProcessorResultStatus.SUCCESS,
        amount=amount,
        fee=amount * Decimal(str(fee_rate)),
        fee_rate=fee_rate,
        latency_ms=5.0,
    )


def _soft(name: str) -> ProcessorResult:
    return ProcessorResult(
        processor_name=name,
        status=ProcessorResultStatus.SOFT_DECLINE,
        decline_code="insufficient_funds",
        decline_type=DeclineType.SOFT,
        latency_ms=5.0,
    )


def _hard(name: str) -> ProcessorResult:
    return ProcessorResult(
        processor_name=name,
        status=ProcessorResultStatus.HARD_DECLINE,
        decline_code="stolen_card",
        decline_type=DeclineType.HARD,
        latency_ms=5.0,
    )


class MockProcessor(AbstractProcessor):
    """Test double that returns a predetermined result and counts calls."""

    def __init__(self, name: str, fee_rate: float, result: ProcessorResult) -> None:
        self.name = name
        self.fee_rate = fee_rate
        self._result = result
        self.call_count = 0

    async def charge(self, request: TransactionRequest) -> ProcessorResult:
        self.call_count += 1
        return self._result


def _engine(
    processors: list[AbstractProcessor],
    cb_registry: CircuitBreakerRegistry | None = None,
) -> tuple[FallbackEngine, CircuitBreakerRegistry]:
    settings = Settings()
    if cb_registry is None:
        cb_registry = CircuitBreakerRegistry(settings)
    engine = FallbackEngine(
        processors=processors,
        cb_registry=cb_registry,
        stats_service=StatsService(),
        settings=settings,
    )
    return engine, cb_registry


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_successful_transaction():
    """VortexPay succeeds on the first attempt; other processors are never called."""
    vortex = MockProcessor("VortexPay", 0.025, _success("VortexPay"))
    swift  = MockProcessor("SwiftPay",  0.029, _success("SwiftPay"))
    pix    = MockProcessor("PixFlow",   0.032, _success("PixFlow"))

    engine, _ = _engine([vortex, swift, pix])
    resp = await engine.process(_request("txn-success"))

    assert resp.status == "approved"
    assert resp.processor_used == "VortexPay"
    assert resp.attempts == 1
    assert swift.call_count == 0
    assert pix.call_count == 0


async def test_soft_decline_triggers_fallback():
    """VortexPay soft-declines; the engine falls back to SwiftPay which succeeds."""
    vortex = MockProcessor("VortexPay", 0.025, _soft("VortexPay"))
    swift  = MockProcessor("SwiftPay",  0.029, _success("SwiftPay", 0.029))
    pix    = MockProcessor("PixFlow",   0.032, _success("PixFlow",  0.032))

    engine, _ = _engine([vortex, swift, pix])
    resp = await engine.process(_request("txn-soft"))

    assert resp.status == "approved"
    assert resp.processor_used == "SwiftPay"
    assert resp.attempts == 2
    assert vortex.call_count == 1
    assert swift.call_count == 1
    assert pix.call_count == 0


async def test_hard_decline_no_retry():
    """VortexPay hard-declines; engine stops immediately without trying other processors."""
    vortex = MockProcessor("VortexPay", 0.025, _hard("VortexPay"))
    swift  = MockProcessor("SwiftPay",  0.029, _success("SwiftPay"))
    pix    = MockProcessor("PixFlow",   0.032, _success("PixFlow"))

    engine, _ = _engine([vortex, swift, pix])
    resp = await engine.process(_request("txn-hard"))

    assert resp.status == "declined"
    assert resp.attempts == 1
    assert resp.decline_type == "hard"
    assert swift.call_count == 0
    assert pix.call_count == 0


async def test_all_processors_fail():
    """All three processors soft-decline; response is declined after exactly 3 attempts."""
    vortex = MockProcessor("VortexPay", 0.025, _soft("VortexPay"))
    swift  = MockProcessor("SwiftPay",  0.029, _soft("SwiftPay"))
    pix    = MockProcessor("PixFlow",   0.032, _soft("PixFlow"))

    engine, _ = _engine([vortex, swift, pix])
    resp = await engine.process(_request("txn-all-fail"))

    assert resp.status == "declined"
    assert resp.attempts == 3
    assert vortex.call_count == 1
    assert swift.call_count == 1
    assert pix.call_count == 1


async def test_circuit_breaker_skips_open_processor():
    """When VortexPay's CB is OPEN it is bypassed; SwiftPay handles the transaction."""
    vortex = MockProcessor("VortexPay", 0.025, _success("VortexPay"))
    swift  = MockProcessor("SwiftPay",  0.029, _success("SwiftPay", 0.029))
    pix    = MockProcessor("PixFlow",   0.032, _success("PixFlow"))

    settings = Settings()
    cb_registry = CircuitBreakerRegistry(settings)
    engine, _ = _engine([vortex, swift, pix], cb_registry=cb_registry)

    # Trip VortexPay's circuit breaker (clean window → 6 failures → 0% success → OPEN)
    vortex_cb = cb_registry.get("VortexPay")
    vortex_cb.reset()
    vortex_cb.inject_failures(6)
    assert vortex_cb.status_snapshot["state"] == CircuitBreakerState.OPEN

    resp = await engine.process(_request("txn-cb-skip"))

    assert resp.status == "approved"
    assert any("VortexPay(circuit_open)" in step for step in resp.processors_tried)
    assert vortex.call_count == 0  # charge() was never invoked on the open processor
    assert swift.call_count == 1


async def test_circuit_breaker_trips_after_failures():
    """Recording 5 consecutive failures drives the success rate to 0% and opens the CB."""
    settings = Settings()
    cb = CircuitBreaker(
        name="TestProc",
        window_size=settings.CB_ROLLING_WINDOW_SIZE,
        window_seconds=settings.CB_ROLLING_WINDOW_SECONDS,
        trip_threshold=settings.CB_TRIP_THRESHOLD,   # 0.20
        cooldown_seconds=settings.CB_COOLDOWN_SECONDS,
    )

    # Minimum sample size to trigger evaluation is 5; all failures → 0% < 20%
    for _ in range(5):
        cb.record_failure()

    snap = cb.status_snapshot
    assert snap["state"] == CircuitBreakerState.OPEN
    assert snap["success_rate"] == 0.0
    assert snap["total_calls_in_window"] == 5
    assert snap["failed_calls_in_window"] == 5
