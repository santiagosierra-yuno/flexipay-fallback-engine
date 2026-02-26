"""
MockableProcessor — shared charge() implementation for all mock processors.

VortexPay, SwiftPay, and PixFlow each extend this class and supply their
per-processor configuration (outcome table, latency envelope, decline codes,
and optional deterministic card overrides) via __init__.
The charge() logic itself lives here exactly once.
"""

import asyncio
import random
import time

from app.processors.base import AbstractProcessor
from app.models.transaction import TransactionRequest
from app.models.processor import DeclineType, ProcessorResult, ProcessorResultStatus

# (cumulative_probability, outcome_status)
OutcomeTable = list[tuple[float, ProcessorResultStatus]]

# card_last_four -> (forced_status, forced_decline_code | None)
CardOverrides = dict[str, tuple[ProcessorResultStatus, str | None]]


class MockableProcessor(AbstractProcessor):
    """
    Parameterised mock processor.

    Args:
        name:           Processor identifier used throughout the engine.
        fee_rate:       Decimal fee fraction (e.g. 0.025 = 2.5 %).
        latency_range:  (min_seconds, max_seconds) for simulated network delay.
        outcome_table:  Probability-weighted list of (cumulative_prob, status).
                        Probabilities must sum to ≤ 1.0; any remainder maps to
                        SUCCESS.
        soft_codes:     Decline codes sampled when outcome is SOFT_DECLINE.
        hard_codes:     Decline codes sampled when outcome is HARD_DECLINE.
        card_overrides: Optional mapping of card_last_four -> (forced_status,
                        forced_decline_code).  Matched before random selection,
                        enabling deterministic test scenarios without touching
                        production routing logic.
    """

    def __init__(
        self,
        name: str,
        fee_rate: float,
        latency_range: tuple[float, float],
        outcome_table: OutcomeTable,
        soft_codes: list[str],
        hard_codes: list[str],
        card_overrides: CardOverrides | None = None,
    ) -> None:
        self.name = name
        self.fee_rate = fee_rate
        self._latency_range = latency_range
        self._outcome_table = outcome_table
        self._soft_codes = soft_codes
        self._hard_codes = hard_codes
        self._card_overrides: CardOverrides = card_overrides or {}

    def _pick_outcome(self) -> ProcessorResultStatus:
        r = random.random()
        cumulative = 0.0
        for prob, outcome in self._outcome_table:
            cumulative += prob
            if r < cumulative:
                return outcome
        return ProcessorResultStatus.SUCCESS

    async def charge(self, request: TransactionRequest) -> ProcessorResult:
        start = time.monotonic()
        await asyncio.sleep(random.uniform(*self._latency_range))
        elapsed_ms = (time.monotonic() - start) * 1000

        # Deterministic test-card overrides take priority over random selection
        forced = self._card_overrides.get(request.card_last_four)
        outcome = forced[0] if forced else self._pick_outcome()

        if outcome == ProcessorResultStatus.SUCCESS:
            fee = request.amount * type(request.amount)(str(self.fee_rate))
            return ProcessorResult(
                processor_name=self.name,
                status=ProcessorResultStatus.SUCCESS,
                amount=request.amount,
                fee=fee,
                fee_rate=self.fee_rate,
                raw_response={"code": "00", "message": "Approved"},
                latency_ms=elapsed_ms,
            )

        if outcome == ProcessorResultStatus.SOFT_DECLINE:
            code = forced[1] if forced else random.choice(self._soft_codes)
            return ProcessorResult(
                processor_name=self.name,
                status=ProcessorResultStatus.SOFT_DECLINE,
                decline_code=code,
                decline_type=DeclineType.SOFT,
                raw_response={"code": "51", "message": code.replace("_", " ").title()},
                latency_ms=elapsed_ms,
            )

        if outcome == ProcessorResultStatus.HARD_DECLINE:
            code = forced[1] if forced else random.choice(self._hard_codes)
            return ProcessorResult(
                processor_name=self.name,
                status=ProcessorResultStatus.HARD_DECLINE,
                decline_code=code,
                decline_type=DeclineType.HARD,
                raw_response={"code": "05", "message": code.replace("_", " ").title()},
                latency_ms=elapsed_ms,
            )

        if outcome == ProcessorResultStatus.RATE_LIMITED:
            return ProcessorResult(
                processor_name=self.name,
                status=ProcessorResultStatus.RATE_LIMITED,
                decline_code="rate_limit_exceeded",
                decline_type=DeclineType.RATE_LIMIT,
                raw_response={"code": "429", "message": "Rate limit exceeded"},
                latency_ms=elapsed_ms,
            )

        # TIMEOUT — caller's wait_for will fire before this resolves
        await asyncio.sleep(60)
        return ProcessorResult(
            processor_name=self.name,
            status=ProcessorResultStatus.TIMEOUT,
            raw_response={"code": "timeout", "message": "Connection timed out"},
            latency_ms=elapsed_ms,
        )
