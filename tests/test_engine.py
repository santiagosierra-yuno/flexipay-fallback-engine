"""Unit tests for the FlexiPay Fallback Engine.

All tests exercise the engine and circuit-breaker logic directly —
no HTTP server is needed.  Processor responses are controlled through
lightweight mock objects that return a fixed ProcessorResult.
"""

import random
import threading
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
from app.processors.vortex_pay import VortexPay
from app.services.stats_service import StatsService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request(txn_id: str = "test-txn", currency: str = "USD") -> TransactionRequest:
    return TransactionRequest(
        transaction_id=txn_id,
        amount=Decimal("100.00"),
        currency=currency,
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


def _rate_limited(name: str) -> ProcessorResult:
    return ProcessorResult(
        processor_name=name,
        status=ProcessorResultStatus.RATE_LIMITED,
        decline_code="rate_limit_exceeded",
        decline_type=DeclineType.RATE_LIMIT,
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


class SequencedProcessor(AbstractProcessor):
    """Test double that returns results from a list in order, repeating the last one."""

    def __init__(self, name: str, fee_rate: float, results: list[ProcessorResult]) -> None:
        self.name = name
        self.fee_rate = fee_rate
        self._results = list(results)
        self.call_count = 0

    async def charge(self, request: TransactionRequest) -> ProcessorResult:
        result = self._results[min(self.call_count, len(self._results) - 1)]
        self.call_count += 1
        return result


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


async def test_rate_limit_backoff():
    """VortexPay rate-limits twice then succeeds; retry_log captures both backoff delays."""
    vortex = SequencedProcessor(
        "VortexPay", 0.025,
        [_rate_limited("VortexPay"), _rate_limited("VortexPay"), _success("VortexPay")],
    )
    swift = MockProcessor("SwiftPay", 0.029, _success("SwiftPay"))
    pix   = MockProcessor("PixFlow",  0.032, _success("PixFlow"))

    # Tiny backoff values keep the test near-instant while still exercising the sleep path.
    settings = Settings(BACKOFF_BASE_SECONDS=0.001, BACKOFF_MAX_SECONDS=0.001, BACKOFF_MAX_RETRIES=2)
    cb_registry = CircuitBreakerRegistry(settings)
    engine = FallbackEngine(
        processors=[vortex, swift, pix],
        cb_registry=cb_registry,
        stats_service=StatsService(),
        settings=settings,
    )

    resp = await engine.process(_request("txn-backoff"))

    assert resp.status == "approved"
    assert resp.processor_used == "VortexPay"
    assert vortex.call_count == 3          # 2 rate-limited + 1 success
    assert swift.call_count == 0           # never reached
    assert len(resp.retry_log) == 2        # one entry per backoff sleep
    assert all("VortexPay: rate_limited, backoff" in e for e in resp.retry_log)


async def test_cost_aware_routing_order():
    """For non-BRL currencies, processors are tried cheapest-first regardless of their list order."""
    vortex = MockProcessor("VortexPay", 0.025, _soft("VortexPay"))
    swift  = MockProcessor("SwiftPay",  0.029, _soft("SwiftPay"))
    pix    = MockProcessor("PixFlow",   0.032, _success("PixFlow", 0.032))

    # Pass processors in reverse fee order; engine must re-sort them cheapest-first for USD.
    engine, _ = _engine([pix, swift, vortex])
    resp = await engine.process(_request("txn-order", currency="USD"))

    assert resp.status == "approved"
    assert resp.processor_used == "PixFlow"
    names = [step.split("(")[0] for step in resp.processors_tried]
    assert names == ["VortexPay", "SwiftPay", "PixFlow"]


async def test_idempotency_cache():
    """Submitting the same transaction_id twice returns the cached response without re-calling processors."""
    vortex = MockProcessor("VortexPay", 0.025, _success("VortexPay"))
    swift  = MockProcessor("SwiftPay",  0.029, _success("SwiftPay"))
    pix    = MockProcessor("PixFlow",   0.032, _success("PixFlow"))

    engine, _ = _engine([vortex, swift, pix])

    resp1 = await engine.process(_request("txn-idem"))
    resp2 = await engine.process(_request("txn-idem"))

    assert resp1.transaction_id == resp2.transaction_id
    assert resp1.status == resp2.status
    assert resp1.processed_at == resp2.processed_at   # identical cached object
    assert vortex.call_count == 1                      # processor called exactly once


def test_stats_service():
    """StatsService correctly accumulates counts across record_attempt and record_final calls."""
    svc = StatsService()

    svc.record_attempt(ProcessorResult(
        processor_name="VortexPay",
        status=ProcessorResultStatus.SUCCESS,
        amount=Decimal("100.00"),
        fee=Decimal("2.50"),
        fee_rate=0.025,
        latency_ms=50.0,
    ))
    svc.record_attempt(ProcessorResult(
        processor_name="SwiftPay",
        status=ProcessorResultStatus.SOFT_DECLINE,
        decline_code="insufficient_funds",
        decline_type=DeclineType.SOFT,
        latency_ms=30.0,
    ))

    svc.record_final(approved=True,  amount=Decimal("100.00"), fee=Decimal("2.50"))
    svc.record_final(approved=False, amount=Decimal("50.00"),  fee=None)

    snap = svc.snapshot()
    assert snap.total_transactions == 2
    assert snap.total_approved == 1
    assert snap.total_declined == 1
    assert snap.per_processor["VortexPay"].success_count == 1
    assert snap.per_processor["SwiftPay"].soft_decline_count == 1


async def test_deterministic_cards():
    """Card 0000 always yields a hard decline (fraud_detected); card 1111 always soft-declines."""
    processor = VortexPay()

    req_fraud = TransactionRequest(
        transaction_id="txn-card-fraud",
        amount=Decimal("100.00"),
        currency="USD",
        merchant_id="test-merchant",
        card_last_four="0000",
    )
    req_soft = TransactionRequest(
        transaction_id="txn-card-soft",
        amount=Decimal("100.00"),
        currency="USD",
        merchant_id="test-merchant",
        card_last_four="1111",
    )

    result_fraud = await processor.charge(req_fraud)
    result_soft  = await processor.charge(req_soft)

    assert result_fraud.status == ProcessorResultStatus.HARD_DECLINE
    assert result_fraud.decline_code == "fraud_detected"

    assert result_soft.status == ProcessorResultStatus.SOFT_DECLINE
    assert result_soft.decline_code == "insufficient_funds"


def test_circuit_breaker_thread_safety():
    """50 threads call record_failure/record_success concurrently; state must stay consistent."""
    settings = Settings()
    cb = CircuitBreaker(
        name="ThreadSafeProc",
        window_size=settings.CB_ROLLING_WINDOW_SIZE,
        window_seconds=settings.CB_ROLLING_WINDOW_SECONDS,
        trip_threshold=settings.CB_TRIP_THRESHOLD,
        cooldown_seconds=settings.CB_COOLDOWN_SECONDS,
    )

    errors: list[Exception] = []

    def worker() -> None:
        try:
            if random.random() < 0.5:
                cb.record_failure()
            else:
                cb.record_success()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Exceptions raised in threads: {errors}"

    snap = cb.status_snapshot
    assert snap["total_calls_in_window"] >= 0
    assert snap["success_rate"] is None or 0.0 <= snap["success_rate"] <= 1.0
