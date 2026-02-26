from app.models.processor import ProcessorResultStatus
from app.processors.mock_processor import MockableProcessor


class VortexPay(MockableProcessor):
    """Primary processor — cheapest (2.5 %) but least reliable."""

    def __init__(self) -> None:
        super().__init__(
            name="VortexPay",
            fee_rate=0.025,
            latency_range=(0.020, 0.180),
            outcome_table=[
                (0.68, ProcessorResultStatus.SUCCESS),
                (0.12, ProcessorResultStatus.SOFT_DECLINE),
                (0.07, ProcessorResultStatus.HARD_DECLINE),
                (0.08, ProcessorResultStatus.RATE_LIMITED),
                (0.05, ProcessorResultStatus.TIMEOUT),
            ],
            soft_codes=[
                "insufficient_funds",
                "limit_exceeded",
                "processor_unavailable",
            ],
            hard_codes=[
                "stolen_card",
                "do_not_honor",
                "invalid_account",
                "fraud_detected",
                "invalid_cvv",
                "card_expired",
            ],
            card_overrides={
                "0000": (ProcessorResultStatus.HARD_DECLINE, "fraud_detected"),
                "1111": (ProcessorResultStatus.SOFT_DECLINE, "insufficient_funds"),
                "9999": (ProcessorResultStatus.TIMEOUT, None),
            },
        )
