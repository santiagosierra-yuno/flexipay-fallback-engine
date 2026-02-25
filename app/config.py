from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Circuit Breaker
    CB_ROLLING_WINDOW_SIZE: int = 50
    CB_ROLLING_WINDOW_SECONDS: float = 300.0  # 5 minutes
    CB_TRIP_THRESHOLD: float = 0.20           # trip below 20% success rate
    CB_COOLDOWN_SECONDS: float = 120.0        # 2-minute cooldown

    # Exponential Backoff (for RATE_LIMITED)
    BACKOFF_BASE_SECONDS: float = 0.5
    BACKOFF_MAX_SECONDS: float = 30.0
    BACKOFF_MAX_RETRIES: int = 2

    # Per-processor call timeout
    PROCESSOR_TIMEOUT_SECONDS: float = 3.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
