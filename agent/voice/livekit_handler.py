"""
LiveKit transport with flexible LLM provider support, Rime TTS, and SQLite telemetry.

Turn version fencing and tool execution tracking are integrated directly with SQLite
persistence and real-time LiveKit room data channel broadcasts.
"""

import asyncio
import os
import json
import time
from typing import Optional, Dict, Any
from dotenv import load_dotenv

os.environ["LIVEKIT_AGENT_HTTP_PORT"] = ""
os.environ["LIVEKIT_AGENT_PORT"] = "0"

from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    function_tool,
    llm,
)
from livekit.plugins import silero
from livekit.plugins import deepgram
from livekit.plugins import openai

try:
    from livekit.plugins import groq
    HAS_GROQ = True
except ImportError:
    HAS_GROQ = False
    groq = None

from agent.voice.rime_tts import build_rime_tts
from agent.orchestrator import Orchestrator
from agent.tools.stocks import get_stock_quote, compare_stocks
from agent.tools.search import get_weather, get_time, get_stock_news, search
from agent.db.repository import repo
from agent.utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

server = AgentServer()


def _build_llm():
    """Build LLM based on available API keys."""
    if os.getenv("GROQ_API_KEY"):
        if not HAS_GROQ:
            logger.warning("GROQ_API_KEY set but livekit-plugins-groq not installed")
        else:
            logger.info("Using Groq LLM (FREE tier)")
            return groq.LLM(model="openai/gpt-oss-20b")

    if os.getenv("OPENAI_API_KEY"):
        logger.info("Using OpenAI LLM")
        return openai.LLM(model="gpt-4o-mini")

    logger.warning("No LLM API key found — using fallback echo for testing")
    return None


def prewarm(proc: JobProcess) -> None:
    """Load VAD once per worker process to minimize connection latency."""
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


