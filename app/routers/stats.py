from fastapi import APIRouter, Request
from app.models.stats import StatsResponse

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
async def get_stats(request: Request) -> StatsResponse:
    """
    Returns aggregated statistics since service startup:
    - Total transactions, approval rate
    - Total volume and fees collected
    - Per-processor breakdown
    """
    return request.app.state.stats_service.snapshot()
