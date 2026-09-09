"""
Deterministic tests for turn version fencing under rapid barge-in conditions.
"""

import asyncio
import pytest
from agent.state import ConversationState, ToolCallManager


@pytest.mark.asyncio
async def test_rapid_triple_barge_in():
    """Simulate rapid triple correction: AAPL -> MSFT -> NVDA."""
    state = ConversationState()
    mgr = ToolCallManager(state)

    # 1. First utterance: AAPL
    v1 = state.bump_version({"symbols": ["AAPL"]})

    async def slow_call(name: str, delay: float):
        await asyncio.sleep(delay)
        return {"symbol": name}

    t1 = asyncio.create_task(mgr.run(v1, slow_call("AAPL", 0.3)))

    # 2. Rapid correction 1: MSFT
    await asyncio.sleep(0.02)
    mgr.cancel_active()
    v2 = state.bump_version({"symbols": ["MSFT"]})
    t2 = asyncio.create_task(mgr.run(v2, slow_call("MSFT", 0.3)))

    # 3. Rapid correction 2: NVDA
    await asyncio.sleep(0.02)
    mgr.cancel_active()
    v3 = state.bump_version({"symbols": ["NVDA"]})
    t3 = asyncio.create_task(mgr.run(v3, slow_call("NVDA", 0.05)))

    res1 = await t1
    res2 = await t2
    res3 = await t3

    assert res1 is None, "AAPL must be fenced"
    assert res2 is None, "MSFT must be fenced"
    assert res3 is not None, "NVDA must be delivered"
    assert res3["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_deterministic_fencing_independent_of_timing():
    """Verify fencing is based on state version, not wall-clock sleep."""
    state = ConversationState()
    mgr = ToolCallManager(state)

    v1 = state.bump_version({"symbols": ["AAPL"]})
    # Advance version immediately before running v1
    v2 = state.bump_version({"symbols": ["TSLA"]})

    async def instant_tool():
        return {"symbol": "AAPL"}

    # Even though instant_tool takes 0ms, it was initiated under v1, while state is v2
    res = await mgr.run(v1, instant_tool())
    assert res is None, "Result must be fenced immediately regardless of fast execution"


@pytest.mark.asyncio
async def test_tool_exception_safety():
    """An exception inside a tool should be safely caught and not raise unhandled."""
    state = ConversationState()
    mgr = ToolCallManager(state)
    v1 = state.bump_version({"symbols": ["FAIL"]})

    async def crashing_tool():
        raise ConnectionResetError("Remote server closed connection")

    res = await mgr.run(v1, crashing_tool())
    assert res is not None
    assert "error" in res
    assert "Remote server closed connection" in res["error"]
