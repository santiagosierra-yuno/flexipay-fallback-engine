# FlexiPay Processor Fallback Engine

A production-ready backend service that acts as an intelligent **payment processor fallback engine** for FlexiPay's subscription platform. Built with Python + FastAPI.

---

## What it does

When a customer payment fails, this engine automatically retries through alternative processors based on smart rules:

- **VortexPay** (primary, 2.5% fee) → **SwiftPay** (2.9%) → **PixFlow** (3.2%)
- **Soft declines** (insufficient funds, timeouts, rate limits) → retry with next processor
- **Hard declines** (fraud, stolen card, invalid CVV) → stop immediately, don't retry
- **Circuit breaker** → if a processor is clearly down (< 20% success rate), skip it automatically
- **Cost-aware routing** → always try the cheapest healthy processor first
- **Exponential backoff** → on rate limit errors, wait and retry same processor before falling through

---

## Setup

### Prerequisites

- Python 3.11+
- pip

### Install dependencies

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### Run the service

```bash
./venv/bin/uvicorn app.main:app --reload
```

The service starts at `http://127.0.0.1:8000`

### Interactive API docs

Open `http://127.0.0.1:8000/docs` in your browser — full Swagger UI with all endpoints.

---

## API Reference

### `POST /transactions`

Process a payment transaction with automatic fallback.

**Request body:**
```json
{
  "transaction_id": "txn-abc-123",
  "amount": "199.90",
  "currency": "BRL",
  "merchant_id": "flexipay-studio-sp",
  "card_last_four": "4242",
  "metadata": {}
}
```

**Currencies supported:** `BRL`, `USD`, `MXN`

**Success response (200):**
```json
{
  "transaction_id": "txn-abc-123",
  "status": "approved",
  "processor_used": "VortexPay",
  "amount": "199.90",
  "currency": "BRL",
  "fee": "4.9975",
  "fee_rate": 0.025,
  "decline_reason": null,
  "decline_type": null,
  "attempts": 1,
  "processors_tried": ["VortexPay(success)"],
  "latency_ms": 145.3,
  "processed_at": "2026-02-25T20:00:00Z"
}
```

**Declined response (200):**
```json
{
  "transaction_id": "txn-abc-124",
  "status": "declined",
  "processor_used": "VortexPay",
  "amount": "199.90",
  "currency": "BRL",
  "decline_reason": "fraud_detected",
  "decline_type": "hard",
  "attempts": 1,
  "processors_tried": ["VortexPay(hard_decline:fraud_detected)"],
  "latency_ms": 89.1,
  "processed_at": "2026-02-25T20:00:01Z"
}
```

---

### `GET /processors/status`

Query the current health status of all processors.

**Response:**
```json
[
  {
    "name": "VortexPay",
    "state": "closed",
    "success_rate": 0.72,
    "total_calls_in_window": 25,
    "successful_calls_in_window": 18,
    "failed_calls_in_window": 7,
    "last_failure_at": "12.3s ago",
    "cooldown_remaining_seconds": null,
    "fee_rate": 0.025
  },
  {
    "name": "SwiftPay",
    "state": "open",
    "success_rate": 0.10,
    "total_calls_in_window": 10,
    "successful_calls_in_window": 1,
    "failed_calls_in_window": 9,
    "last_failure_at": "3.1s ago",
    "cooldown_remaining_seconds": 118.5,
    "fee_rate": 0.029
  }
]
```

**Circuit breaker states:**
| State | Meaning |
|---|---|
| `closed` | Healthy — all requests pass through |
| `open` | Tripped — all requests rejected, cooldown running |
| `half_open` | Cooldown elapsed — one probe request allowed to test recovery |

---

### `GET /stats`

Aggregate statistics since service startup.

**Response:**
```json
{
  "total_transactions": 100,
  "total_approved": 72,
  "total_declined": 28,
  "total_volume": "15420.00",
  "total_fees_collected": "385.50",
  "overall_approval_rate": 0.72,
  "per_processor": {
    "VortexPay": {
      "processor_name": "VortexPay",
      "transaction_count": 100,
      "total_volume": "9800.00",
      "total_fees": "245.00",
      "success_count": 68,
      "hard_decline_count": 7,
      "soft_decline_count": 12,
      "timeout_count": 5,
      "rate_limited_count": 8,
      "avg_latency_ms": 102.4
    }
  },
  "uptime_seconds": 432.1
}
```