class ContinuityAgent(Agent):
    """
    The LLM-facing Agent. Tool methods are exposed via @function_tool.
    Handles turn version fencing, database recording, and real-time telemetry broadcasts.
    """

    def __init__(self, orchestrator: Orchestrator, session_id: str, room=None):
        super().__init__(
            instructions=(
                "You are a helpful voice assistant. You can help with:\n"
                "1. Stock prices: call stock_quote with ticker (e.g. AAPL, TSLA, NVDA). "
                "By default this returns the price. If the user asks specifically for "
                "trading volume (e.g. 'what's the volume on Apple'), call stock_quote "
                "again with field='volume'.\n"
                "2. Stock comparisons: ONLY call compare_stocks when the user asks to "
                "COMPARE two or more stocks. For a single stock, ALWAYS use stock_quote.\n"
                "3. Weather: call get_weather_info with a city name\n"
                "4. Time/Date: call get_time_info (e.g., 'Tokyo', 'America/New_York')\n"
                "5. Stock news: call get_news with a ticker symbol\n"
                "6. General questions: call general_search with your query\n\n"
                "Use these exact tickers for ambiguous companies: Google/Alphabet "
                "-> GOOGL (not GOOG). When in doubt about a ticker, prefer the "
                "primary/most commonly traded class of shares.\n\n"
                "If the user corrects themselves mid-request (says 'actually', "
                "'wait', 'no', or names a different company), treat it as a new "
                "request — do not report on the old one. This also applies if they "
                "switch from asking about price to asking about volume on the same "
                "ticker, or vice versa — treat that switch as a new request too.\n\n"
                "Keep replies brief: one to two sentences, written to be spoken "
                "aloud, not read. For stock prices, state the price and percent change plainly, "
                "e.g. 'Tesla is at $242.10, up 1.8% today.' "
                "For comparisons: 'Apple is at $150.20 up 0.5%, Tesla is at $242.10 up 1.8%'"
            ),
        )
        self.orchestrator = orchestrator
        self.session_id = session_id
        self.room = room
        self.last_broadcast_agent_text = ""
        self.turn_number = 0
        self.current_user_text = ""
        self._has_greeted = False  # Prevent duplicate greetings

    async def broadcast(self, event_type: str, data: dict) -> None:
        """Broadcast real-time JSON events across the LiveKit Room DataChannel."""
        if not self.room or not getattr(self.room, "local_participant", None):
            return
        try:
            payload = json.dumps({"type": event_type, "timestamp": time.time(), **data})
            await self.room.local_participant.publish_data(payload, reliable=True)
        except Exception as e:
            logger.debug(f"Could not broadcast data channel event {event_type}: {e}")

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Called when user speech ends, right before LLM reasoning."""
        text = new_message.text_content
        logger.info(f"User turn completed: '{text}'")
        if text:
            self.current_user_text = text
            self.turn_number += 1
            self.orchestrator.metrics.mark("utterance_received")

            version, is_new, intent = await self.orchestrator.on_user_utterance(
                text, is_final=True
            )

            # Persist user turn in SQLite
            try:
                repo.record_turn(
                    session_id=self.session_id,
                    turn_number=self.turn_number,
                    turn_version=version,
                    sender="user",
                    text=text,
                    is_interrupted=is_new,
                )
            except Exception as e:
                logger.warning(f"Failed to record user turn to DB: {e}")

            if is_new:
                await self.broadcast("interruption", {
                    "version": version,
                    "text": text,
                    "intent": intent
                })

    async def on_enter(self) -> None:
        """Called when the agent enters the session. Only greet once."""
        # Only send greeting if not already greeted
        if not self._has_greeted:
            self._has_greeted = True
            await self.session.generate_reply(
                instructions="Greet the user briefly, introduce yourself as a voice assistant that can help with stocks, weather, time, news, and general questions. Keep it to one sentence."
            )

    # =============================================
    # STOCK TOOLS
    # =============================================

    @function_tool()
    async def stock_quote(self, context: RunContext, symbol: str, field: str = "price") -> str:
        """Look up a live stock quote for a single ticker."""
        sym = symbol.strip().upper()
        version = self.orchestrator.state.turn_version
        intent = {"symbols": [sym], "field": field}
        t0 = time.time()
        
        # Mark tool start on orchestrator
        self.orchestrator.mark_tool_started()

        tool_db_id = repo.record_tool_execution(
            session_id=self.session_id,
            turn_version=version,
            tool_name="stock_quote",
            parameters={"symbol": sym, "field": field},
            status="running",
        )

        await self.broadcast("tool_start", {
            "name": f"stock_quote({sym})",
            "symbol": sym,
            "field": field,
            "version": version
        })

        result = await self.orchestrator.tool_mgr.run(
            version, get_stock_quote(intent)
        )
        latency_ms = round((time.time() - t0) * 1000, 1)
        
        # Mark tool resolved
        self.orchestrator.mark_tool_resolved()

        if result is None:
            repo.update_tool_execution(
                tool_db_id,
                status="cancelled",
                latency_ms=latency_ms,
                error="Superseded by user interruption"
            )
            await self.broadcast("tool_cancel", {
                "name": f"stock_quote({sym})",
                "symbol": sym,
                "stale_version": version,
                "reason": "User interruption / superseded"
            })
            return "That request was superseded by a newer one — no result to report."

        if "error" in result:
            repo.update_tool_execution(
                tool_db_id,
                status="error",
                latency_ms=latency_ms,
                error=result["error"]
            )
            await self.broadcast("tool_complete", {
                "name": f"stock_quote({sym})",
                "version": version,
                "error": result["error"],
                "latency_ms": latency_ms
            })
            return f"Couldn't get data for {sym}: {result['error']}"

        repo.update_tool_execution(
            tool_db_id,
            status="success",
            latency_ms=latency_ms,
            result=result
        )

        if result.get("field") == "volume" and result.get("note"):
            spoken_text = result["note"]
        else:
            pct_val = result.get('percent_change') or 0.0
            change_val = result.get('change') or 0.0
            spoken_text = (
                f"{result['symbol']} is at ${result['current_price']}, "
                f"{'up' if pct_val >= 0 else 'down'} "
                f"{abs(pct_val):.2f}% today."
            )

        self.last_broadcast_agent_text = spoken_text
        
        # Mark response spoken - this will trigger metrics logging
        self.orchestrator.mark_response_spoken()
        
        self.turn_number += 1

        repo.record_turn(
            session_id=self.session_id,
            turn_number=self.turn_number,
            turn_version=version,
            sender="agent",
            text=spoken_text,
            latency_ms=latency_ms,
        )

        await self.broadcast("tool_complete", {
            "name": f"stock_quote({sym})",
            "version": version,
            "data": result,
            "latency_ms": latency_ms
        })
        await self.broadcast("agent_response", {
            "text": spoken_text,
            "version": version,
            "stock_data": {
                "symbol": result["symbol"],
                "name": result["symbol"],
                "price": f"${result['current_price']}",
                "change": f"{'+' if change_val >= 0 else ''}{change_val:.2f} ({'+' if pct_val >= 0 else ''}{pct_val:.2f}%)",
                "time": "Real-time",
                "isPositive": pct_val >= 0
            }
        })
        return spoken_text

    @function_tool()
    async def compare_stocks(self, context: RunContext, symbols: str, field: str = "price") -> str:
        """Compare live stock quotes for multiple tickers. ONLY use when user asks to compare 2+ stocks."""
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        
        # Guard: compare_stocks requires at least 2 symbols
        if len(symbol_list) < 2:
            return "I need at least two ticker symbols to compare. For a single stock, please use stock_quote."

        version = self.orchestrator.state.turn_version
        intent = {"symbols": symbol_list, "field": field, "is_comparison": True}
        t0 = time.time()
        
        self.orchestrator.mark_tool_started()

        tool_db_id = repo.record_tool_execution(
            session_id=self.session_id,
            turn_version=version,
            tool_name="compare_stocks",
            parameters={"symbols": symbol_list, "field": field},
            status="running",
        )

        await self.broadcast("tool_start", {
            "name": f"compare_stocks({','.join(symbol_list)})",
            "symbols": symbol_list,
            "version": version
        })

        result = await self.orchestrator.tool_mgr.run(
            version, compare_stocks(intent)
        )
        latency_ms = round((time.time() - t0) * 1000, 1)
        
        self.orchestrator.mark_tool_resolved()

        if result is None:
            repo.update_tool_execution(
                tool_db_id,
                status="cancelled",
                latency_ms=latency_ms,
                error="Superseded by user interruption"
            )
            await self.broadcast("tool_cancel", {
                "name": f"compare_stocks({','.join(symbol_list)})",
                "stale_version": version,
                "reason": "User interruption / superseded"
            })
            return "That request was superseded by a newer one — no result to report."

        if "error" in result:
            repo.update_tool_execution(
                tool_db_id,
                status="error",
                latency_ms=latency_ms,
                error=result["error"]
            )
            await self.broadcast("tool_complete", {
                "name": f"compare_stocks({','.join(symbol_list)})",
                "version": version,
                "error": result["error"],
                "latency_ms": latency_ms
            })
            return f"Couldn't compare: {result['error']}"

        repo.update_tool_execution(
            tool_db_id,
            status="success",
            latency_ms=latency_ms,
            result=result
        )

        parts = []
        for data in result.get("results", []):
            price = data["current_price"]
            change = data["percent_change"]
            direction = "up" if change >= 0 else "down"
            parts.append(f"{data['symbol']} at ${price} {direction} {abs(change):.2f}%")

        spoken_text = " vs ".join(parts) if parts else "No comparison data available."
        self.last_broadcast_agent_text = spoken_text
        
        self.orchestrator.mark_response_spoken()
        self.turn_number += 1

        repo.record_turn(
            session_id=self.session_id,
            turn_number=self.turn_number,
            turn_version=version,
            sender="agent",
            text=spoken_text,
            latency_ms=latency_ms,
        )

        await self.broadcast("tool_complete", {
            "name": f"compare_stocks({','.join(symbol_list)})",
            "version": version,
            "data": result,
            "latency_ms": latency_ms
        })
        await self.broadcast("agent_response", {
            "text": spoken_text,
            "version": version
        })
        return spoken_text

    # =============================================
    # SEARCH TOOLS (Weather, Time, News, General)
    # =============================================

    @function_tool()
    async def get_weather_info(self, context: RunContext, city: str) -> str:
        """Get current weather for a city."""
        clean_city = city.strip()
        version = self.orchestrator.state.turn_version
        t0 = time.time()
        
        self.orchestrator.mark_tool_started()

        tool_db_id = repo.record_tool_execution(
            session_id=self.session_id,
            turn_version=version,
            tool_name="get_weather",
            parameters={"city": clean_city},
            status="running",
        )

        await self.broadcast("tool_start", {"name": f"get_weather({clean_city})", "version": version})
        data = await self.orchestrator.tool_mgr.run(
            version, get_weather(clean_city)
        )
        latency_ms = round((time.time() - t0) * 1000, 1)
        
        self.orchestrator.mark_tool_resolved()

        if data is None:
            repo.update_tool_execution(tool_db_id, status="cancelled", latency_ms=latency_ms, error="Superseded")
            await self.broadcast("tool_cancel", {"name": f"get_weather({clean_city})", "stale_version": version, "reason": "superseded"})
            return "That request was superseded by a newer one — no result to report."

        if "error" in data:
            repo.update_tool_execution(tool_db_id, status="error", latency_ms=latency_ms, error=data["error"])
            await self.broadcast("tool_complete", {"name": f"get_weather({clean_city})", "version": version, "error": data["error"], "latency_ms": latency_ms})
            return f"Couldn't get weather: {data['error']}"

        repo.update_tool_execution(tool_db_id, status="success", latency_ms=latency_ms, result=data)

        res_text = (
            f"Weather in {data['location']}: {data['temperature_c']}°C, {data['condition']}, "
            f"humidity {data['humidity']}%, wind {data['wind_speed_kmh']} km/h."
        )
        self.last_broadcast_agent_text = res_text
        
        self.orchestrator.mark_response_spoken()
        self.turn_number += 1

        repo.record_turn(
            session_id=self.session_id,
            turn_number=self.turn_number,
            turn_version=version,
            sender="agent",
            text=res_text,
            latency_ms=latency_ms,
        )

        await self.broadcast("tool_complete", {"name": f"get_weather({clean_city})", "version": version, "data": data, "latency_ms": latency_ms})
        await self.broadcast("agent_response", {"text": res_text, "version": version})
        return res_text

    @function_tool()
    async def get_time_info(self, context: RunContext, timezone: str = "America/New_York") -> str:
        """Get current time and date for a timezone or city."""
        clean_tz = timezone.strip()
        version = self.orchestrator.state.turn_version
        t0 = time.time()
        
        self.orchestrator.mark_tool_started()

        tool_db_id = repo.record_tool_execution(
            session_id=self.session_id,
            turn_version=version,
            tool_name="get_time",
            parameters={"timezone": clean_tz},
            status="running",
        )

        await self.broadcast("tool_start", {"name": f"get_time({clean_tz})", "version": version})
        data = await self.orchestrator.tool_mgr.run(
            version, get_time(clean_tz)
        )
        latency_ms = round((time.time() - t0) * 1000, 1)
        
        self.orchestrator.mark_tool_resolved()

        if data is None:
            repo.update_tool_execution(tool_db_id, status="cancelled", latency_ms=latency_ms, error="Superseded")
            await self.broadcast("tool_cancel", {"name": f"get_time({clean_tz})", "stale_version": version, "reason": "superseded"})
            return "That request was superseded by a newer one — no result to report."

        if "error" in data:
            repo.update_tool_execution(tool_db_id, status="error", latency_ms=latency_ms, error=data["error"])
            await self.broadcast("tool_complete", {"name": f"get_time({clean_tz})", "version": version, "error": data["error"], "latency_ms": latency_ms})
            return f"Couldn't get time: {data['error']}"

        repo.update_tool_execution(tool_db_id, status="success", latency_ms=latency_ms, result=data)

        res_text = f"{data['day_of_week']}, {data['datetime']} ({data['timezone']})"
        self.last_broadcast_agent_text = res_text
        
        self.orchestrator.mark_response_spoken()
        self.turn_number += 1

        repo.record_turn(
            session_id=self.session_id,
            turn_number=self.turn_number,
            turn_version=version,
            sender="agent",
            text=res_text,
            latency_ms=latency_ms,
        )

        await self.broadcast("tool_complete", {"name": f"get_time({clean_tz})", "version": version, "data": data, "latency_ms": latency_ms})
        await self.broadcast("agent_response", {"text": res_text, "version": version})
        return res_text

    @function_tool()
    async def get_news(self, context: RunContext, symbol: str) -> str:
        """Get latest news for a stock ticker."""
        clean_sym = symbol.strip().upper()
        version = self.orchestrator.state.turn_version
        t0 = time.time()
        
        self.orchestrator.mark_tool_started()

        tool_db_id = repo.record_tool_execution(
            session_id=self.session_id,
            turn_version=version,
            tool_name="get_news",
            parameters={"symbol": clean_sym},
            status="running",
        )

        await self.broadcast("tool_start", {"name": f"get_news({clean_sym})", "version": version})
        data = await self.orchestrator.tool_mgr.run(
            version, get_stock_news(clean_sym, limit=2)
        )
        latency_ms = round((time.time() - t0) * 1000, 1)
        
        self.orchestrator.mark_tool_resolved()

        if data is None:
            repo.update_tool_execution(tool_db_id, status="cancelled", latency_ms=latency_ms, error="Superseded")
            await self.broadcast("tool_cancel", {"name": f"get_news({clean_sym})", "stale_version": version, "reason": "superseded"})
            return "That request was superseded by a newer one — no result to report."

        if "error" in data:
            repo.update_tool_execution(tool_db_id, status="error", latency_ms=latency_ms, error=data["error"])
            await self.broadcast("tool_complete", {"name": f"get_news({clean_sym})", "version": version, "error": data["error"], "latency_ms": latency_ms})
            return f"Couldn't get news: {data['error']}"

        repo.update_tool_execution(tool_db_id, status="success", latency_ms=latency_ms, result=data)

        if not data.get("news"):
            res_text = f"No recent news found for {clean_sym}."
        else:
            headlines = [f"• {item['title']} ({item['source']})" for item in data["news"][:2]]
            res_text = f"Latest news for {clean_sym}: " + " ".join(headlines)

        self.last_broadcast_agent_text = res_text
        
        self.orchestrator.mark_response_spoken()
        self.turn_number += 1

        repo.record_turn(
            session_id=self.session_id,
            turn_number=self.turn_number,
            turn_version=version,
            sender="agent",
            text=res_text,
            latency_ms=latency_ms,
        )

        await self.broadcast("tool_complete", {"name": f"get_news({clean_sym})", "version": version, "data": data, "latency_ms": latency_ms})
        await self.broadcast("agent_response", {"text": res_text, "version": version})
        return res_text

    @function_tool()
    async def general_search(self, context: RunContext, query: str) -> str:
        """Perform a general web search."""
        clean_query = query.strip()
        version = self.orchestrator.state.turn_version
        t0 = time.time()
        
        self.orchestrator.mark_tool_started()

        tool_db_id = repo.record_tool_execution(
            session_id=self.session_id,
            turn_version=version,
            tool_name="general_search",
            parameters={"query": clean_query},
            status="running",
        )

        await self.broadcast("tool_start", {"name": f"search({clean_query})", "version": version})
        data = await self.orchestrator.tool_mgr.run(
            version, search(clean_query, limit=3)
        )
        latency_ms = round((time.time() - t0) * 1000, 1)
        
        self.orchestrator.mark_tool_resolved()

        if data is None:
            repo.update_tool_execution(tool_db_id, status="cancelled", latency_ms=latency_ms, error="Superseded")
            await self.broadcast("tool_cancel", {"name": f"search({clean_query})", "stale_version": version, "reason": "superseded"})
            return "That request was superseded by a newer one — no result to report."

        if "error" in data:
            repo.update_tool_execution(tool_db_id, status="error", latency_ms=latency_ms, error=data["error"])
            await self.broadcast("tool_complete", {"name": f"search({clean_query})", "version": version, "error": data["error"], "latency_ms": latency_ms})
            return f"Couldn't search: {data['error']}"

        repo.update_tool_execution(tool_db_id, status="success", latency_ms=latency_ms, result=data)

        if not data.get("results"):
            res_text = f"No results found for '{clean_query}'."
        else:
            results = [f"• {item['title']}" for item in data["results"][:3]]
            res_text = f"Here's what I found: " + " ".join(results)

        self.last_broadcast_agent_text = res_text
        
        self.orchestrator.mark_response_spoken()
        self.turn_number += 1

        repo.record_turn(
            session_id=self.session_id,
            turn_number=self.turn_number,
            turn_version=version,
            sender="agent",
            text=res_text,
            latency_ms=latency_ms,
        )

        await self.broadcast("tool_complete", {"name": f"search({clean_query})", "version": version, "data": data, "latency_ms": latency_ms})
        await self.broadcast("agent_response", {"text": res_text, "version": version})
        return res_text


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    room_name = ctx.room.name
    ctx.log_context_fields = {"room": room_name}

    session_id = room_name[5:] if room_name.startswith("room_") else room_name

    # Ensure session exists in SQLite
    if not repo.get_session(session_id):
        repo.create_session(session_id=session_id, room_name=room_name)

    session_settings = repo.get_settings(session_id)

    orchestrator = Orchestrator()

    llm = _build_llm()
    if llm is None:
        raise RuntimeError(
            "No LLM provider configured. Set GROQ_API_KEY (free, "
            "recommended) or OPENAI_API_KEY in your .env before running."
        )

    tts_plugin = build_rime_tts(
        model=session_settings.get("voice_model"),
        speaker=session_settings.get("voice_speaker"),
        speed=session_settings.get("voice_speed"),
    )

    session = AgentSession(
        stt=deepgram.STT(),
        llm=llm,
        tts=tts_plugin,
        vad=ctx.proc.userdata["vad"],
        allow_interruptions=True,
    )

    agent = ContinuityAgent(orchestrator, session_id=session_id, room=ctx.room)

    @session.on("user_input_transcribed")
    def on_transcript(transcript) -> None:
        if transcript.is_final:
            logger.info(f"Final transcript: {transcript.transcript}")
        else:
            logger.debug(f"Interim: {transcript.transcript}")

        async def _handle_transcript():
            await agent.broadcast("transcript", {
                "sender": "You",
                "text": transcript.transcript,
                "is_final": transcript.is_final
            })

        asyncio.create_task(_handle_transcript())

    @session.on("conversation_item_added")
    def on_item_added(ev) -> None:
        try:
            item = ev.item
            if getattr(item, "role", None) == "assistant":
                text = getattr(item, "text_content", None) or "".join(getattr(item, "content", []))
                if text and text.strip() and text.strip() != agent.last_broadcast_agent_text.strip():
                    agent.last_broadcast_agent_text = text.strip()
                    agent.turn_number += 1
                    try:
                        repo.record_turn(
                            session_id=agent.session_id,
                            turn_number=agent.turn_number,
                            turn_version=orchestrator.state.turn_version,
                            sender="agent",
                            text=text.strip(),
                        )
                    except Exception as e:
                        logger.warning(f"Error persisting conversational agent turn: {e}")

                    logger.info(f"Broadcasting assistant response: {text}")
                    asyncio.create_task(agent.broadcast("agent_response", {
                        "text": text,
                        "version": orchestrator.state.turn_version
                    }))
        except Exception as e:
            logger.warning(f"Error handling conversation item: {e}")

    @ctx.room.on("data_received")
    def on_data_received(data_packet) -> None:
        try:
            raw = data_packet.data
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            if data.get("type") == "user_chat":
                text = data.get("text")
                if text:
                    logger.info(f"Received text chat from frontend: {text}")
                    asyncio.create_task(handle_text_chat(session, text, agent))
        except Exception as e:
            logger.warning(f"Error handling data channel packet: {e}")

    async def handle_text_chat(session: AgentSession, text: str, agent: ContinuityAgent):
        """Handle text chat input from the frontend."""
        try:
            # If the user sent "Hello" and we already greeted, don't respond with another greeting
            # Let the orchestrator handle it naturally
            await session.generate_reply(user_input=text)
        except Exception as e:
            logger.error(f"Error processing text chat: {e}")

    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()


if __name__ == "__main__":
    from livekit.agents import cli
    cli.run_app(server)