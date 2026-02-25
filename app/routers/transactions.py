from fastapi import APIRouter, Request
from app.models.transaction import TransactionRequest, TransactionResponse

router = APIRouter()


@router.post("/transactions", response_model=TransactionResponse)
async def create_transaction(
    body: TransactionRequest,
    request: Request,
) -> TransactionResponse:
    """
    Process a payment transaction with automatic processor fallback.

    - Attempts VortexPay first (cheapest).
    - On soft decline/timeout, falls back to SwiftPay, then PixFlow.
    - On hard decline (fraud, stolen card, etc.), stops immediately.
    - Circuit breaker skips unhealthy processors automatically.
    """
    engine = request.app.state.fallback_engine
    return await engine.process(body)
