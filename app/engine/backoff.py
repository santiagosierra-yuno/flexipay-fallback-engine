import asyncio
import random


async def exponential_backoff(
    attempt: int,
    base: float = 0.5,
    cap: float = 30.0,
    jitter: bool = True,
) -> float:
    """
    Full jitter exponential backoff.
    delay = random(0, min(cap, base * 2^attempt))

    Returns the actual delay in seconds.
    """
    delay = min(cap, base * (2 ** attempt))
    if jitter:
        delay = random.uniform(0, delay)
    await asyncio.sleep(delay)
    return delay
