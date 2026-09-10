"""
LiveKit transport with OpenAI primary / Groq fallback, Rime TTS, and SQLite telemetry.

Turn version fencing and tool execution tracking are integrated directly with SQLite
persistence and real-time LiveKit room data channel broadcasts.
"""

import asyncio
import os
import json
import time
from typing import Optional, Dict, Any
from dotenv import load_dotenv

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
from livekit.agents import llm as llm_module
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

# Cap on how many past items we retain in the LLM chat context.
MAX_CHAT_CONTEXT_ITEMS = 16


def _build_llm():
    """
    OpenAI is the primary provider. Groq is the fallback.

    FallbackAdapter tries providers in order and fails over on retryable
    errors (429, 5xx, connection errors). Because OpenAI and Groq have
    separate rate limits and separate billing accounts, the fallback is
    effectively a second independent quota.
    """
    primary = None
    fallback = None

    if os.getenv("OPENAI_API_KEY"):
        oa_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        logger.info(f"Primary LLM: OpenAI (model={oa_model})")
        primary = openai.LLM(model=oa_model)

    if os.getenv("GROQ_API_KEY") and HAS_GROQ:
        gq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        logger.info(f"Fallback LLM: Groq (model={gq_model})")
        fallback = groq.LLM(model=gq_model)

    if primary and fallback:
        return llm_module.FallbackAdapter([primary, fallback])
    if primary:
        return primary
    if fallback:
        logger.warning("Only Groq available — running without OpenAI primary")
        return fallback

    logger.warning("No LLM API key found (need OPENAI_API_KEY or GROQ_API_KEY)")
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
                "Voice assistant for stock prices, stock comparisons, weather, "
                "time, stock news, and general web search. Use the provided tools. "
                "Never invent a ticker symbol. Reply in one or two spoken sentences."
            ),
        )
        self.orchestrator = orchestrator
        self.session_id = session_id
        self.room = room
        self.last_broadcast_agent_text = ""
        self.turn_number = 0
        self.current_user_text = ""
        self._has_greeted = False

    async def broadcast(self, event_type: str, data: dict) -> None:
        """Broadcast real-time JSON events across the LiveKit Room DataChannel."""
        if not self.room or not getattr(self.room, "local_participant", None):
            return
        try:
            payload = json.dumps({"type": event_type, "timestamp": time.time(), **data})
            await self.room.local_participant.publish_data(payload, reliable=True)
        except Exception as e:
            logger.debug(f"Could not broadcast data channel event {event_type}: {e}")

    def _truncate_chat_ctx(self, turn_ctx: llm.ChatContext) -> None:
        """
        Bound the chat context before each LLM turn.

        ChatContext.truncate() is the supported API in livekit-agents 1.7/1.8 —
        the AgentSession constructor does not accept max_chat_history, so
        truncation is done here instead. The turn_ctx passed to
        on_user_turn_completed is a per-turn working copy.
        """
        try:
            if len(turn_ctx.items) > MAX_CHAT_CONTEXT_ITEMS:
                turn_ctx.truncate(max_items=MAX_CHAT_CONTEXT_ITEMS)
                logger.debug(
                    f"Truncated chat context to {MAX_CHAT_CONTEXT_ITEMS} items"
                )
        except Exception as e:
            logger.debug(f"Chat context truncate skipped: {e}")

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Called when user speech ends, right before LLM reasoning."""
        self._truncate_chat_ctx(turn_ctx)

        text = new_message.text_content
        logger.info(f"User turn completed: '{text}'")
        if text:
            self.current_user_text = text
            self.turn_number += 1
            self.orchestrator.metrics.mark("utterance_received")

            version, is_new, intent = await self.orchestrator.on_user_utterance(
                text, is_final=True
            )

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
                    "intent": intent,
                })

    async def on_enter(self) -> None:
        """Called when the agent enters the session. Only greet once."""
        if not self._has_greeted:
            self._has_greeted = True
            await self.session.generate_reply(
                instructions=(
                    "Greet the user briefly in one sentence as a voice assistant "
                    "that helps with stocks, weather, time, news, and general questions."
                )
            )

    # =============================================
    # Shared tool wrapper
    # =============================================
    async def _run_tool(
        self,
        tool_name: str,
        params: dict,
        coro,
        spoken_formatter,
        broadcast_extra: Optional[dict] = None,
    ) -> str:
        version = self.orchestrator.state.turn_version
        t0 = time.time()

        self.orchestrator.mark_tool_started()

        tool_db_id = repo.record_tool_execution(
            session_id=self.session_id,
            turn_version=version,
            tool_name=tool_name,
            parameters=params,
            status="running",
        )

        await self.broadcast("tool_start", {
            "name": tool_name,
            "version": version,
            **(broadcast_extra or {}),
        })

        result = await self.orchestrator.tool_mgr.run(version, coro)
        latency_ms = round((time.time() - t0) * 1000, 1)

        self.orchestrator.mark_tool_resolved()

        if result is None:
            repo.update_tool_execution(
                tool_db_id, status="cancelled",
                latency_ms=latency_ms,
                error="Superseded by user interruption",
            )
            await self.broadcast("tool_cancel", {
                "name": tool_name,
                "stale_version": version,
                "reason": "User interruption / superseded",
                **(broadcast_extra or {}),
            })
            return "That request was superseded by a newer one — no result to report."

        if isinstance(result, dict) and "error" in result:
            repo.update_tool_execution(
                tool_db_id, status="error",
                latency_ms=latency_ms, error=result["error"],
            )
            await self.broadcast("tool_complete", {
                "name": tool_name, "version": version,
                "error": result["error"], "latency_ms": latency_ms,
            })
            return f"Couldn't complete {tool_name}: {result['error']}"

        repo.update_tool_execution(
            tool_db_id, status="success",
            latency_ms=latency_ms, result=result,
        )

        spoken_text = spoken_formatter(result)
        self.last_broadcast_agent_text = spoken_text

        self.orchestrator.mark_response_spoken()

        await self.broadcast("tool_complete", {
            "name": tool_name, "version": version,
            "data": result, "latency_ms": latency_ms,
        })
        await self.broadcast("agent_response", {
            "text": spoken_text, "version": version,
        })
        return spoken_text

    # =============================================
    # STOCK TOOLS
    # =============================================

    @function_tool()
    async def stock_quote(
        self,
        context: RunContext,
        symbol: str,
        field: Optional[str] = "price",
    ) -> str:
        """
        Look up a live stock quote for a single ticker.

        Args:
            symbol: Ticker symbol, e.g. AAPL, TSLA, NVDA.
            field: Either "price" (default) or "volume".
        """
        sym = symbol.strip().upper()
        field_value = (field or "price").lower()
        intent = {"symbols": [sym], "field": field_value}

        def fmt(result: dict) -> str:
            pct_val = result.get("percent_change") or 0.0
            if result.get("field") == "volume" and result.get("note"):
                return result["note"]
            return (
                f"{result['symbol']} is at ${result['current_price']}, "
                f"{'up' if pct_val >= 0 else 'down'} "
                f"{abs(pct_val):.2f}% today."
            )

        return await self._run_tool(
            tool_name="stock_quote",
            params={"symbol": sym, "field": field_value},
            coro=get_stock_quote(intent),
            spoken_formatter=fmt,
            broadcast_extra={"symbol": sym, "field": field_value},
        )

    @function_tool()
    async def compare_stocks(
        self,
        context: RunContext,
        symbols: str,
        field: Optional[str] = "price",
    ) -> str:
        """
        Compare live stock quotes for two or more tickers.
        Use ONLY when the user explicitly asks to compare.

        Args:
            symbols: Comma-separated list, e.g. "AAPL,TSLA".
            field: Either "price" (default) or "volume".
        """
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]

        if len(symbol_list) < 2:
            return "I need at least two ticker symbols to compare. For a single stock, please use stock_quote."

        field_value = (field or "price").lower()
        intent = {"symbols": symbol_list, "field": field_value, "is_comparison": True}

        def fmt(result: dict) -> str:
            parts = []
            for data in result.get("results", []):
                price = data["current_price"]
                change = data["percent_change"]
                direction = "up" if change >= 0 else "down"
                parts.append(f"{data['symbol']} at ${price} {direction} {abs(change):.2f}%")
            return " vs ".join(parts) if parts else "No comparison data available."

        return await self._run_tool(
            tool_name="compare_stocks",
            params={"symbols": symbol_list, "field": field_value},
            coro=compare_stocks(intent),
            spoken_formatter=fmt,
            broadcast_extra={"symbols": symbol_list},
        )

    # =============================================
    # SEARCH TOOLS
    # =============================================

    @function_tool()
    async def get_weather_info(self, context: RunContext, city: str) -> str:
        """
        Get current weather for a city.

        Args:
            city: City name, e.g. "London", "Cape Town", "Delhi".
        """
        clean_city = city.strip()

        def fmt(data: dict) -> str:
            return (
                f"Weather in {data['location']}: {data['temperature_c']}°C, "
                f"{data['condition']}, humidity {data['humidity']}%, "
                f"wind {data['wind_speed_kmh']} km/h."
            )

        return await self._run_tool(
            tool_name="get_weather",
            params={"city": clean_city},
            coro=get_weather(clean_city),
            spoken_formatter=fmt,
            broadcast_extra={"city": clean_city},
        )

    @function_tool()
    async def get_time_info(
        self,
        context: RunContext,
        timezone: Optional[str] = "America/New_York",
    ) -> str:
        """
        Get current time and date for a timezone or city.

        Args:
            timezone: IANA timezone or common city name, e.g. "Asia/Kolkata",
                "Cape Town", "New Zealand".
        """
        clean_tz = (timezone or "America/New_York").strip()

        def fmt(data: dict) -> str:
            return f"{data['day_of_week']}, {data['datetime']} ({data['timezone']})"

        return await self._run_tool(
            tool_name="get_time",
            params={"timezone": clean_tz},
            coro=get_time(clean_tz),
            spoken_formatter=fmt,
            broadcast_extra={"timezone": clean_tz},
        )

    @function_tool()
    async def get_news(self, context: RunContext, symbol: str) -> str:
        """
        Get latest news headlines for a stock ticker.

        Args:
            symbol: Ticker symbol, e.g. AAPL, TSLA, NVDA.
        """
        clean_sym = symbol.strip().upper()

        def fmt(data: dict) -> str:
            if not data.get("news"):
                return f"No recent news found for {clean_sym}."
            headlines = [f"• {item['title']} ({item['source']})" for item in data["news"][:2]]
            return f"Latest news for {clean_sym}: " + " ".join(headlines)

        return await self._run_tool(
            tool_name="get_news",
            params={"symbol": clean_sym},
            coro=get_stock_news(clean_sym, limit=2),
            spoken_formatter=fmt,
            broadcast_extra={"symbol": clean_sym},
        )

    @function_tool()
    async def general_search(self, context: RunContext, query: str) -> str:
        """
        Perform a general web search for facts not covered by other tools.

        Args:
            query: The search query, e.g. "population of Iceland".
        """
        clean_query = query.strip()

        def fmt(data: dict) -> str:
            if not data.get("results"):
                return f"No results found for '{clean_query}'."
            results = [f"• {item['title']}" for item in data["results"][:3]]
            return "Here's what I found: " + " ".join(results)

        return await self._run_tool(
            tool_name="general_search",
            params={"query": clean_query},
            coro=search(clean_query, limit=3),
            spoken_formatter=fmt,
            broadcast_extra={"query": clean_query},
        )


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    room_name = ctx.room.name
    ctx.log_context_fields = {"room": room_name}

    session_id = room_name[5:] if room_name.startswith("room_") else room_name

    if not repo.get_session(session_id):
        repo.create_session(session_id=session_id, room_name=room_name)

    session_settings = repo.get_settings(session_id)

    orchestrator = Orchestrator()

    llm = _build_llm()
    if llm is None:
        raise RuntimeError(
            "No LLM provider configured. Set OPENAI_API_KEY (primary) and/or "
            "GROQ_API_KEY (fallback) in your .env before running."
        )

    tts_plugin = build_rime_tts(
        model=session_settings.get("voice_model"),
        speaker=session_settings.get("voice_speaker"),
        speed=session_settings.get("voice_speed"),
    )

    # NOTE: do NOT pass max_chat_history — it is not a valid AgentSession
    # kwarg in livekit-agents 1.7/1.8, and passing it raises TypeError
    # before the session is constructed. Chat history is bounded inside
    # ContinuityAgent.on_user_turn_completed via ChatContext.truncate().
    #
    # preemptive_generation=False halves LLM request volume. With preemptive
    # on, every user utterance triggers two LLM calls: one speculative call
    # as the transcript settles, and one real call after the turn commits.
    # That doubling is what pushed the earlier Groq-only runs into 429s.
    session = AgentSession(
        stt=deepgram.STT(),
        llm=llm,
        tts=tts_plugin,
        vad=ctx.proc.userdata["vad"],
        allow_interruptions=True,
        preemptive_generation=False,
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
                "is_final": transcript.is_final,
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
                        "version": orchestrator.state.turn_version,
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
        try:
            await session.generate_reply(user_input=text)
        except Exception as e:
            logger.error(f"Error processing text chat: {e}")

    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()


if __name__ == "__main__":
    from livekit.agents import cli
    cli.run_app(server)