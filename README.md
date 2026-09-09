# Production-Ready DataForge Rime Voice Agent

> DataForge 2026 — Rime Hackathon Challenge submission.
> Production-grade, multi-user WebRTC voice agent with deterministic turn-version fencing, persistent SQLite WAL storage, isolated LiveKit sessions, live market telemetry, and zero mock dependencies.

---

## 🎯 The Core Claim

> "While our agent is running a multi-second tool call (such as a real-time market quote lookup), the user can interrupt mid-flight to change the ticker, request a different metric (price → volume), or ask about multiple stocks. The spoken response will **never** reflect a stale result — it always matches what the user last actually said."

---

## 🎙️ The Hard Voice Problem: Interruption & Recovery

In conversational voice applications, user barge-in during asynchronous tool execution creates race conditions. If a user asks *"What's Apple's price?"* and then interrupts mid-lookup with *"Wait, check Nvidia instead"*, naive agents speak **both** results: the stale Apple price followed by the Nvidia quote.

We solve this using **deterministic turn-version fencing**:
1. Every user turn and tool dispatch is tagged with a monotonic version integer.
2. An interruption cancels active asynchronous background tasks immediately and increments the turn version.
3. When any tool completes, its dispatch version is checked against `state.is_current(version)`.
4. If outdated, the result is fenced (discarded immediately) and never passed to the LLM or synthesized by Rime TTS.
5. Fencing is 100% deterministic and independent of artificial delays or network jitter.

---

## 🏗️ Architecture & Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Client Browser UI                             │
│       (Vanilla HTML5 / Modern CSS / WebRTC Audio / REST & SSE)          │
└──────────────────┬─────────────────────────────────┬────────────────────┘
                   │ Audio Stream (WebRTC)           │ REST API / Auth
                   ▼                                 ▼
