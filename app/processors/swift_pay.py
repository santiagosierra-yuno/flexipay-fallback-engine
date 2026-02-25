import asyncio
import random
import time

from app.processors.base import AbstractProcessor
from app.models.transaction import TransactionRequest
from app.models.processor import ProcessorResult, ProcessorResultStatus, DeclineType

# SwiftPay: slightly more reliable than VortexPay, slightly more expensive
_OUTCOMES = [
    (0.74, ProcessorResultStatus.SUCCESS),
    (0.10, ProcessorResultStatus.SOFT_DECLINE),
    (0.06, ProcessorResultStatus.HARD_DECLINE),
    (0.06, ProcessorResultStatus.RATE_LIMITED),
    (0.04, ProcessorResultStatus.TIMEOUT),
]

_SOFT_CODES = [
    "insufficient_funds",
    "card_expired",
    "processor_timeout",
    "temporary_unavailable",
]

_HARD_CODES = [
    "stolen_card",
    "do_not_honor",
    "fraud_detected",
    "invalid_card_number",
]


def _pick_outcome() -> ProcessorResultStatus:
    r = random.random()
    cumulative = 0.0
    for prob, outcome in _OUTCOMES:
        cumulative += prob
        if r < cumulative:
            return outcome
    return ProcessorResultStatus.SUCCESS


class SwiftPay(AbstractProcessor):
    name = "SwiftPay"
    fee_rate = 0.029  # 2.9%

    async def charge(self, request: TransactionRequest) -> ProcessorResult:
        start = time.monotonic()

        # Simulate network latency (30–200ms for SwiftPay)
        latency = random.uniform(0.030, 0.200)
        await asyncio.sleep(latency)

        outcome = _pick_outcome()
        elapsed_ms = (time.monotonic() - start) * 1000

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

        elif outcome == ProcessorResultStatus.SOFT_DECLINE:
            code = random.choice(_SOFT_CODES)
            return ProcessorResult(
                processor_name=self.name,
                status=ProcessorResultStatus.SOFT_DECLINE,
                decline_code=code,
                decline_type=DeclineType.SOFT,
                raw_response={"code": "51", "message": code.replace("_", " ").title()},
                latency_ms=elapsed_ms,
            )

        elif outcome == ProcessorResultStatus.HARD_DECLINE:
            code = random.choice(_HARD_CODES)
            return ProcessorResult(
                processor_name=self.name,
                status=ProcessorResultStatus.HARD_DECLINE,
                decline_code=code,
                decline_type=DeclineType.HARD,
                raw_response={"code": "05", "message": code.replace("_", " ").title()},
                latency_ms=elapsed_ms,
            )

        elif outcome == ProcessorResultStatus.RATE_LIMITED:
            return ProcessorResult(
                processor_name=self.name,
                status=ProcessorResultStatus.RATE_LIMITED,
                decline_code="rate_limit_exceeded",
                decline_type=DeclineType.RATE_LIMIT,
                raw_response={"code": "429", "message": "Rate limit exceeded"},
                latency_ms=elapsed_ms,
            )

        else:  # TIMEOUT
            await asyncio.sleep(60)
            return ProcessorResult(
                processor_name=self.name,
                status=ProcessorResultStatus.TIMEOUT,
                raw_response={"code": "timeout", "message": "Connection timed out"},
                latency_ms=elapsed_ms,
            )