---

### Test Utility Endpoints (Demo / Testing Only)

> **These endpoints exist solely to make circuit-breaker behaviour deterministic in demos and integration tests. Do not expose them in production.**

#### `POST /processors/{name}/reset`

Resets a processor's circuit breaker to `CLOSED` state with an empty rolling window.

```bash
curl -s -X POST http://localhost:8000/processors/VortexPay/reset
```

```json
{"processor": "VortexPay", "action": "reset", "state": "closed"}
```

#### `POST /processors/{name}/inject-failures?count=N`

Injects `N` synthetic failures into the processor's rolling window. If the resulting success rate drops below the trip threshold (default 20%), the circuit breaker opens immediately.

```bash
curl -s -X POST "http://localhost:8000/processors/VortexPay/inject-failures?count=6"
```

```json
{
  "processor": "VortexPay",
  "injected_failures": 6,
  "state": "open",
  "success_rate": 0.0,
  "total_calls_in_window": 6
}
```

**Typical demo flow:**

```bash
# 1. Reset to a clean state
curl -s -X POST http://localhost:8000/processors/VortexPay/reset

# 2. Inject enough failures to trip the circuit breaker
curl -s -X POST "http://localhost:8000/processors/VortexPay/inject-failures?count=6"

# 3. Send a transaction — VortexPay will be skipped (circuit OPEN)
curl -s -X POST http://localhost:8000/transactions \
  -H "Content-Type: application/json" \
  -d '{"transaction_id":"demo-cb","amount":"100.00","currency":"BRL","merchant_id":"flexipay","card_last_four":"4242"}'
# → processors_tried will show "VortexPay(circuit_open)", fallback to SwiftPay/PixFlow
```

---

## Demo script

Run the full demonstration that exercises all scenarios:

```bash
# Terminal 1: start the server
uvicorn app.main:app --reload

# Terminal 2: run the demo
python tests/demo.py
```

The demo covers:
1. Normal mixed transactions (10 requests)
2. Processor health status check
3. Burst of transactions to trigger circuit breaker
4. Health check after burst (shows circuit open/half_open)
5. Aggregate statistics

---

## Key Scenarios via cURL

### 1. Successful transaction
```bash
curl -s -X POST http://localhost:8000/transactions \
  -H "Content-Type: application/json" \
  -d '{
    "transaction_id": "test-001",
    "amount": "150.00",
    "currency": "BRL",
    "merchant_id": "flexipay",
    "card_last_four": "4242"
  }' | python -m json.tool
```

### 2. Check processor health
```bash
curl -s http://localhost:8000/processors/status | python -m json.tool
```

### 3. Trigger circuit breaker (send 20 rapid requests)
```bash
for i in $(seq 1 20); do
  curl -s -X POST http://localhost:8000/transactions \
    -H "Content-Type: application/json" \
    -d "{
      \"transaction_id\": \"burst-$i\",
      \"amount\": \"75.00\",
      \"currency\": \"BRL\",
      \"merchant_id\": \"flexipay\",
      \"card_last_four\": \"1234\"
    }" | python -c "import sys,json; r=json.load(sys.stdin); print(f'  {r[\"status\"]} | {r[\"processors_tried\"]}')"
done
```

### 4. Check stats
```bash
curl -s http://localhost:8000/stats | python -m json.tool
```

---

## Architecture

```
POST /transactions
      │
      ▼
FallbackEngine.process()
      │
      ├─── Sort processors by fee_rate (cost-aware routing)
      │
      ├─── For each processor in [VortexPay, SwiftPay, PixFlow]:
      │
      │     ┌─ Circuit Breaker Guard ─────────────────────────────┐
      │     │  if CB state == OPEN (and cooldown not elapsed):     │
      │     │      skip processor → try next                       │
      │     └────────────────────────────────────────────────────┘
      │
      │     ┌─ Rate Limit Backoff Loop ──────────────────────────┐
      │     │  for attempt in range(MAX_RETRIES + 1):             │
      │     │      await asyncio.wait_for(processor.charge(), 3s) │
      │     │                                                      │
      │     │      SUCCESS      → record_success(), return ✓      │
      │     │      HARD_DECLINE → record_failure(), return ✗ STOP │
      │     │      RATE_LIMITED → record_failure(), backoff+retry  │
      │     │      SOFT_DECLINE → record_failure(), break → next  │
      │     │      TIMEOUT      → record_failure(), break → next  │
      │     └────────────────────────────────────────────────────┘
      │
      └─── All processors exhausted → return declined
```

