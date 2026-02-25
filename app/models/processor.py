from pydantic import BaseModel
from enum import Enum
from typing import Optional
from decimal import Decimal


class DeclineType(str, Enum):
    SOFT = "soft"
    HARD = "hard"
    RATE_LIMIT = "rate_limit"


class ProcessorResultStatus(str, Enum):
    SUCCESS = "success"
    SOFT_DECLINE = "soft_decline"
    HARD_DECLINE = "hard_decline"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"  # CB refused the call before it was made


class ProcessorResult(BaseModel):
    processor_name: str
    status: ProcessorResultStatus
    amount: Optional[Decimal] = None
    fee: Optional[Decimal] = None
    fee_rate: Optional[float] = None
    decline_code: Optional[str] = None
    decline_type: Optional[DeclineType] = None
    raw_response: dict = {}
    latency_ms: float = 0.0


class CircuitBreakerState(str, Enum):
    CLOSED = "closed"       # healthy, passing calls through
    OPEN = "open"           # tripped, rejecting all calls
    HALF_OPEN = "half_open" # cooldown elapsed, probing with one call


class ProcessorStatusResponse(BaseModel):
    name: str
    state: CircuitBreakerState
    success_rate: Optional[float] = None
    total_calls_in_window: int
    successful_calls_in_window: int
    failed_calls_in_window: int
    last_failure_at: Optional[str] = None
    cooldown_remaining_seconds: Optional[float] = None
    fee_rate: float
