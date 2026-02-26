"""
API-level integration tests for the FlexiPay Fallback Engine.

Uses FastAPI's TestClient (synchronous) so no pytest-asyncio is needed.
The lifespan context manager (startup/shutdown) runs automatically when
the client is used as a context manager.

Test cards used for deterministic outcomes:
  "0000" -> HARD_DECLINE (fraud_detected) on all processors
  "1111" -> SOFT_DECLINE (insufficient_funds) on all processors
  "4242" -> random outcome (only used where shape, not outcome, is asserted)
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

# ---------------------------------------------------------------------------
# Shared client — lifespan runs once for the whole module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# Base payload used as a starting point; tests override individual fields.
_BASE = {
    "transaction_id": "test-placeholder",
    "amount": "100.00",
    "currency": "BRL",
    "merchant_id": "test-merchant",
    "card_last_four": "4242",
}


def _txn(**overrides) -> dict:
    """Return a copy of _BASE with the given overrides applied."""
    return {**_BASE, **overrides}


# ---------------------------------------------------------------------------
# 1. POST /transactions — 200 with correct response shape
# ---------------------------------------------------------------------------

def test_post_transaction_response_shape(client):
    """
    A transaction with card 0000 always hard-declines on the first processor.
    We use it here because it is fast and deterministic, letting us assert
    the full response shape without flakiness.
    """
    payload = _txn(transaction_id="shape-001", card_last_four="0000")
    r = client.post("/transactions", json=payload)

    assert r.status_code == 200
    data = r.json()

    # Required top-level fields
    assert data["transaction_id"] == "shape-001"
    assert data["status"] in ("approved", "declined")
    assert "amount" in data
    assert "currency" in data
    assert isinstance(data["attempts"], int) and data["attempts"] >= 1
    assert isinstance(data["processors_tried"], list)
    assert isinstance(data["latency_ms"], float)
    assert "processed_at" in data

    # Hard-decline specific assertions (card 0000)
    assert data["status"] == "declined"
    assert data["decline_type"] == "hard"
    assert data["decline_reason"] == "fraud_detected"
    assert data["attempts"] == 1  # stopped immediately, no fallback


# ---------------------------------------------------------------------------
# 2. POST /transactions — invalid transaction_id (special characters)
# ---------------------------------------------------------------------------

def test_post_transaction_invalid_txn_id(client):
    """transaction_id only allows word chars, hyphens, and underscores."""
    r = client.post("/transactions", json=_txn(transaction_id="txn@#!bad"))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 3. POST /transactions — amount = 0
# ---------------------------------------------------------------------------

def test_post_transaction_amount_zero(client):
    r = client.post("/transactions", json=_txn(transaction_id="zero-amount", amount="0.00"))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 4. POST /transactions — unsupported currency
# ---------------------------------------------------------------------------

def test_post_transaction_unsupported_currency(client):
    """EUR is not in the Currency enum (only BRL, USD, MXN are supported)."""
    r = client.post("/transactions", json=_txn(transaction_id="bad-currency", currency="EUR"))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 5. POST /transactions — metadata exceeding 1 KB
# ---------------------------------------------------------------------------

def test_post_transaction_metadata_too_large(client):
    """JSON-serialised metadata must not exceed 1 024 bytes."""
    big_metadata = {"key": "x" * 1025}  # serialises to ~1 036 bytes
    r = client.post(
        "/transactions",
        json=_txn(transaction_id="big-meta", metadata=big_metadata),
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 6. GET /processors/status — list of 3 processors with correct fields
# ---------------------------------------------------------------------------

def test_get_processor_status_shape(client):
    r = client.get("/processors/status")
    assert r.status_code == 200

    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 3

    names = {p["name"] for p in data}
    assert names == {"VortexPay", "SwiftPay", "PixFlow"}

    required_fields = {
        "name", "state", "fee_rate",
        "total_calls_in_window",
        "successful_calls_in_window",
        "failed_calls_in_window",
    }
    for processor in data:
        assert required_fields.issubset(processor.keys()), (
            f"Processor {processor.get('name')} is missing fields: "
            f"{required_fields - processor.keys()}"
        )
        assert processor["state"] in ("closed", "open", "half_open")
        assert isinstance(processor["fee_rate"], float)


# ---------------------------------------------------------------------------
# 7. GET /stats — correct aggregate shape
# ---------------------------------------------------------------------------

def test_get_stats_shape(client):
    r = client.get("/stats")
    assert r.status_code == 200

    data = r.json()
    required_fields = {
        "total_transactions",
        "total_approved",
        "total_declined",
        "overall_approval_rate",
        "total_volume",
        "total_fees_collected",
        "per_processor",
        "uptime_seconds",
    }
    assert required_fields.issubset(data.keys())
    assert isinstance(data["per_processor"], dict)
    assert data["total_transactions"] >= 0
    assert 0.0 <= data["overall_approval_rate"] <= 1.0


# ---------------------------------------------------------------------------
# 8. POST /processors/UnknownProcessor/reset — 404
# ---------------------------------------------------------------------------

def test_reset_unknown_processor_returns_404(client):
    r = client.post("/processors/UnknownProcessor/reset")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 9. POST /processors/VortexPay/inject-failures?count=0 — 422
# ---------------------------------------------------------------------------

def test_inject_failures_count_zero_returns_422(client):
    """count must be between 1 and 200; 0 should be rejected."""
    r = client.post("/processors/VortexPay/inject-failures?count=0")
    assert r.status_code == 422