┌─────────────────────────────────────┐   ┌───────────────────────────────┐
│         LiveKit Cloud / SFU         │   │   Production HTTP Server      │
│     Isolated Room: room_{sess_id}   │   │   (frontend/serve.py :5500)   │
└──────────────────┬──────────────────┘   │   - POST /api/session (isolate│
                   │ Audio Packets        │   - POST /api/token (30m TTL) │
                   ▼                      │   - GET  /api/sessions        │
┌─────────────────────────────────────┐   │   - GET  /api/settings        │
│       Voice Agent Worker            │   │   - Security Headers & CORS   │
│       (agent/main.py dev)           │   └──────────────┬────────────────┘
│                                     │                  │
│ ┌─────────────────────────────────┐ │                  │ Read / Write
│ │ Deepgram STT (Nova-2 Speech)    │ │                  │
│ └────────────────┬────────────────┘ │                  ▼
│                  ▼                  │   ┌───────────────────────────────┐
│ ┌─────────────────────────────────┐ │   │   SQLite Database (WAL Mode)  │
│ │ State & Turn Fencing            │ │   │   (agent/db/database.py)      │
│ │ - agent/state.py                │ │   │   - sessions (isolated rooms) │
│ │ - agent/router.py               │ │   │   - turns (ordered transcript)│
│ └────────────────┬────────────────┘ │   │   - tool_executions (latency) │
│                  ▼                  │   │   - settings (voice/speed)    │
│ ┌─────────────────────────────────┐ │   └───────────────────────────────┘
│ │ LLM (Groq Llama-3 / OpenAI)     │ │
│ └────────────────┬────────────────┘ │
│                  ▼                  │
│ ┌─────────────────────────────────┐ │
│ │ Real Tools (Stocks & Search)    │ │
│ │ - Finnhub Quote API             │ │
│ │ - yfinance Volume Fallback      │ │
│ │ - ZoneInfo Local Time           │ │
│ └────────────────┬────────────────┘ │
│                  ▼                  │
│ ┌─────────────────────────────────┐ │
│ │ Rime TTS (Coda / Celeste)       │ │
│ │ WebSocket Streaming Synthesis   │ │
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11, 3.12, or 3.13
- LiveKit Cloud credentials, Rime API key, Deepgram API key, Groq or OpenAI key

### Option A: Local Installation

```bash
# 1. Clone repository
git clone <repo-url>
cd dataforge-rime-starter

# 2. Run automated setup script
# Windows (PowerShell):
.\scripts\setup.ps1
# Linux / macOS (Bash):
chmod +x scripts/setup.sh && ./scripts/setup.sh

# 3. Configure environment credentials
cp .env.example .env
# Edit .env and supply your API keys

# 4. Run automated test suite
.\venv\Scripts\python.exe -m pytest -v

# 5. Start the frontend & API server (Port 5500)
.\venv\Scripts\python.exe frontend/serve.py

# 6. Start the voice agent worker (in a separate terminal)
# Console testing mode (no WebRTC room required):
.\venv\Scripts\python.exe -m agent.main console
# OR Full LiveKit WebRTC room mode:
.\venv\Scripts\python.exe -m agent.main dev
```

Open `http://localhost:5500` to access the interactive web dashboard.

---

### Option B: Docker Containerization

```bash
# 1. Build and start server and agent containers
docker-compose up --build -d

# 2. View container logs
docker-compose logs -f

# 3. Access web dashboard
open http://localhost:5500
```

---

## 🔒 Security & Credential Isolation

- **Zero Plaintext Secrets**: The codebase contains no committed API keys. Secret validation (`agent/config.py`) masks all tokens in logs (e.g. `rim_...abcd`) and validates required keys upon boot.
- **Room Isolation**: Every user connection creates a cryptographically isolated session room (`room_{session_id}`) via `POST /api/session`. Tokens expire after 30 minutes.
- **Security Headers & CORS**: Strict CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `X-XSS-Protection`, and origin verification are enforced on all HTTP responses.
- **In-Memory IP Rate Limiting**: Built-in sliding-window limiter guards endpoints against abuse.

---

## 🧪 Verification & Automated Test Suite

The test suite validates fencing logic, multi-user persistence, router intelligence, live tool fallbacks, and HTTP security headers:

```bash
pytest -v
```

### Test Suite Structure (33 / 33 Passing)
- `tests/test_fencing.py` — Rapid triple barge-in stress test, deterministic cancellation independent of delays, exception safety.
- `tests/test_interruption.py` — Verifies stale Apple result is fenced and only fresh Nvidia result is returned.
- `tests/test_persistence.py` — SQLite WAL schema initialization, CRUD operations, foreign key cascade, turn history, and tool execution tracking.
- `tests/test_router.py` — 50+ company entity resolution, order preservation, `$TICKER` syntax, stopword disambiguation, intent switches (price vs volume).
- `tests/test_security_api.py` — Health probes, security headers, CORS origin verification, session room isolation, token validation.
- `tests/test_state_versioning.py` — State machine unit tests, pre-execution fencing, in-flight cancellation.
- `tests/test_stocks.py` — Finnhub quote parsing, spoken volume number formatting (e.g. "9.03 million shares"), automatic yfinance fallback.

---

## 🔧 Production Rime Configuration

| Parameter | Configuration | Purpose |
|---|---|---|
| **Model** | `coda` | Low-latency natural speech model |
| **Speaker** | `celeste` | Warm, expressive conversational voice |
| **Transport** | WebSocket Streaming (`use_websocket=True`) | Sub-second audio delivery directly to LiveKit |
| **Speed** | 1.0 (configurable 0.75 - 1.5) | Per-session customizable playback speed |

---

## 👥 Team & Submission

- **Amar Jyoti Hota** — Team Leader
- **Abhinav Ashutosh** — Team Member
- **Harsh Raj** — Team Member
- **Suraj Kumar** — Team Member

**Submission Date:** September 2026  
**License:** MIT