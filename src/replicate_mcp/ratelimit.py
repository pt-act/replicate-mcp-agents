"""Async token-bucket rate limiter for Replicate API calls.

Sprint S7 — Hardening.  Prevents overloading the Replicate API and
avoids 429 (Too Many Requests) errors by throttling outgoing requests
to a configurable rate.

The token-bucket algorithm:
    - Tokens accumulate at ``rate`` tokens/second up to ``capacity``.
    - Each API call consumes one (or more) tokens.
    - If insufficient tokens are available, the caller is suspended
      until enough tokens have accumulated.

Usage::

    from replicate_mcp.ratelimit import TokenBucket, RateLimiter

    # 5 requests/second, burst of up to 10
    bucket = TokenBucket(rate=5.0, capacity=10.0)

    async def make_request():
        await bucket.acquire()          # blocks if too fast
        return await call_replicate()

    # Or use the higher-level RateLimiter with named buckets:
    limiter = RateLimiter()
    limiter.add("replicate", rate=5.0, capacity=10.0)

    async def make_request(model: str):
        await limiter.acquire("replicate")
        ...
"""

from __future__ import annotations

import asyncio
import time

# ---------------------------------------------------------------------------
# Token bucket
# ---------------------------------------------------------------------------


class TokenBucket:
    """Async token-bucket rate limiter.

    Args:
        rate:     Tokens refilled per second.
        capacity: Maximum token capacity (burst allowance).

    Thread/task safety:
        Uses ``asyncio.Lock`` — safe for concurrent async tasks within
        a single event loop.  Not safe across threads without additional
        locking.
    """

    def __init__(self, rate: float, capacity: float) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate}")
        if capacity <= 0:
            raise ValueError(f"capacity must be positive, got {capacity}")
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill: float = time.monotonic()
        self._lock: asyncio.Lock | None = None  # created lazily

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ---- refill logic (not locked — caller must hold lock) ----

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    # ---- public API ----

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until *tokens* tokens are available, then consume them.

        Args:
            tokens: Number of tokens to consume (default 1).

        Raises:
            ValueError: If *tokens* exceeds the bucket capacity.
        """
        if tokens > self._capacity:
            raise ValueError(
                f"Requested {tokens} tokens exceeds capacity {self._capacity}"
            )

        async with self._get_lock():
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait_s = (tokens - self._tokens) / self._rate
                await asyncio.sleep(wait_s)

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking attempt to consume *tokens*.

        Returns:
            ``True`` if tokens were consumed; ``False`` if insufficient
            tokens are currently available.
        """
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    @property
    def available_tokens(self) -> float:
        """Current token level (after a refill calculation)."""
        self._refill()
        return self._tokens

    @property
    def rate(self) -> float:
        """Configured refill rate (tokens/second)."""
        return self._rate

    @property
    def capacity(self) -> float:
        """Maximum token capacity."""
        return self._capacity

    def __repr__(self) -> str:
        return (
            f"TokenBucket(rate={self._rate}, capacity={self._capacity}, "
            f"available={self._tokens:.2f})"
        )


# ---------------------------------------------------------------------------
# Higher-level rate limiter with named buckets
# ---------------------------------------------------------------------------


class RateLimiter:
    """Registry of named :class:`TokenBucket` instances.

    Allows different subsystems to maintain independent rate limits
    under a common interface.

    Args:
        default_rate:     Rate for buckets added without explicit rate.
        default_capacity: Capacity for buckets added without explicit capacity.

    Usage::

        limiter = RateLimiter(default_rate=10.0, default_capacity=20.0)
        limiter.add("llama3")
        limiter.add("flux", rate=2.0, capacity=5.0)

        await limiter.acquire("llama3")
        await limiter.acquire("flux")
    """

    def __init__(
        self,
        default_rate: float = 10.0,
        default_capacity: float = 20.0,
    ) -> None:
        self._default_rate = default_rate
        self._default_capacity = default_capacity
        self._buckets: dict[str, TokenBucket] = {}

    def add(
        self,
        name: str,
        *,
        rate: float | None = None,
        capacity: float | None = None,
    ) -> RateLimiter:
        """Register a new named bucket (or replace an existing one).

        Returns ``self`` for fluent chaining.
        """
        self._buckets[name] = TokenBucket(
            rate=rate if rate is not None else self._default_rate,
            capacity=capacity if capacity is not None else self._default_capacity,
        )
        return self

    def _get_or_create(self, name: str) -> TokenBucket:
        if name not in self._buckets:
            self.add(name)
        return self._buckets[name]

    async def acquire(self, name: str, tokens: float = 1.0) -> None:
        """Acquire *tokens* from the bucket named *name*.

        Auto-creates a bucket with default parameters if *name* is not
        yet registered.
        """
        await self._get_or_create(name).acquire(tokens)

    def try_acquire(self, name: str, tokens: float = 1.0) -> bool:
        """Non-blocking acquire from *name*."""
        return self._get_or_create(name).try_acquire(tokens)

    def available(self, name: str) -> float:
        """Return available tokens for the named bucket."""
        return self._get_or_create(name).available_tokens

    def remove(self, name: str) -> None:
        """Remove the bucket named *name* if it exists."""
        self._buckets.pop(name, None)

    @property
    def bucket_names(self) -> list[str]:
        """Sorted list of registered bucket names."""
        return sorted(self._buckets)

    def __repr__(self) -> str:
        return f"RateLimiter(buckets={self.bucket_names})"


__all__ = [
    "TokenBucket",
    "RateLimiter",
]