### Circuit Breaker state machine

```
    [CLOSED] ──────────────────────────────────────────────────┐
        │                                                        │
        │ success_rate < 20% (min 5 samples)                    │
        ▼                                                        │
     [OPEN]                                                      │
        │                                                        │
        │ cooldown elapsed (120s)                                │
        ▼                                                        │
  [HALF_OPEN] ── probe succeeds ──────────────────────────────►─┘
        │
        │ probe fails
        ▼
     [OPEN] (cooldown resets)
```

---

## Design Decisions & Trade-offs

| Decision | Rationale |
|---|---|
| **In-memory state** | Circuit breaker state and stats are stored in memory. Simple and fast for this challenge. In production: Redis for distributed state across multiple instances. |
| **Random mock processors** | Each processor has a probability table for outcomes (success, soft decline, hard decline, rate limit, timeout). Tuned to make fallback behavior visible without being too frequent. |
| **asyncio.wait_for for timeouts** | Processor timeouts are simulated by sleeping for 60s. The engine uses `asyncio.wait_for(3s)` to interrupt them. This cleanly separates timeout logic from processor logic. |
| **Full jitter backoff** | Uses random(0, min(cap, base * 2^attempt)) to avoid thundering herd when many clients retry simultaneously. |
| **Cost-aware routing** | Processors are sorted by fee_rate before each transaction. Since VortexPay < SwiftPay < PixFlow, this naturally favors the primary processor, but dynamically re-routes if cheaper processors are circuit-broken. |
| **Minimum 5 samples before tripping** | Prevents the circuit breaker from tripping on the very first failure when the window is empty. |
| **TransactionResponse always 200** | Both approved and declined transactions return HTTP 200. The `status` field indicates the outcome. A declined transaction is a valid business outcome, not an error. HTTP 4xx/5xx are reserved for invalid requests or server errors. |

---

## Project Structure

```
├── app/
│   ├── main.py                   # FastAPI app, lifespan, router wiring
│   ├── config.py                 # Settings (env-configurable)
│   ├── models/
│   │   ├── transaction.py        # TransactionRequest, TransactionResponse
│   │   ├── processor.py          # ProcessorResult, ProcessorStatusResponse
│   │   └── stats.py              # StatsResponse
│   ├── processors/
│   │   ├── base.py               # AbstractProcessor
│   │   ├── vortex_pay.py         # Mock VortexPay (2.5% fee)
│   │   ├── swift_pay.py          # Mock SwiftPay (2.9% fee)
│   │   └── pix_flow.py           # Mock PixFlow (3.2% fee)
│   ├── circuit_breaker/
│   │   ├── breaker.py            # CircuitBreaker state machine
│   │   └── registry.py           # CircuitBreakerRegistry
│   ├── engine/
│   │   ├── fallback_engine.py    # Core orchestration logic
│   │   └── backoff.py            # Exponential backoff utility
│   ├── routers/
│   │   ├── transactions.py       # POST /transactions
│   │   ├── processors.py         # GET /processors/status
│   │   └── stats.py              # GET /stats
│   └── services/
│       └── stats_service.py      # In-memory stats accumulator
├── tests/
│   └── demo.py                   # Full demonstration script
├── requirements.txt
└── README.md
```

---

## Configuration

All settings can be overridden via environment variables or a `.env` file:

| Variable | Default | Description |
|---|---|---|
| `CB_ROLLING_WINDOW_SIZE` | `50` | Max samples in CB rolling window |
| `CB_ROLLING_WINDOW_SECONDS` | `300` | Max age (seconds) for CB samples |
| `CB_TRIP_THRESHOLD` | `0.20` | Trip when success rate drops below this |
| `CB_COOLDOWN_SECONDS` | `120` | Cooldown before circuit tries to recover |
| `BACKOFF_BASE_SECONDS` | `0.5` | Base delay for exponential backoff |
| `BACKOFF_MAX_SECONDS` | `30` | Cap for backoff delay |
| `BACKOFF_MAX_RETRIES` | `2` | Max retries on rate limit before moving to next processor |
| `PROCESSOR_TIMEOUT_SECONDS` | `3.0` | Per-processor call timeout |
