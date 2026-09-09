"""
Owns conversation state across turns. With LiveKit's AgentSession handling
STT/LLM/TTS orchestration directly (see voice/livekit_handler.py), this
class's job narrows to exactly the part LiveKit doesn't solve for you:
turn versioning and stale-tool-result fencing (agent/state.py).
"""

import os
import json
from agent.state import ConversationState, ToolCallManager
from agent.router import route_intent
from agent.utils.logger import get_logger
from agent.utils.metrics import LatencyTracker

logger = get_logger(__name__)


class Orchestrator:
    """
    Owns one conversation's lifecycle: state + tool-call fencing.
    ContinuityAgent's @function_tool methods read self.state.turn_version
    and call self.tool_mgr.run(...) directly — see voice/livekit_handler.py.
    """

    def __init__(self):
        self.state = ConversationState()
        self.tool_mgr = ToolCallManager(self.state)
        self.metrics = LatencyTracker()
        self._pending_metrics_flush = False

    async def on_user_utterance(self, text: str, is_final: bool):
        """
        Called by voice/livekit_handler.py's `user_input_transcribed`
        handler on each STT result.

        Returns: (version, is_new_request, intent)
        """
        if not is_final:
            return self.state.turn_version, False, {}

        self.metrics.mark("utterance_received")

        intent, is_new_request = route_intent(text, self.state.current_intent)

        # Check if this is a search query (weather, time, news, etc.)
        if self._is_search_query(text):
            intent["search"] = True
            intent["search_type"] = self._detect_search_type(text)

        if is_new_request:
            # Cancel active tools BEFORE bumping the version
            cancelled_count = self.tool_mgr.cancel_active()
            if cancelled_count > 0:
                logger.debug(f"Cancelled {cancelled_count} active tool tasks")
            
            version = self.state.bump_version(intent)
            logger.info(f"New intent, version={version}: {intent}")
            
            # Mark that we have a new turn - but don't log yet (wait for tool completion)
            self._pending_metrics_flush = True
        else:
            version = self.state.turn_version
            logger.info(f"Continuation, version unchanged={version}")
            
            # For continuations with no tool calls, log immediately
            if self.metrics.has_marks() and not self._pending_metrics_flush:
                self.metrics.log_turn()
                self._flush_metrics_history()

        return version, is_new_request, intent

    def mark_tool_started(self) -> None:
        """Mark that a tool call has started."""
        self.metrics.mark("tool_call_started")

    def mark_tool_resolved(self) -> None:
        """Mark that a tool call has resolved."""
        self.metrics.mark("tool_call_resolved")

    def mark_response_spoken(self) -> None:
        """Mark that a response has been spoken."""
        self.metrics.mark("response_spoken")
        # Log the turn now that we have all marks
        if self.metrics.has_marks():
            self.metrics.log_turn()
            self._flush_metrics_history()
            self._pending_metrics_flush = False

    def _flush_metrics_history(self) -> None:
        """Flush metrics history to file if in evidence recording mode."""
        if os.getenv("EVIDENCE_RECORDING_MODE", "").lower() in ("true", "1") and self.metrics.history:
            try:
                with open("latency_history.json", "w", encoding="utf-8") as f:
                    json.dump(self.metrics.history, f, indent=2)
            except Exception as e:
                logger.warning(f"Could not write latency history: {e}")

    def _is_search_query(self, text: str) -> bool:
        """Detect if the user is asking for weather, time, news, or general info."""
        text_lower = text.lower()
        search_markers = [
            "weather", "temperature", "rain", "sunny", "cloudy",
            "time", "date", "what time", "what day",
            "news", "headlines", "latest",
            "search", "find", "look up", "tell me about",
            "who is", "what is", "where is",
        ]
        return any(marker in text_lower for marker in search_markers)

    def _detect_search_type(self, text: str) -> str:
        """Determine which search tool to use."""
        text_lower = text.lower()

        if any(w in text_lower for w in ["weather", "temperature", "rain", "sunny", "cloudy", "forecast"]):
            return "weather"

        if any(w in text_lower for w in ["time", "date", "what time", "what day", "timezone"]):
            return "time"

        if any(w in text_lower for w in ["news", "headlines", "latest"]):
            return "news"

        return "general"