from pydantic import BaseModel
from decimal import Decimal
from typing import Dict


class ProcessorStats(BaseModel):
    processor_name: str
    transaction_count: int
    total_volume: Decimal
    total_fees: Decimal
    success_count: int
    hard_decline_count: int
    soft_decline_count: int
    timeout_count: int
    rate_limited_count: int
    avg_latency_ms: float


class StatsResponse(BaseModel):
    total_transactions: int
    total_approved: int
    total_declined: int
    total_volume: Decimal
    total_fees_collected: Decimal
    overall_approval_rate: float
    per_processor: Dict[str, ProcessorStats]
    uptime_seconds: float
