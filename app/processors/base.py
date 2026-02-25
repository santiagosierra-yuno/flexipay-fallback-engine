from abc import ABC, abstractmethod
from app.models.transaction import TransactionRequest
from app.models.processor import ProcessorResult


class AbstractProcessor(ABC):
    name: str
    fee_rate: float  # decimal fraction, e.g. 0.025 = 2.5%

    @abstractmethod
    async def charge(self, request: TransactionRequest) -> ProcessorResult:
        """
        Attempt to charge the given transaction.
        Never raises — all error conditions are encoded in ProcessorResult.status.
        """

    @property
    def display_name(self) -> str:
        return self.name
