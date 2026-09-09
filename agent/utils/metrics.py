"""
Latency tracking for the "perceived response time" evidence you'll need
in RIME_EVIDENCE.md — measure the entire user path, not a convenient
proxy. This is working code, no TODOs: call .mark() at each stage and
.log_turn() at the end of a turn.
"""

import time
from typing import Dict, List, Optional
from agent.utils.logger import get_logger

logger = get_logger(__name__)


class LatencyTracker:
    def __init__(self):
        self._marks: Dict[str, float] = {}
        self._history: List[Dict] = []
        self._last_logged_turn: Optional[Dict] = None

    def mark(self, label: str) -> None:
        """Record a timestamp for a specific event."""
        self._marks[label] = time.monotonic()
        logger.debug(f"Marked: {label} at {self._marks[label]}")

    def elapsed(self, start_label: str, end_label: str) -> float:
        """Calculate elapsed time between two marks in seconds."""
        if start_label not in self._marks or end_label not in self._marks:
            return -1.0
        return self._marks[end_label] - self._marks[start_label]

    def has_marks(self) -> bool:
        """Check if any marks exist."""
        return len(self._marks) > 0

    def log_turn(self) -> Optional[Dict]:
        """
        Log and record the full breakdown of the current turn.
        Clears marks after logging.
        Returns the breakdown dict or None if insufficient marks.
        """
        if not self._marks:
            logger.debug("No marks to log - skipping turn latency logging")
            return None

        order = [
            "utterance_received",
            "tool_call_started",
            "tool_call_resolved",
            "response_spoken",
        ]
        
        # Find which marks are present
        present = [m for m in order if m in self._marks]
        
        if len(present) < 2:
            logger.debug(f"Insufficient marks for turn logging: {present}")
            self._marks.clear()
            return None

        breakdown = {}
        for a, b in zip(present, present[1:]):
            elapsed = self.elapsed(a, b)
            if elapsed >= 0:
                breakdown[f"{a}->{b}"] = round(elapsed * 1000, 1)

        total_ms = round(self.elapsed(present[0], present[-1]) * 1000, 1) if len(present) >= 2 else -1
        
        turn_data = {
            "total_ms": total_ms,
            "marks": present,
            **breakdown
        }
        
        logger.info(f"Turn latency: total={total_ms}ms breakdown={breakdown}")
        self._history.append(turn_data)
        self._last_logged_turn = turn_data
        
        # CRITICAL: Clear marks after logging to prevent contamination
        self._marks.clear()
        
        return turn_data

    def get_last_turn(self) -> Optional[Dict]:
        """Get the last logged turn data."""
        return self._last_logged_turn

    @property
    def history(self) -> List[Dict]:
        """Get all logged turn history."""
        return self._history

    def clear(self) -> None:
        """Clear all marks and history."""
        self._marks.clear()
        self._history.clear()
        self._last_logged_turn = None