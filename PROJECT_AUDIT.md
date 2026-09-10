# PROJECT AUDIT — DATAFORGE RIME STARTER

**Last Updated:** September 2026
**Target Submission:** DataForge 2026 — Rime Hackathon Challenge

---

## 1. Executive Summary

DataForge Rime Starter is a real-time AI voice agent that streams speech-to-text
(Deepgram), reasons with an LLM (Groq / OpenAI), and speaks via Rime TTS over
LiveKit WebRTC transport.

### The Core Claim

Turn-version fencing: if the user interrupts mid-tool-call, the stale result
is deterministically discarded and never spoken. The response the user hears
always matches their latest intent.

### Current Status

- ✅ Deterministic turn-version fencing (verified by 33 passing tests)
- ✅ Persistent SQLite WAL storage for sessions, turns, tool executions, settings
- ✅ Isolated LiveKit rooms per session (`room_{session_id}`)
- ✅ Rime TTS streaming via `livekit-plugins-rime` WebSocket
- ✅ FastAPI-style heuristic router with 50+ company name → ticker mappings
- ✅ Fixed: Stock volume lookups (Finnhub → yfinance fallback)
- ✅ Fixed: Echo/feedback bug (single mic capture, explicit echoCancellation)
- ✅ Fixed: Pytest asyncio configuration (`pytest.ini` with `asyncio_mode = auto`)

---

## 2. Architecture
Browser (WebRTC + REST)
│
├─► POST /api/session → isolated room + JWT
├─► WebRTC audio + DataChannel
▼
LiveKit Cloud Room ──► Voice Agent Worker
│
├─► Deepgram STT
├─► Orchestrator (turn version + fencing)
├─► LLM (Groq / OpenAI)
├─► Tools (stocks, weather, time, news, search)
└─► Rime TTS (WebSocket streaming)
│
▼
SQLite (WAL mode)
sessions / turns / tool_executions / settings

text

---

## 3. File Inventory

| Layer | Files | Notes |
|---|---|---|
| Frontend UI | `frontend/index.html`, `frontend/css/*` | Vanilla HTML + CSS modules |
| Frontend JS | `frontend/js/*` | ES modules; `app.js`, `livekit.js`, `voice.js`, `state.js`, `notifications.js`, `metrics.js` |
| HTTP Server | `frontend/serve.py` | ThreadingHTTPServer: static + `/api/*` |
| Voice Agent | `agent/main.py`, `agent/voice/livekit_handler.py`, `agent/voice/rime_tts.py` | LiveKit AgentServer + `@server.rtc_session()` |
| State & Routing | `agent/state.py`, `agent/orchestrator.py`, `agent/router.py` | Fencing + intent |
| Tools | `agent/tools/stocks.py`, `agent/tools/search.py` | Finnhub/yfinance + web scrapers |
| Persistence | `agent/db/database.py`, `agent/db/repository.py` | SQLite WAL |
| Config & Utils | `agent/config.py`, `agent/utils/*` | Env validation + logging + metrics |
| Tests | `tests/*`, `pytest.ini` | 33 passing tests |
| Deployment | `render.yaml`, `start.sh`, `Dockerfile`, `docker-compose.yml` | Configurable |

---

## 4. Known Limitations

1. **Render free tier (512 MB)**: cannot run the LiveKit agent alongside the
   HTTP server. Recommended deployment is either:
   - Two services (web + worker) OR
   - A paid tier with ≥ 1 GB RAM.
2. **Public web scrapers** (`wttr.in`, DuckDuckGo Lite, worldtimeapi.org) are
   used for weather/time/general search — they have no SLA. Production would
   swap these for structured APIs.
3. **Heuristic ticker router** uses a static company-name → ticker dictionary.
   Unlisted companies fall through to the LLM, which resolves them via tool
   arguments.
4. **LiveKit agent always starts an internal HTTP health server**. This is a
   known behavior of `livekit-agents` 1.8.0 — there is no supported way to
   disable it. It's harmless when the agent runs in its own process/container.

---

## 5. Verification

```bash
pytest -v
Expected: 33 passed.

Manual end-to-end test:

bash
# Terminal 1
python frontend/serve.py

# Terminal 2
python -m agent.main console
Then open http://localhost:5500 and click the orb.

6. Security Notes
No plaintext secrets in the repository (.env.example only).

.env is gitignored.

Per-session JWT tokens with 30-minute TTL.

CORS restricted to ALLOWED_ORIGINS.

Security headers: CSP, X-Frame-Options, X-Content-Type-Options, etc.

text

**Change:** Removed the stale "no database" claims; added accurate current status; documented the Render limitation honestly.

---

## 4. `README.md` — updated (only the sections that changed)

Replace the **"Production Readiness"** section and the **"Verification & Automated Test Suite"** section with:

```markdown
## 🚀 Quick Start

### Prerequisites
- Python 3.11–3.13
- API keys: Rime, LiveKit, Deepgram, and either Groq or OpenAI

### Local Installation

```bash
git clone <repo-url>
cd dataforge-rime-starter

# Windows
.\scripts\setup.ps1
# Linux / macOS
chmod +x scripts/setup.sh && ./scripts/setup.sh

# Edit .env (created from .env.example) and fill in your API keys

# Run tests
.\venv\Scripts\python.exe -m pytest -v

# Terminal 1: HTTP server
.\venv\Scripts\python.exe frontend/serve.py

# Terminal 2: Voice agent
.\venv\Scripts\python.exe -m agent.main console   # local mic/speaker test
# OR
.\venv\Scripts\python.exe -m agent.main dev       # full LiveKit room
Open http://localhost:5500.

🐳 Docker
bash
docker-compose up --build -d
docker-compose logs -f
open http://localhost:5500
🧪 Verification
bash
pytest -v
Expected output: 33 passed in ~3s.

Test coverage:

test_fencing.py — rapid barge-in stress, deterministic cancellation

test_interruption.py — stale result fencing

test_persistence.py — SQLite schema, CRUD, foreign keys, WAL

test_router.py — ticker extraction, stopwords, intent changes

test_security_api.py — health, security headers, CORS, session isolation

test_state_versioning.py — state machine unit tests

test_stocks.py — Finnhub → yfinance fallback and spoken formatting