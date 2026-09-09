# System Architecture & Technical Specifications

This document outlines the architecture, concurrency model, data persistence, and security controls for the DataForge Rime Starter Voice Agent.

---

## 1. High-Level Architecture

The system operates across three decoupled layers:

1. **Client Layer (SPA)**:
   - Modern browser WebRTC client (`frontend/js/livekit.js`).
   - Dynamic session provisioning via `POST /api/session`.
   - Real-time rendering of turns, tool executions, and voice settings.

2. **Server & Gateway Layer (`frontend/serve.py`)**:
   - HTTP/1.1 multi-threaded server with security headers, CORS origin verification, and IP rate limiting.
   - REST API for session isolation, SQLite query endpoints, and short-lived LiveKit access tokens (30-minute TTL).
   - Serves de-mocked SPA assets without external CDN dependencies for core state.

3. **Voice Agent Worker Layer (`agent/main.py`)**:
   - LiveKit WebRTC transport connecting browser microphone to pipeline.
   - Deepgram Nova-2 streaming STT with Silero Voice Activity Detection (VAD).
   - Heuristic intent router (`agent/router.py`) providing sub-millisecond intent extraction and version tracking.
   - LLM reasoning (Groq Llama-3 / OpenAI GPT-4o-mini) executing tools decorated with `@function_tool`.
   - Rime Coda TTS streaming synthesized audio over WebSocket.
   - Persistent SQLite WAL repository (`agent/db/repository.py`) recording all sessions, turns, tool latencies, and user settings.

---

## 2. Deterministic Turn-Version Fencing

### The Race Condition
When a user asks:
```
Turn 1: "What's Apple's price?"
        └── Agent starts asynchronous fetch: get_stock_quote("AAPL") [latency: ~500ms]
Turn 2 (Barge-in at 200ms): "Actually, check Nvidia instead"
        └── Agent receives new instruction while Turn 1 fetch is still in flight
```

Without fencing, Turn 1 completes at 500ms and sends Apple's price to the LLM/TTS. The agent speaks the stale Apple price before answering about Nvidia.

### The Fencing Protocol
```
User Utterance (STT Finalized)
      │
      ▼
router.route_intent(text, current_intent)
      │
      ├─► Is New Intent? ──YES──► 1. tool_mgr.cancel_active()
      │                           2. state.bump_version() -> Version N+1
      │                           3. DB: record_turn(version=N+1, is_interrupted=True)
      ▼
LLM Function Tool Execution
      │
      ▼
tool_mgr.run(version, coroutine)
      │
      ├─► Before Execution: If not state.is_current(version) ──► Discard immediately
      ├─► In Flight: If cancelled via asyncio.CancelledError  ──► Discard immediately
      └─► After Execution: If not state.is_current(version)  ──► Discard, return None
            │
            ▼ (If Current)
Return clean data to LLM -> Synthesize spoken speech with Rime TTS
```

---

## 3. Database Persistence Schema (SQLite WAL)

All application state is persisted in `data/agent.db` with Write-Ahead Logging (`PRAGMA journal_mode = WAL;`) and enforced Foreign Keys:

```sql
-- Isolated user sessions
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    room_name TEXT UNIQUE NOT NULL,
    client_ip TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Chronological transcript history
CREATE TABLE turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    turn_version INTEGER NOT NULL,
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    is_interrupted INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Tool execution auditing and latency telemetry
CREATE TABLE tool_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_version INTEGER NOT NULL,
    tool_name TEXT NOT NULL,
    parameters TEXT,
    status TEXT NOT NULL,
    latency_ms REAL,
    result TEXT,
    error TEXT,
    timestamp TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

-- Per-session voice & synthesis configuration
CREATE TABLE settings (
    session_id TEXT PRIMARY KEY,
    voice_model TEXT NOT NULL DEFAULT 'coda',
    voice_speaker TEXT NOT NULL DEFAULT 'celeste',
    voice_speed REAL NOT NULL DEFAULT 1.0,
    auto_speak INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
```

---

## 4. Live Tool Pipeline & Failover

### Market Data (`agent/tools/stocks.py`)
- **Primary Source**: Finnhub REST API `/quote` endpoint (low-latency real-time market data).
- **Automatic Fallback 1**: For volume queries (`field == "volume"`), Finnhub free-tier returns `None`. The system automatically routes to `yfinance` to fetch real trading volumes.
- **Automatic Fallback 2**: If Finnhub returns an HTTP error, network timeout, or rate-limit code, the system seamlessly falls back to `yfinance` without user interruption.
- **Spoken Formatting**: Numbers are converted into natural spoken English (e.g. `9,030,436` -> `"9.03 million shares"`).

### Local Search & Time (`agent/tools/search.py`)
- Standard Python `zoneinfo` time zone conversion (100% reliable, zero network latency).
- Public web fallback for weather and headlines.

---

## 5. Security & Isolation Controls

| Vector | Mitigation |
|---|---|
| **Multi-Tenant Cross-Talk** | Cryptographically isolated LiveKit room per session (`room_{session_id}`). |
| **Token Hijacking** | Access tokens minted with a 30-minute strict TTL (`datetime.timedelta(minutes=30)`). |
| **XSS & Injection** | Strict CSP: `default-src 'self'`, `script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net`. |
| **Clickjacking** | `X-Frame-Options: DENY`. |
| **MIME Sniffing** | `X-Content-Type-Options: nosniff`. |
| **Denial of Service** | Sliding-window in-memory IP rate limiting and 64KB maximum body guards. |
| **Secret Leaks** | Zero tracked secrets in repository; environment startup validator masks all logging. |