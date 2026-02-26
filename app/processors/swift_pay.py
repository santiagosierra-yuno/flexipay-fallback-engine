from app.models.processor import ProcessorResultStatus
from app.processors.mock_processor import MockableProcessor


class SwiftPay(MockableProcessor):
    """Secondary processor — more reliable (2.9 %), first fallback."""

    def __init__(self) -> None:
        super().__init__(
            name="SwiftPay",
            fee_rate=0.029,
            latency_range=(0.030, 0.200),
            outcome_table=[
                (0.74, ProcessorResultStatus.SUCCESS),
                (0.10, ProcessorResultStatus.SOFT_DECLINE),
                (0.06, ProcessorResultStatus.HARD_DECLINE),
                (0.06, ProcessorResultStatus.RATE_LIMITED),
                (0.04, ProcessorResultStatus.TIMEOUT),
            ],
            soft_codes=[
                "insufficient_funds",
                "processor_timeout",
                "temporary_unavailable",
            ],
            hard_codes=[
                "stolen_card",
                "do_not_honor",
                "fraud_detected",
                "invalid_card_number",
                "card_expired",
            ],
            card_overrides={
                "0000": (ProcessorResultStatus.HARD_DECLINE, "fraud_detected"),
                "1111": (ProcessorResultStatus.SOFT_DECLINE, "insufficient_funds"),
                "9999": (ProcessorResultStatus.TIMEOUT, None),
            },
        )
