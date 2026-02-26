import asyncio
import logging
import threading
import time
from datetime import datetime, timezone

from app.processors.base import AbstractProcessor
from app.circuit_breaker.registry import CircuitBreakerRegistry
from app.models.transaction import Currency, TransactionRequest, TransactionResponse
from app.models.processor import ProcessorResult, ProcessorResultStatus
from app.engine.backoff import exponential_backoff
from app.services.stats_service import StatsService
from app.config import Settings

logger = logging.getLogger(__name__)

_IDEMPOTENCY_TTL_SECONDS = 86_400  # 24 hours


class FallbackEngine:
    """
    Orchestrates the processor chain: VortexPay -> SwiftPay -> PixFlow.

    Routing rules:
      SUCCESS        -> stop immediately, return approved
      HARD_DECLINE   -> stop immediately, return declined (no fallback)
      SOFT_DECLINE   -> move to next processor
      TIMEOUT        -> move to next processor
      RATE_LIMITED   -> exponential backoff, retry same processor up to
                        BACKOFF_MAX_RETRIES times, then move to next processor
      CIRCUIT_OPEN   -> skip processor entirely, move to next
      All exhausted  -> return declined

    Idempotency:
      If the same transaction_id is submitted again within 24 hours the
      cached TransactionResponse is returned immediately without hitting
      any processor.
    """

    def __init__(
        self,
        processors: list[AbstractProcessor],
        cb_registry: CircuitBreakerRegistry,
        stats_service: StatsService,
        settings: Settings,
    ):
        self._processors = processors
        self._cb_registry = cb_registry
        self._stats = stats_service
        self._settings = settings
        # Idempotency cache: transaction_id -> (cached_at: float, TransactionResponse)
        self._idempotency_cache: dict[str, tuple[float, TransactionResponse]] = {}
        self._cache_lock = threading.Lock()

    def _get_cached(self, transaction_id: str) -> TransactionResponse | None:
        with self._cache_lock:
            entry = self._idempotency_cache.get(transaction_id)
            if entry is None:
                return None
            cached_at, response = entry
            if time.monotonic() - cached_at > _IDEMPOTENCY_TTL_SECONDS:
                del self._idempotency_cache[transaction_id]
                return None
            return response

    def _store_cached(self, transaction_id: str, response: TransactionResponse) -> None:
        with self._cache_lock:
            self._idempotency_cache[transaction_id] = (time.monotonic(), response)

    async def process(self, request: TransactionRequest) -> TransactionResponse:
        cached = self._get_cached(request.transaction_id)
        if cached is not None:
            logger.info(
                f"[TXN {request.transaction_id}] Idempotent replay — returning cached response"
            )
            return cached
        start = time.monotonic()
        attempts = 0
        processors_tried: list[str] = []
        retry_log: list[str] = []
        last_result: ProcessorResult | None = None

        # Currency-aware routing: BRL transactions are routed to PixFlow first because
        # PixFlow supports PIX natively, giving it a structural conversion advantage
        # for Brazilian Real payments.  All other currencies use cost-aware ordering
        # (cheapest processor first).
        if request.currency == Currency.BRL:
            pix = [p for p in self._processors if p.name == "PixFlow"]
            rest = sorted(
                [p for p in self._processors if p.name != "PixFlow"],
                key=lambda p: p.fee_rate,
            )
            ordered_processors = pix + rest
        else:
            ordered_processors = sorted(self._processors, key=lambda p: p.fee_rate)

        logger.info(
            f"[TXN {request.transaction_id}] Processing {request.amount} {request.currency} "
            f"| chain: {[p.name for p in ordered_processors]}"
        )

        for processor in ordered_processors:
            cb = self._cb_registry.get(processor.name)

            # --- Circuit Breaker Guard ---
            if not cb.allow_request():
                logger.warning(
                    f"[TXN {request.transaction_id}] [{processor.name}] Circuit OPEN — skipping"
                )
                last_result = ProcessorResult(
                    processor_name=processor.name,
                    status=ProcessorResultStatus.CIRCUIT_OPEN,
                )
                processors_tried.append(f"{processor.name}(circuit_open)")
                continue

            # --- Rate Limit Backoff Loop ---
            for backoff_attempt in range(self._settings.BACKOFF_MAX_RETRIES + 1):
                if backoff_attempt > 0:
                    delay = await exponential_backoff(
                        backoff_attempt - 1,
                        base=self._settings.BACKOFF_BASE_SECONDS,
                        cap=self._settings.BACKOFF_MAX_SECONDS,
                    )
                    logger.info(
                        f"[TXN {request.transaction_id}] [{processor.name}] "
                        f"Backoff retry #{backoff_attempt} after {delay:.2f}s"
                    )
                    retry_log.append(f"{processor.name}: rate_limited, backoff {delay:.2f}s")

                attempts += 1

                try:
                    result = await asyncio.wait_for(
                        processor.charge(request),
                        timeout=self._settings.PROCESSOR_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[TXN {request.transaction_id}] [{processor.name}] "
                        f"Timed out after {self._settings.PROCESSOR_TIMEOUT_SECONDS}s"
                    )
                    result = ProcessorResult(
                        processor_name=processor.name,
                        status=ProcessorResultStatus.TIMEOUT,
                        latency_ms=self._settings.PROCESSOR_TIMEOUT_SECONDS * 1000,
                    )

                last_result = result
                self._stats.record_attempt(result)

                logger.info(
                    f"[TXN {request.transaction_id}] [{processor.name}] "
                    f"attempt={attempts} status={result.status.value} "
                    f"decline_code={result.decline_code} "
                    f"latency={result.latency_ms:.1f}ms"
                )

                # --- Outcome Routing ---
                if result.status == ProcessorResultStatus.SUCCESS:
                    cb.record_success()
                    processors_tried.append(f"{processor.name}(success)")
                    self._stats.record_final(approved=True, amount=request.amount, fee=result.fee)
                    total_latency_ms = (time.monotonic() - start) * 1000
                    logger.info(
                        f"[TXN {request.transaction_id}] APPROVED via {processor.name} "
                        f"after {attempts} attempt(s) | total latency={total_latency_ms:.1f}ms"
                    )
                    response = TransactionResponse(
                        transaction_id=request.transaction_id,
                        status="approved",
                        processor_used=processor.name,
                        amount=request.amount,
                        currency=request.currency.value,
                        fee=result.fee,
                        fee_rate=result.fee_rate,
                        attempts=attempts,
                        processors_tried=processors_tried,
                        retry_log=retry_log,
                        latency_ms=round(total_latency_ms, 2),
                        processed_at=datetime.now(timezone.utc),
                    )
                    self._store_cached(request.transaction_id, response)
                    return response

                elif result.status == ProcessorResultStatus.HARD_DECLINE:
                    cb.record_failure()
                    processors_tried.append(f"{processor.name}(hard_decline:{result.decline_code})")
                    self._stats.record_final(approved=False, amount=request.amount, fee=None)
                    total_latency_ms = (time.monotonic() - start) * 1000
                    logger.warning(
                        f"[TXN {request.transaction_id}] HARD DECLINE from {processor.name} "
                        f"code={result.decline_code} — NOT retrying"
                    )
                    response = TransactionResponse(
                        transaction_id=request.transaction_id,
                        status="declined",
                        processor_used=processor.name,
                        amount=request.amount,
                        currency=request.currency.value,
                        decline_reason=result.decline_code,
                        decline_type="hard",
                        attempts=attempts,
                        processors_tried=processors_tried,
                        retry_log=retry_log,
                        latency_ms=round(total_latency_ms, 2),
                        processed_at=datetime.now(timezone.utc),
                    )
                    self._store_cached(request.transaction_id, response)
                    return response

                elif result.status == ProcessorResultStatus.RATE_LIMITED:
                    cb.record_failure()
                    if backoff_attempt < self._settings.BACKOFF_MAX_RETRIES:
                        processors_tried.append(f"{processor.name}(rate_limited:retry_{backoff_attempt+1})")
                        continue  # retry same processor with backoff
                    else:
                        processors_tried.append(f"{processor.name}(rate_limited:exhausted)")
                        logger.warning(
                            f"[TXN {request.transaction_id}] [{processor.name}] "
                            f"Rate limit retries exhausted — falling through"
                        )
                        break  # move to next processor

                else:  # SOFT_DECLINE or TIMEOUT
                    cb.record_failure()
                    processors_tried.append(
                        f"{processor.name}({result.status.value}:{result.decline_code or 'n/a'})"
                    )
                    logger.info(
                        f"[TXN {request.transaction_id}] [{processor.name}] "
                        f"Soft failure ({result.status.value}) — trying next processor"
                    )
                    break  # move to next processor

        # All processors exhausted
        self._stats.record_final(approved=False, amount=request.amount, fee=None)
        total_latency_ms = (time.monotonic() - start) * 1000
        logger.error(
            f"[TXN {request.transaction_id}] ALL PROCESSORS FAILED after {attempts} attempts"
        )

        decline_reason = last_result.decline_code if last_result else "all_processors_failed"
        decline_type = (
            last_result.decline_type.value
            if last_result and last_result.decline_type
            else "soft"
        )

        response = TransactionResponse(
            transaction_id=request.transaction_id,
            status="declined",
            amount=request.amount,
            currency=request.currency.value,
            decline_reason=decline_reason or "all_processors_failed",
            decline_type=decline_type,
            attempts=attempts,
            processors_tried=processors_tried,
            retry_log=retry_log,
            latency_ms=round(total_latency_ms, 2),
            processed_at=datetime.now(timezone.utc),
        )
        self._store_cached(request.transaction_id, response)
        return response
