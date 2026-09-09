"""
Rime TTS integration — CRITICAL for eligibility. Rime must be the primary
spoken output, streamed via the official LiveKit Rime plugin (not called
as an incidental/side channel).

Install:
    pip install livekit-plugins-rime

Auth: set RIME_API_KEY in your .env.

Model/voice IDs:
    Model: "coda"
    Speaker: "celeste" (default)
"""

import os
from typing import Optional
from livekit.plugins import rime
from agent.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL_ID = os.getenv("RIME_MODEL_ID", "coda")
DEFAULT_SPEAKER = os.getenv("RIME_SPEAKER", "celeste")


def build_rime_tts(
    model: Optional[str] = None,
    speaker: Optional[str] = None,
    speed: Optional[float] = None
) -> "rime.TTS":
    """
    Construct the Rime TTS plugin instance with WebSocket streaming for lowest latency.
    """
    if not os.getenv("RIME_API_KEY"):
        logger.warning(
            "RIME_API_KEY not set — Rime TTS will fail to authenticate. "
            "Set it in .env before running."
        )

    selected_model = model or os.getenv("RIME_MODEL_ID", DEFAULT_MODEL_ID)
    selected_speaker = speaker or os.getenv("RIME_SPEAKER", DEFAULT_SPEAKER)
    selected_speed = speed if speed is not None else 1.0

    return rime.TTS(
        model=selected_model,
        speaker=selected_speaker,
        speed_alpha=selected_speed,
        use_websocket=True,
    )
