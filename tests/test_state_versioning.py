"""
Unit tests for ConversationState and ToolCallManager version fencing.
"""

import asyncio
import pytest
from agent.state import ConversationState, ToolCallManager


def test_conversation_state_initialization():
    state = ConversationState()
    assert state.turn_version == 0
    assert state.current_intent == {}
    assert state.is_current(0) is True
    assert state.is_current(1) is False


def test_conversation_state_version_bumping():
    state = ConversationState()
    v1 = state.bump_version({"symbols": ["AAPL"], "field": "price"})
    assert v1 == 1
    assert state.turn_version == 1
    assert state.current_intent == {"symbols": ["AAPL"], "field": "price"}
    assert state.is_current(1) is True
    assert state.is_current(0) is False

    v2 = state.bump_version({"symbols": ["TSLA"], "field": "volume"})
    assert v2 == 2
    assert state.turn_version == 2
    assert state.is_current(2) is True
    assert state.is_current(1) is False


@pytest.mark.asyncio
async def test_tool_call_manager_pre_execution_fencing():
    """If the state was already bumped before the tool begins running, return None immediately."""
    state = ConversationState()
    mgr = ToolCallManager(state)

    v1 = state.bump_version({"symbols": ["AAPL"]})
    # Bump to v2 before running v1
    v2 = state.bump_version({"symbols": ["NVDA"]})

    executed = False

    async def dummy_tool():
        nonlocal executed
        executed = True
        return "result"

    result = await mgr.run(v1, dummy_tool())
    assert result is None
    assert executed is False


@pytest.mark.asyncio
async def test_tool_call_manager_in_flight_fencing():
    """Tool starts under v1, version bumped to v2 while in-flight -> result is discarded."""
    state = ConversationState()
    mgr = ToolCallManager(state)

    v1 = state.bump_version({"symbols": ["AAPL"]})

    async def slow_tool():
        await asyncio.sleep(0.2)
        return {"data": "AAPL"}

    task = asyncio.create_task(mgr.run(v1, slow_tool()))
    await asyncio.sleep(0.05)

    # User interrupts
    mgr.cancel_active()
    v2 = state.bump_version({"symbols": ["NVDA"]})

    result = await task
    assert result is None


@pytest.mark.asyncio
async def test_tool_call_manager_successful_completion():
    """Tool finishes while version is still current -> result is preserved."""
    state = ConversationState()
    mgr = ToolCallManager(state)

    v1 = state.bump_version({"symbols": ["NVDA"]})

    async def fast_tool():
        await asyncio.sleep(0.05)
        return {"data": "NVDA"}

    result = await mgr.run(v1, fast_tool())
    assert result == {"data": "NVDA"}
