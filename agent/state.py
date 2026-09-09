"""
Conversation state versioning and tool-call fencing.

Core idea: every user utterance that changes intent bumps a `turn_version`.
Every tool call is tagged with the version it was started under. When a tool
call resolves, we only act on / speak its result if the version it was
started under still matches the CURRENT version. Otherwise the result is
discarded (fenced) — it is stale and must never reach the user.

This is the single source of truth for fencing logic in this project.
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Set
import asyncio
import time
import sys

from agent.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ConversationState:
    """Single source of truth for 'what does the user currently want'."""
    turn_version: int = 0
    current_intent: dict = field(default_factory=dict)
    active_task_id: Optional[str] = None
    active_task_version: Optional[int] = None

    def bump_version(self, new_intent: dict) -> int:
        """Call whenever the user says something that changes the request."""
        self.turn_version += 1
        self.current_intent = dict(new_intent)
        logger.debug(f"Bumped version to {self.turn_version}: {new_intent}")
        return self.turn_version

    def is_current(self, version: int) -> bool:
        """Check before speaking any tool result: is this still relevant?"""
        return version == self.turn_version


class ToolCallManager:
    """Wraps slow or external tool calls with deterministic cancellation + fencing."""

    def __init__(self, state: ConversationState):
        self.state = state
        self._current_task: Optional[asyncio.Task] = None
        self._active_tasks: Set[asyncio.Task] = set()
        self._cancelled_count: int = 0

    def cancel_active(self) -> int:
        """
        Call the instant a new user utterance changes intent (barge-in).
        Returns the number of tasks cancelled.
        """
        cancelled = 0
        for task in list(self._active_tasks):
            if not task.done():
                try:
                    task.cancel()
                    cancelled += 1
                except Exception as e:
                    logger.debug(f"Error cancelling task: {e}")
        self._active_tasks.clear()
        
        if self._current_task and not self._current_task.done():
            try:
                self._current_task.cancel()
                cancelled += 1
            except Exception as e:
                logger.debug(f"Error cancelling current task: {e}")
        self._current_task = None
        
        if cancelled > 0:
            self._cancelled_count += cancelled
            logger.debug(f"Cancelled {cancelled} active tool tasks (total: {self._cancelled_count})")
        
        return cancelled

    def get_cancelled_count(self) -> int:
        """Get the total number of tool calls cancelled."""
        return self._cancelled_count

    async def run(self, version: int, coro) -> Optional[Any]:
        """
        Run a tool coroutine tagged with its version.
        Returns the result only if still current when it finishes; None if stale/cancelled.
        """
        # If the version is already stale before we even start, discard immediately
        if not self.state.is_current(version):
            logger.info(f"Discarded tool call pre-execution: version {version} != current {self.state.turn_version}")
            if asyncio.iscoroutine(coro):
                coro.close()
            return None

        task = asyncio.ensure_future(coro)
        self._current_task = task
        self._active_tasks.add(task)

        try:
            result = await task
        except asyncio.CancelledError:
            logger.info(f"Tool call task cancelled for version {version}")
            return None
        except Exception as e:
            logger.error(f"Tool execution exception for version {version}: {e}")
            return {"error": str(e)}
        finally:
            self._active_tasks.discard(task)
            if self._current_task is task:
                self._current_task = None

        # Critical Fencing Check: Did intent change while the tool was running?
        if not self.state.is_current(version):
            logger.info(
                f"Fenced stale tool result: started under v{version}, "
                f"current version is v{self.state.turn_version}"
            )
            return None

        return result


# ---------------------------------------------------------------------------
# Demo stress test
# ---------------------------------------------------------------------------

async def _example_slow_tool(intent: dict, delay_s: float = 4.0) -> dict:
    await asyncio.sleep(delay_s)
    return {"status": "ok", "intent": intent, "resolved_at": time.time()}


async def demo_stress_case():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    state = ConversationState()
    mgr = ToolCallManager(state)

    v1 = state.bump_version({"symbols": ["AAPL"], "field": "price"})
    task = asyncio.create_task(mgr.run(v1, _example_slow_tool(state.current_intent, delay_s=4.0)))

    await asyncio.sleep(0.5)
    mgr.cancel_active()
    v2 = state.bump_version({"symbols": ["NVDA"], "field": "price"})

    stale_result = await task
    assert stale_result is None, "Stale result must be fenced, not spoken!"
    print("Stale AAPL result correctly discarded.")

    fresh_result = await mgr.run(v2, _example_slow_tool(state.current_intent, delay_s=0.2))
    assert fresh_result is not None
    assert fresh_result["intent"]["symbols"] == ["NVDA"]
    print(f"Fresh result correctly spoken: {fresh_result}")


if __name__ == "__main__":
    asyncio.run(demo_stress_case())