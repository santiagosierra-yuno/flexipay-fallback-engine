import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.circuit_breaker.registry import CircuitBreakerRegistry
from app.engine.fallback_engine import FallbackEngine
from app.processors.vortex_pay import VortexPay
from app.processors.swift_pay import SwiftPay
from app.processors.pix_flow import PixFlow
from app.routers import transactions, processors, stats
from app.services.stats_service import StatsService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info("FlexiPay Fallback Engine starting up...")

    processor_list = [VortexPay(), SwiftPay(), PixFlow()]
    cb_registry = CircuitBreakerRegistry(settings)
    stats_service = StatsService()

    # Pre-register circuit breakers so /processors/status works before any traffic
    for p in processor_list:
        cb_registry.get(p.name)

    fallback_engine = FallbackEngine(
        processors=processor_list,
        cb_registry=cb_registry,
        stats_service=stats_service,
        settings=settings,
    )

    app.state.processors = processor_list
    app.state.cb_registry = cb_registry
    app.state.stats_service = stats_service
    app.state.fallback_engine = fallback_engine

    logger.info(
        f"Processors loaded: {[p.name for p in processor_list]} | "
        f"CB window={settings.CB_ROLLING_WINDOW_SIZE} txns / "
        f"{settings.CB_ROLLING_WINDOW_SECONDS}s | "
        f"trip_threshold={settings.CB_TRIP_THRESHOLD:.0%} | "
        f"cooldown={settings.CB_COOLDOWN_SECONDS}s"
    )

    yield

    # --- Shutdown ---
    logger.info("FlexiPay Fallback Engine shutting down.")
    snap = stats_service.snapshot()
    logger.info(
        f"Final stats: {snap.total_transactions} transactions | "
        f"{snap.total_approved} approved | "
        f"{snap.overall_approval_rate:.1%} approval rate | "
        f"${snap.total_fees_collected} fees"
    )


app = FastAPI(
    title="FlexiPay Processor Fallback Engine",
    description=(
        "Intelligent payment processor fallback engine with circuit breaking, "
        "cost-aware routing, and exponential backoff. Built for Yuno's orchestration platform."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(transactions.router, tags=["Transactions"])
app.include_router(processors.router, tags=["Processor Health"])
app.include_router(stats.router, tags=["Statistics"])


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again later."},
    )


@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "FlexiPay Processor Fallback Engine",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
    }
