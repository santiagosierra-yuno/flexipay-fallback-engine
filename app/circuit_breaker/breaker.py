import time
import threading
from collections import deque

from app.models.processor import CircuitBreakerState


class CircuitBreaker:
    """
    Dual-constraint rolling window circuit breaker.

    A sample is evicted if it is either:
      - older than window_seconds, OR
      - beyond the most recent window_size samples

    States:
      CLOSED    -> all requests pass through
      OPEN      -> all requests rejected (cooldown running)
      HALF_OPEN -> cooldown elapsed; exactly one probe allowed through

    Trip condition: success_rate < trip_threshold (default 20%)
    """

    def __init__(
        self,
        name: str,
        window_size: int = 50,
        window_seconds: float = 300.0,
        trip_threshold: float = 0.20,
        cooldown_seconds: float = 120.0,
    ):
        self.name = name
        self._window_size = window_size
        self._window_seconds = window_seconds
        self._trip_threshold = trip_threshold
        self._cooldown_seconds = cooldown_seconds

        # Each entry: (timestamp: float, success: bool)
        self._window: deque[tuple[float, bool]] = deque()
        self._lock = threading.Lock()

        self._state = CircuitBreakerState.CLOSED
        self._opened_at: float | None = None
        self._last_failure_at: float | None = None
        self._half_open_probe_in_flight = False

    def allow_request(self) -> bool:
        """
        Returns True if a request should be allowed through.
        Side effect: transitions OPEN -> HALF_OPEN when cooldown elapses.
        """
        with self._lock:
            if self._state == CircuitBreakerState.CLOSED:
                return True

            if self._state == CircuitBreakerState.OPEN:
                if self._opened_at is not None:
                    elapsed = time.monotonic() - self._opened_at
                    if elapsed >= self._cooldown_seconds:
                        # Transition to HALF_OPEN and allow one probe
                        self._state = CircuitBreakerState.HALF_OPEN
                        self._half_open_probe_in_flight = True
                        return True
                return False

            if self._state == CircuitBreakerState.HALF_OPEN:
                # Only allow one probe at a time
                if not self._half_open_probe_in_flight:
                    self._half_open_probe_in_flight = True
                    return True
                return False

        return False

    def record_success(self) -> None:
        with self._lock:
            self._add_sample(success=True)
            if self._state == CircuitBreakerState.HALF_OPEN:
                # Probe succeeded — recover to CLOSED
                self._state = CircuitBreakerState.CLOSED
                self._opened_at = None
                self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._last_failure_at = time.monotonic()
            self._add_sample(success=False)

            if self._state == CircuitBreakerState.HALF_OPEN:
                # Probe failed — go back to OPEN and reset cooldown
                self._state = CircuitBreakerState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_probe_in_flight = False
            elif self._state == CircuitBreakerState.CLOSED:
                self._evaluate_trip()

    def _add_sample(self, success: bool) -> None:
        now = time.monotonic()
        self._window.append((now, success))
        self._evict_stale(now)

    def _evict_stale(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()
        while len(self._window) > self._window_size:
            self._window.popleft()

    def _evaluate_trip(self) -> None:
        if len(self._window) < 5:
            # Need at least 5 samples before tripping
            return
        success_count = sum(1 for _, s in self._window if s)
        rate = success_count / len(self._window)
        if rate < self._trip_threshold:
            self._state = CircuitBreakerState.OPEN
            self._opened_at = time.monotonic()

    def reset(self) -> None:
        """Reset to CLOSED state with an empty window (for testing / admin)."""
        with self._lock:
            self._window.clear()
            self._state = CircuitBreakerState.CLOSED
            self._opened_at = None
            self._last_failure_at = None
            self._half_open_probe_in_flight = False

    def inject_failures(self, count: int) -> None:
        """
        Inject *count* synthetic failures into the rolling window.
        If the resulting success-rate drops below the trip threshold the
        circuit breaker transitions to OPEN immediately.
        Intended for demo / integration-testing only.
        """
        with self._lock:
            for _ in range(count):
                self._add_sample(success=False)
            self._last_failure_at = time.monotonic()
            if self._state == CircuitBreakerState.CLOSED:
                self._evaluate_trip()

    @property
    def status_snapshot(self) -> dict:
        """Thread-safe snapshot for the /processors/status endpoint."""
        with self._lock:
            now = time.monotonic()
            self._evict_stale(now)
            total = len(self._window)
            successes = sum(1 for _, s in self._window if s)
            failures = total - successes
            rate = (successes / total) if total > 0 else None

            cooldown_remaining = None
            if self._state == CircuitBreakerState.OPEN and self._opened_at is not None:
                elapsed = now - self._opened_at
                cooldown_remaining = max(0.0, self._cooldown_seconds - elapsed)

            last_failure = None
            if self._last_failure_at is not None:
                seconds_ago = now - self._last_failure_at
                last_failure = f"{seconds_ago:.1f}s ago"

            return {
                "state": self._state,
                "success_rate": rate,
                "total_calls_in_window": total,
                "successful_calls_in_window": successes,
                "failed_calls_in_window": failures,
                "last_failure_at": last_failure,
                "cooldown_remaining_seconds": cooldown_remaining,
            }
