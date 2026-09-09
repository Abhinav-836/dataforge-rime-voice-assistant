"""
Entry point. All the actual wiring (AgentSession, Rime TTS, STT, transcript
routing) lives in agent/voice/livekit_handler.py, which defines its own
AgentServer + @server.rtc_session() entrypoint — that's the current
(2026) LiveKit Agents pattern, replacing the older WorkerOptions/
cli.run_app(entrypoint_fnc=...) style from older docs/tutorials you may
find online. This file just runs it.
Run modes (standard LiveKit Agents CLI, unchanged by the above):
    python -m agent.main console   # local mic/speaker test, no LiveKit room
    python -m agent.main dev       # connects to your LiveKit project, hot-reload
    python -m agent.main start     # production
"""

from dotenv import load_dotenv
load_dotenv()

from livekit.agents import cli
from agent.voice.livekit_handler import server
from agent.utils.logger import get_logger

logger = get_logger(__name__)

if __name__ == "__main__":
    logger.info("Starting agent via LiveKit Agents CLI")
    cli.run_app(server)