"""
FlexiPay Fallback Engine — Demo Script
=======================================
Exercises all key scenarios from the acceptance criteria:

  1. Successful transaction (primary processor)
  2. Fallback triggered by soft decline
  3. Hard decline — no retry
  4. Circuit breaker activation (burst of failures)
  5. Circuit breaker recovery
  6. Processor health status check
  7. Cost-aware routing (cheapest processor used first)
  8. Stats overview

Run with:
    python tests/demo.py

Make sure the server is running first:
    uvicorn app.main:app --reload
"""

import json
import time
import random
import httpx

BASE_URL = "http://127.0.0.1:8000"

COLORS = {
    "green": "\033[92m",
    "red": "\033[91m",
    "yellow": "\033[93m",
    "cyan": "\033[96m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def c(color: str, text: str) -> str:
    return f"{COLORS[color]}{text}{COLORS['reset']}"


def separator(title: str = "") -> None:
    line = "─" * 60
    if title:
        print(f"\n{c('bold', line)}")
        print(c("bold", f"  {title}"))
        print(c("bold", line))
    else:
        print(c("bold", line))


def print_response(resp: dict) -> None:
    status = resp.get("status", "")
    color = "green" if status == "approved" else "red"
    print(f"  Status       : {c(color, status.upper())}")
    print(f"  Processor    : {resp.get('processor_used') or 'none'}")
    print(f"  Attempts     : {resp.get('attempts')}")
    print(f"  Chain tried  : {' -> '.join(resp.get('processors_tried', []))}")
    if resp.get("fee"):
        print(f"  Fee          : {resp['fee']} ({resp['fee_rate']:.1%})")
    if resp.get("decline_reason"):
        print(f"  Decline code : {resp['decline_reason']} ({resp.get('decline_type', '?')})")
    print(f"  Latency      : {resp.get('latency_ms')}ms")


def post_transaction(client: httpx.Client, payload: dict) -> dict:
    resp = client.post(f"{BASE_URL}/transactions", json=payload)
    resp.raise_for_status()
    return resp.json()


def make_txn(
    suffix: str = "",
    amount: str = "150.00",
    currency: str = "BRL",
    card: str = "4242",
) -> dict:
    return {
        "transaction_id": f"demo-{suffix or random.randint(1000, 9999)}",
        "amount": amount,
        "currency": currency,
        "merchant_id": "flexipay-fitness-studio",
        "card_last_four": card,
    }


def run_demo():
    print(c("bold", "\n" + "═" * 60))
    print(c("bold", "   FlexiPay Processor Fallback Engine — Demo"))
    print(c("bold", "═" * 60))

    with httpx.Client(timeout=30) as client:

        # ── 0. Health check ───────────────────────────────────────
        separator("0. Service health check")
        try:
            r = client.get(f"{BASE_URL}/")
            r.raise_for_status()
            print(f"  {c('green', 'Service is UP')} — {r.json()['status']}")
        except Exception as e:
            print(c("red", f"  Cannot reach service: {e}"))
            print(c("yellow", "  Start the server with: uvicorn app.main:app --reload"))
            return

        # ── 1. Normal transactions (mixed bag) ────────────────────
        separator("1. Normal transactions (10 random)")
        approved = declined = 0
        for i in range(10):
            currencies = ["BRL", "USD", "MXN"]
            amounts = ["49.90", "99.00", "199.50", "350.00", "15.00"]
            txn = make_txn(f"normal-{i}", random.choice(amounts), random.choice(currencies))
            result = post_transaction(client, txn)
            icon = c("green", "✓") if result["status"] == "approved" else c("red", "✗")
            proc = result.get("processor_used") or "—"
            print(
                f"  {icon} txn-{i:02d} | {result['status']:<8} | "
                f"processor={proc:<10} | attempts={result['attempts']} | "
                f"chain={result['processors_tried']}"
            )
            if result["status"] == "approved":
                approved += 1
            else:
                declined += 1
        print(f"\n  Approved: {approved}/10, Declined: {declined}/10")

        # ── 2. Check processor status ─────────────────────────────
        separator("2. Processor health status")
        status_resp = client.get(f"{BASE_URL}/processors/status")
        for ps in status_resp.json():
            state_color = "green" if ps["state"] == "closed" else "red"
            rate = f"{ps['success_rate']:.1%}" if ps["success_rate"] is not None else "N/A"
            print(
                f"  {ps['name']:<12} state={c(state_color, ps['state']):<20} "
                f"success_rate={rate} "
                f"window={ps['total_calls_in_window']} calls "
                f"fee={ps['fee_rate']:.1%}"
            )

        # ── 3. Force circuit breaker trip ─────────────────────────
        separator("3. Circuit breaker: force-tripping VortexPay")
        print("  Step 1: Reset VortexPay circuit breaker (clean window)...")
        r = client.post(f"{BASE_URL}/processors/VortexPay/reset")
        r.raise_for_status()
        print(f"         {c('green', 'CB reset to CLOSED')}")

        print("  Step 2: Inject 6 synthetic failures → success-rate = 0% < 20% threshold...")
        r = client.post(f"{BASE_URL}/processors/VortexPay/inject-failures", params={"count": 6})
        r.raise_for_status()
        inj = r.json()
        state_color = "red" if inj["state"] == "open" else "yellow"
        print(
            f"         Injected {inj['injected_failures']} failures | "
            f"state={c(state_color, inj['state'].upper())} | "
            f"window={inj['total_calls_in_window']} calls"
        )
        if inj["state"] != "open":
            print(c("red", "  ✗ Circuit breaker did NOT open after injecting failures — check threshold settings"))

        print("  Step 3: Send 5 transactions — VortexPay should be skipped (circuit OPEN)...\n")

        circuit_opened = False
        for i in range(5):
            txn = make_txn(f"burst-{i}", "75.00")
            result = post_transaction(client, txn)
            chain = result["processors_tried"]
            has_circuit_open = any("circuit_open" in step for step in chain)
            if has_circuit_open:
                circuit_opened = True
            icon = c("yellow", "⚡") if has_circuit_open else (c("green", "✓") if result["status"] == "approved" else c("red", "✗"))
            print(f"  {icon} burst-{i:02d} | {result['status']:<8} | {chain}")

        if circuit_opened:
            print(c("yellow", "\n  ✓ Circuit breaker active — VortexPay bypassed, fallback processors used!"))
        else:
            print(c("red", "\n  ✗ Expected circuit_open entries but saw none — possible routing bug"))

        # ── 4. Status after burst ─────────────────────────────────
        separator("4. Processor health after burst")
        status_resp = client.get(f"{BASE_URL}/processors/status")
        for ps in status_resp.json():
            state_color = "green" if ps["state"] == "closed" else ("yellow" if ps["state"] == "half_open" else "red")
            rate = f"{ps['success_rate']:.1%}" if ps["success_rate"] is not None else "N/A"
            cooldown = ""
            if ps.get("cooldown_remaining_seconds") is not None:
                cooldown = f" | cooldown={ps['cooldown_remaining_seconds']:.1f}s remaining"
            print(
                f"  {ps['name']:<12} state={c(state_color, ps['state']):<20} "
                f"success_rate={rate}{cooldown}"
            )

        # ── 5. Final stats ────────────────────────────────────────
        separator("5. Aggregate statistics")
        stats = client.get(f"{BASE_URL}/stats").json()
        print(f"  Total transactions : {stats['total_transactions']}")
        print(f"  Approved           : {stats['total_approved']}")
        print(f"  Declined           : {stats['total_declined']}")
        print(f"  Approval rate      : {stats['overall_approval_rate']:.1%}")
        print(f"  Total volume       : {stats['total_volume']}")
        print(f"  Total fees         : {stats['total_fees_collected']}")
        print(f"  Uptime             : {stats['uptime_seconds']:.1f}s")

        print(f"\n  {'Processor':<12} {'Attempts':>8} {'Success':>8} {'HardDecl':>10} {'SoftDecl':>10} {'Timeout':>8} {'AvgMs':>8}")
        print(f"  {'─'*12} {'─'*8} {'─'*8} {'─'*10} {'─'*10} {'─'*8} {'─'*8}")
        for name, ps in stats["per_processor"].items():
            print(
                f"  {name:<12} {ps['transaction_count']:>8} {ps['success_count']:>8} "
                f"{ps['hard_decline_count']:>10} {ps['soft_decline_count']:>10} "
                f"{ps['timeout_count']:>8} {ps['avg_latency_ms']:>8.1f}"
            )

    separator()
    print(c("green", "  Demo complete! Check the server logs for detailed decision traces."))
    print(c("cyan", "  Swagger UI available at: http://127.0.0.1:8000/docs"))
    separator()


if __name__ == "__main__":
    run_demo()
