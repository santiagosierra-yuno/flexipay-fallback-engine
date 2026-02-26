import json

from pydantic import BaseModel, Field, field_validator
from decimal import Decimal
from enum import Enum
from datetime import datetime
from typing import Literal, Optional


class Currency(str, Enum):
    BRL = "BRL"
    USD = "USD"
    MXN = "MXN"


class TransactionRequest(BaseModel):
    transaction_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[\w\-]+$",
        description="Client-supplied idempotency key (alphanumeric, hyphens, underscores)",
    )
    amount: Decimal = Field(..., gt=0, le=1_000_000, decimal_places=2)
    currency: Currency = Currency.BRL
    merchant_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[\w\-]+$",
        description="Merchant identifier (alphanumeric, hyphens, underscores)",
    )
    card_last_four: str = Field(..., pattern=r"^\d{4}$")
    metadata: dict = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def limit_metadata_size(cls, v: dict) -> dict:
        if len(json.dumps(v)) > 1024:
            raise ValueError("metadata must not exceed 1 KB")
        return v


class TransactionResponse(BaseModel):
    transaction_id: str
    status: Literal["approved", "declined"]
    processor_used: Optional[str] = None
    amount: Decimal
    currency: str
    fee: Optional[Decimal] = None
    fee_rate: Optional[float] = None
    decline_reason: Optional[str] = None
    decline_type: Optional[str] = None  # "soft" | "hard"
    attempts: int
    processors_tried: list[str] = Field(default_factory=list)
    retry_log: list[str] = Field(default_factory=list)
    latency_ms: float
    processed_at: datetime
