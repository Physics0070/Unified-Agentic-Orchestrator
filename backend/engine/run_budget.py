"""Run-wide hard limits (tokens, cost, calls) with concurrency-safe reservation.

Per-agent budgets alone let N parallel agents each spend their own allowance.
A call first *reserves* its worst case (input estimate + max output) under one
lock, so parallel agents cannot jointly overspend; `settle` then swaps the
reservation for what the provider actually reported.
"""

import asyncio


class BudgetExceeded(RuntimeError):
    pass


class RunBudget:
    def __init__(
        self, max_tokens: int = 0, max_cost_usd: float = 0.0, max_calls: int = 0
    ) -> None:
        # 0 = unlimited for that dimension.
        self.max_tokens, self.max_cost_usd, self.max_calls = max_tokens, max_cost_usd, max_calls
        self.spent_tokens = 0
        self.spent_cost = 0.0
        self.calls = 0
        self.reserved_tokens = 0  # spent + in-flight reservations
        self._lock = asyncio.Lock()

    async def reserve(self, tokens: int) -> None:
        async with self._lock:
            if self.max_calls and self.calls >= self.max_calls:
                raise BudgetExceeded(f"run call limit {self.max_calls} reached")
            if self.max_cost_usd and self.spent_cost >= self.max_cost_usd:
                raise BudgetExceeded(f"run cost limit ${self.max_cost_usd} reached")
            if self.max_tokens and self.reserved_tokens + tokens > self.max_tokens:
                raise BudgetExceeded(f"run token limit {self.max_tokens} would be exceeded")
            self.calls += 1
            self.reserved_tokens += tokens

    async def settle(self, reserved: int, tokens: int, cost_usd: float) -> None:
        """Replace a reservation with actual usage (use tokens=0 on a failed call)."""
        async with self._lock:
            self.reserved_tokens += tokens - reserved
            self.spent_tokens += tokens
            self.spent_cost += cost_usd
