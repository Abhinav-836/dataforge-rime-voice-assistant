"""
End-to-end interruption acceptance test.

This test verifies that stale tool results are fenced (discarded) when
the user interrupts and changes their request mid-tool-call.
"""

import asyncio
import os
import sys
import pytest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.state import ConversationState, ToolCallManager


async def simulate_tool_call(intent: dict, delay_s: float = 0.5) -> dict:
    """Simulate a tool call with asynchronous delay."""
    await asyncio.sleep(delay_s)
    return {
        "status": "ok",
        "intent": intent,
        "resolved_at": datetime.now().isoformat(),
    }


@pytest.mark.asyncio
async def test_interruption_fences_stale_result():
    """
    Acceptance test verifying that when user interrupts mid-flight:
    1. The active tool task is cancelled.
    2. The stale result (AAPL) is fenced (None returned).
    3. The fresh result (NVDA) is resolved and spoken.
    """
    state = ConversationState()
    mgr = ToolCallManager(state)

    # 1. User asks for Apple
    v1 = state.bump_version({"symbols": ["AAPL"], "field": "price"})
    assert v1 == 1

    task = asyncio.create_task(
        mgr.run(v1, simulate_tool_call(state.current_intent, delay_s=0.5))
    )

    # 2. User interrupts before Apple finishes
    await asyncio.sleep(0.1)
    mgr.cancel_active()
    v2 = state.bump_version({"symbols": ["NVDA"], "field": "price"})
    assert v2 == 2

    # 3. Await stale task - must be fenced
    stale_result = await task
    assert stale_result is None, "Stale result from v1 must be fenced (None)"

    # 4. Fresh request completes successfully
    fresh_result = await mgr.run(
        v2, simulate_tool_call(state.current_intent, delay_s=0.1)
    )
    assert fresh_result is not None, "Fresh result for v2 must not be None"
    assert fresh_result["intent"]["symbols"] == ["NVDA"]
    assert fresh_result["status"] == "ok"


if __name__ == "__main__":
    asyncio.run(test_interruption_fences_stale_result())
    print("Test passed successfully!")
