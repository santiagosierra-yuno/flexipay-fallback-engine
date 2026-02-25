from app.circuit_breaker.breaker import CircuitBreaker
from app.config import Settings


class CircuitBreakerRegistry:
    """
    Stores one CircuitBreaker per processor.
    Created once at app startup and stored on app.state.
    """

    def __init__(self, settings: Settings):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._settings = settings

    def get(self, processor_name: str) -> CircuitBreaker:
        if processor_name not in self._breakers:
            self._breakers[processor_name] = CircuitBreaker(
                name=processor_name,
                window_size=self._settings.CB_ROLLING_WINDOW_SIZE,
                window_seconds=self._settings.CB_ROLLING_WINDOW_SECONDS,
                trip_threshold=self._settings.CB_TRIP_THRESHOLD,
                cooldown_seconds=self._settings.CB_COOLDOWN_SECONDS,
            )
        return self._breakers[processor_name]

    def all_names(self) -> list[str]:
        return list(self._breakers.keys())
