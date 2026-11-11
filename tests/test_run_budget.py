"""Run-wide hard limits: reservation must hold under concurrent agents."""

import asyncio

import pytest

from backend.engine import AgentSpec, Executor, GraphSpec, Node
from backend.engine.run_budget import BudgetExceeded, RunBudget
from backend.events import EventStore
from backend.providers.base import Completion


async def test_concurrent_reservations_never_exceed_the_cap():
    b = RunBudget(max_tokens=1000)
    results = await asyncio.gather(*(_try(b, 300) for _ in range(10)), return_exceptions=True)
    ok = [r for r in results if r is True]
    assert len(ok) == 3  # 3 x 300 fits, a 4th would pass 1000
    assert b.reserved_tokens <= 1000


async def _try(b, n):
    await asyncio.sleep(0)
    await b.reserve(n)
    return True


async def test_settle_returns_unused_reservation():
    b = RunBudget(max_tokens=1000)
    await b.reserve(600)
    await b.settle(600, tokens=100, cost_usd=0.01)
    await b.reserve(800)  # 100 spent + 800 reserved fits
    assert b.spent_tokens == 100


async def test_call_and_cost_limits():
    b = RunBudget(max_calls=1, max_cost_usd=0.05)
    await b.reserve(10)
    with pytest.raises(BudgetExceeded):
        await b.reserve(10)
    b2 = RunBudget(max_cost_usd=0.05)
    await b2.reserve(10)
    await b2.settle(10, tokens=10, cost_usd=0.06)
    with pytest.raises(BudgetExceeded):
        await b2.reserve(1)


async def test_executor_parallel_agents_share_the_run_cap(tmp_path):
    calls = 0

    async def completer(model, messages, **kw):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return Completion(text='{"ok": true}', tokens_in=100, tokens_out=100, model=model)

    nodes = [Node(id=f"a{i}", type="agent", agent=AgentSpec(
        id=f"a{i}", role="r", system_contract="s", model="x:y", budget_tokens=4000))
        for i in range(6)]
    g = GraphSpec(nodes=nodes).finalize()
    async with EventStore(url=f"file:{tmp_path}/e.db") as s:
        await s.create_run("r", "g", g.config_hash, 1, g.model_dump())
        ex = Executor(s, g, "r", completer=completer, run_budget=RunBudget(max_tokens=2500))
        res = await ex.run()
    done = [r for r in res.results.values() if r.ok]
    # each call reserves input estimate + 1024 max_tokens, so only 2 can be in flight/settled
    assert 1 <= len(done) < 6
    assert calls == len(done)  # refused agents made NO provider call
    assert any("run token limit" in (r.error or "") for r in res.results.values() if not r.ok)
