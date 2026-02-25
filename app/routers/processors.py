from fastapi import APIRouter, HTTPException, Request
from app.models.processor import ProcessorStatusResponse

router = APIRouter()


@router.get("/processors/status", response_model=list[ProcessorStatusResponse])
async def get_processor_status(request: Request) -> list[ProcessorStatusResponse]:
    """
    Returns the current health status of all processors including:
    - Circuit breaker state (closed / open / half_open)
    - Success rate in the rolling window
    - Number of calls tracked
    - Time remaining on cooldown (if circuit is open)
    """
    cb_registry = request.app.state.cb_registry
    processors = request.app.state.processors

    results = []
    for p in processors:
        cb = cb_registry.get(p.name)
        snap = cb.status_snapshot
        results.append(
            ProcessorStatusResponse(
                name=p.name,
                fee_rate=p.fee_rate,
                **snap,
            )
        )
    return results


def _get_cb_or_404(name: str, request: Request):
    known = {p.name for p in request.app.state.processors}
    if name not in known:
        raise HTTPException(status_code=404, detail=f"Processor '{name}' not found")
    return request.app.state.cb_registry.get(name)


@router.post(
    "/processors/{name}/reset",
    tags=["Testing"],
    summary="Reset a processor's circuit breaker to CLOSED with an empty window",
)
async def reset_circuit_breaker(name: str, request: Request) -> dict:
    cb = _get_cb_or_404(name, request)
    cb.reset()
    return {"processor": name, "action": "reset", "state": "closed"}


@router.post(
    "/processors/{name}/inject-failures",
    tags=["Testing"],
    summary="Inject synthetic failures into a processor's CB window",
)
async def inject_failures(name: str, count: int, request: Request) -> dict:
    """
    Records *count* failure samples directly into the circuit breaker's
    rolling window.  If the resulting success-rate drops below the trip
    threshold the circuit breaker opens immediately.

    Use this endpoint (together with /processors/{name}/reset) to
    deterministically demonstrate circuit-breaker behaviour in demos
    and integration tests.
    """
    if count < 1 or count > 200:
        raise HTTPException(status_code=422, detail="count must be between 1 and 200")
    cb = _get_cb_or_404(name, request)
    cb.inject_failures(count)
    snap = cb.status_snapshot
    return {
        "processor": name,
        "injected_failures": count,
        "state": snap["state"],
        "success_rate": snap["success_rate"],
        "total_calls_in_window": snap["total_calls_in_window"],
    }
