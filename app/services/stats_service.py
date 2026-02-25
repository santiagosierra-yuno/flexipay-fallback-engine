import time
import threading
from collections import defaultdict
from decimal import Decimal

from app.models.processor import ProcessorResult, ProcessorResultStatus
from app.models.stats import StatsResponse, ProcessorStats


class StatsService:
    """
    In-memory accumulator for transaction statistics.
    All mutations are protected by a Lock for thread-safety.

    Trade-off: data is lost on restart. In production, this would
    be backed by Redis or a time-series database.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._started_at = time.monotonic()

        self._total_transactions = 0
        self._total_approved = 0
        self._total_declined = 0
        self._total_volume = Decimal("0")
        self._total_fees = Decimal("0")

        # per_processor stats keyed by processor name
        self._per_processor: dict[str, dict] = defaultdict(lambda: {
            "count": 0,
            "volume": Decimal("0"),
            "fees": Decimal("0"),
            "success": 0,
            "hard_decline": 0,
            "soft_decline": 0,
            "timeout": 0,
            "rate_limited": 0,
            "latency_sum": 0.0,
        })

    def record_attempt(self, result: ProcessorResult) -> None:
        """Called by FallbackEngine after each individual processor attempt."""
        with self._lock:
            p = self._per_processor[result.processor_name]
            p["count"] += 1
            p["latency_sum"] += result.latency_ms

            if result.status == ProcessorResultStatus.SUCCESS:
                p["success"] += 1
                if result.amount:
                    p["volume"] += result.amount
                if result.fee:
                    p["fees"] += result.fee
            elif result.status == ProcessorResultStatus.HARD_DECLINE:
                p["hard_decline"] += 1
            elif result.status == ProcessorResultStatus.SOFT_DECLINE:
                p["soft_decline"] += 1
            elif result.status == ProcessorResultStatus.TIMEOUT:
                p["timeout"] += 1
            elif result.status == ProcessorResultStatus.RATE_LIMITED:
                p["rate_limited"] += 1

    def record_final(self, approved: bool, amount: Decimal, fee: Decimal | None) -> None:
        """Called once per transaction with the final outcome."""
        with self._lock:
            self._total_transactions += 1
            if approved:
                self._total_approved += 1
                self._total_volume += amount
                if fee:
                    self._total_fees += fee
            else:
                self._total_declined += 1

    def snapshot(self) -> StatsResponse:
        with self._lock:
            uptime = time.monotonic() - self._started_at
            approval_rate = (
                self._total_approved / self._total_transactions
                if self._total_transactions > 0
                else 0.0
            )

            per_processor = {}
            for name, p in self._per_processor.items():
                avg_latency = p["latency_sum"] / p["count"] if p["count"] > 0 else 0.0
                per_processor[name] = ProcessorStats(
                    processor_name=name,
                    transaction_count=p["count"],
                    total_volume=p["volume"],
                    total_fees=p["fees"],
                    success_count=p["success"],
                    hard_decline_count=p["hard_decline"],
                    soft_decline_count=p["soft_decline"],
                    timeout_count=p["timeout"],
                    rate_limited_count=p["rate_limited"],
                    avg_latency_ms=round(avg_latency, 2),
                )

            return StatsResponse(
                total_transactions=self._total_transactions,
                total_approved=self._total_approved,
                total_declined=self._total_declined,
                total_volume=self._total_volume,
                total_fees_collected=self._total_fees,
                overall_approval_rate=round(approval_rate, 4),
                per_processor=per_processor,
                uptime_seconds=round(uptime, 2),
            )
