from app.models.processor import ProcessorResultStatus
from app.processors.mock_processor import MockableProcessor


class PixFlow(MockableProcessor):
    """Tertiary processor — most reliable (3.2 %), last-resort fallback."""

    def __init__(self) -> None:
        super().__init__(
            name="PixFlow",
            fee_rate=0.032,
            latency_range=(0.050, 0.250),
            outcome_table=[
                (0.82, ProcessorResultStatus.SUCCESS),
                (0.08, ProcessorResultStatus.SOFT_DECLINE),
                (0.05, ProcessorResultStatus.HARD_DECLINE),
                (0.03, ProcessorResultStatus.RATE_LIMITED),
                (0.02, ProcessorResultStatus.TIMEOUT),
            ],
            soft_codes=[
                "insufficient_funds",
                "account_frozen",
                "pix_limit_exceeded",
                "temporary_unavailable",
            ],
            hard_codes=[
                "stolen_card",
                "do_not_honor",
                "fraud_detected",
                "invalid_pix_key",
            ],
            card_overrides={
                "0000": (ProcessorResultStatus.HARD_DECLINE, "fraud_detected"),
                "1111": (ProcessorResultStatus.SOFT_DECLINE, "insufficient_funds"),
                "9999": (ProcessorResultStatus.TIMEOUT, None),
            },
        )
