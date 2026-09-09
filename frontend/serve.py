"""
Production HTTP server for DataForge Rime Starter frontend & API.

Supports:
  - Multi-user session management (POST /api/session, GET /api/sessions, GET /api/sessions/<id>)
  - Session & global settings persistence (GET/PUT /api/settings)
  - LiveKit token generation with per-room isolation (GET /api/token)
  - Real-time stock quote proxying (GET /api/quote, GET /api/compare)
  - Health & readiness probes (GET /health, GET /ready)
  - Security headers, CORS configuration, in-memory IP rate limiting, and request size guards.
"""

import os
import sys
import json
import uuid
import time
import functools
import http.server
import urllib.parse
from collections import defaultdict
import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dotenv import load_dotenv

FRONTEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from livekit import api
from agent.config import config
from agent.tools.stocks import _fetch_quote_sync
from agent.db.repository import repo
from agent.db.database import get_db_connection
from agent.utils.logger import get_logger

logger = get_logger(__name__)

LIVEKIT_URL = config.livekit_url
LIVEKIT_API_KEY = config.livekit_api_key
LIVEKIT_API_SECRET = config.livekit_api_secret
ALLOWED_ORIGINS = config.allowed_origins

# Rate Limiting configuration (60 requests per minute per IP)
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_REQUESTS = config.rate_limit_per_minute
_ip_request_timestamps: Dict[str, List[float]] = defaultdict(list)
MAX_BODY_BYTES = 65536  # 64 KB max payload to prevent DoS


def _is_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS
    timestamps = [t for t in _ip_request_timestamps[client_ip] if t > window_start]
    timestamps.append(now)
    _ip_request_timestamps[client_ip] = timestamps
    return len(timestamps) > RATE_LIMIT_MAX_REQUESTS


def _make_livekit_token(identity: str, room_name: str) -> str:
    if not (LIVEKIT_API_KEY and LIVEKIT_API_SECRET):
        raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be configured")
    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_name(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room_name))
        .with_ttl(datetime.timedelta(minutes=30))
    )
    return token.to_jwt()


class ProductionServerHandler(http.server.SimpleHTTPRequestHandler):
    """Production HTTP request handler for API endpoints and static assets."""

    def _get_client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0] if self.client_address else "127.0.0.1"

    def _send_cors_headers(self):
        req_origin = self.headers.get("Origin", "")
        if "*" in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif req_origin and req_origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", req_origin)
        else:
            self.send_header("Access-Control-Allow-Origin", "null")

        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def _send_security_headers(self):
        self._send_cors_headers()
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-XSS-Protection", "1; mode=block")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "microphone=(self)")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "connect-src 'self' wss: https:; "
            "img-src 'self' data: https:; "
            "media-src 'self' blob: mediastream:;"
        )

    def _send_json_response(self, status_code: int, data: Any):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            return None, "Invalid Content-Length"

        if content_length > MAX_BODY_BYTES:
            return None, f"Payload exceeds maximum allowed size of {MAX_BODY_BYTES} bytes"

        if content_length == 0:
            return {}, None

        try:
            raw_data = self.rfile.read(content_length).decode("utf-8")
            return json.loads(raw_data), None
        except Exception as e:
            return None, f"Malformed JSON: {str(e)}"

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        client_ip = self._get_client_ip()
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # Health Probe
        if path == "/health":
            return self._send_json_response(200, {
                "status": "healthy",
                "timestamp": time.time(),
                "service": "dataforge-voice-agent"
            })

        # Readiness Probe
        if path == "/ready":
            db_ready = False
            try:
                with get_db_connection() as conn:
                    conn.execute("SELECT 1").fetchone()
                    db_ready = True
            except Exception:
                db_ready = False

            livekit_configured = bool(LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET)
            ready = db_ready and livekit_configured
            status_code = 200 if ready else 503
            return self._send_json_response(status_code, {
                "ready": ready,
                "database_connected": db_ready,
                "livekit_configured": livekit_configured,
            })

        # Check Rate Limit on API endpoints
        if path.startswith("/api/"):
            if _is_rate_limited(client_ip):
                self.send_response(429)
                self.send_header("Retry-After", "60")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Rate limit exceeded. Please wait a moment."}')
                return

        # GET /api/sessions/<id>
        if path.startswith("/api/sessions/"):
            session_id = path[len("/api/sessions/"):].strip("/")
            session = repo.get_session(session_id)
            if not session:
                return self._send_json_response(404, {"error": f"Session {session_id} not found"})
            turns = repo.get_session_turns(session_id)
            tools = repo.get_session_tools(session_id)
            settings = repo.get_settings(session_id)
            return self._send_json_response(200, {
                "session": session,
                "turns": turns,
                "tools": tools,
                "settings": settings,
            })

        # GET /api/sessions (List sessions with pagination & search)
        if path == "/api/sessions":
            limit = min(int(query_params.get("limit", ["50"])[0]), 100)
            offset = max(int(query_params.get("offset", ["0"])[0]), 0)
            status = query_params.get("status", [None])[0]
            search = query_params.get("search", [None])[0]
            sessions = repo.list_sessions(limit=limit, offset=offset, status=status, search=search)
            return self._send_json_response(200, {
                "sessions": sessions,
                "count": len(sessions),
                "limit": limit,
                "offset": offset,
            })

        # GET /api/settings
        if path == "/api/settings":
            session_id = query_params.get("session_id", ["global"])[0]
            settings = repo.get_settings(session_id)
            return self._send_json_response(200, settings)

        # GET /api/token (Legacy single-room token generation)
        if path == "/api/token":
            if not (LIVEKIT_API_KEY and LIVEKIT_API_SECRET and LIVEKIT_URL):
                return self._send_json_response(500, {
                    "error": "LiveKit credentials not configured on server"
                })
            room_name = query_params.get("room", ["console-demo"])[0]
            identity = query_params.get("identity", [f"user-{uuid.uuid4().hex[:8]}"])[0]
            try:
                token = _make_livekit_token(identity, room_name)
                return self._send_json_response(200, {
                    "token": token,
                    "url": LIVEKIT_URL,
                    "room": room_name,
                    "identity": identity,
                })
            except Exception as e:
                return self._send_json_response(500, {"error": str(e)})

        # GET /api/quote
        if path == "/api/quote":
            symbol = query_params.get("symbol", ["AAPL"])[0].upper()
            quote = _fetch_quote_sync(symbol)
            if not quote or "error" in quote:
                return self._send_json_response(404, quote or {"error": f"Quote failed for {symbol}"})
            return self._send_json_response(200, quote)

        # GET /api/compare
        if path == "/api/compare":
            symbols_str = query_params.get("symbols", ["AAPL,TSLA"])[0]
            symbols = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
            quotes = [_fetch_quote_sync(s) for s in symbols]
            valid_quotes = [q for q in quotes if q and "error" not in q]
            return self._send_json_response(200, {"results": valid_quotes})

        # Default: Serve static files from FRONTEND_DIR with security headers
        return super().do_GET()

    def do_POST(self):
        client_ip = self._get_client_ip()
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if _is_rate_limited(client_ip):
            return self._send_json_response(429, {"error": "Rate limit exceeded. Please wait a moment."})

        # POST /api/session (Isolated multi-user session creation)
        if path == "/api/session":
            body, err = self._read_json_body()
            if err:
                return self._send_json_response(400, {"error": err})

            if not (LIVEKIT_API_KEY and LIVEKIT_API_SECRET and LIVEKIT_URL):
                return self._send_json_response(500, {
                    "error": "LiveKit credentials not configured on server"
                })

            session_id = uuid.uuid4().hex[:12]
            room_name = f"room_{session_id}"
            identity = (body or {}).get("identity") or f"user-{uuid.uuid4().hex[:8]}"

            try:
                session_record = repo.create_session(
                    session_id=session_id,
                    room_name=room_name,
                    client_ip=client_ip
                )
                token = _make_livekit_token(identity, room_name)
                return self._send_json_response(201, {
                    "session_id": session_id,
                    "room_name": room_name,
                    "token": token,
                    "url": LIVEKIT_URL,
                    "identity": identity,
                    "status": "active",
                    "created_at": session_record["created_at"],
                })
            except Exception as e:
                logger.error(f"Failed to create session: {e}")
                return self._send_json_response(500, {"error": f"Failed to create session: {str(e)}"})

        # POST /api/token (Issue token for validated session ID)
        if path == "/api/token":
            body, err = self._read_json_body()
            if err:
                return self._send_json_response(400, {"error": err})

            session_id = (body or {}).get("session_id")
            if not session_id:
                return self._send_json_response(400, {"error": "session_id is required"})

            session = repo.get_session(session_id)
            if not session:
                return self._send_json_response(404, {"error": f"Session {session_id} not found"})

            if not (LIVEKIT_API_KEY and LIVEKIT_API_SECRET and LIVEKIT_URL):
                return self._send_json_response(500, {
                    "error": "LiveKit credentials not configured on server"
                })

            identity = (body or {}).get("identity") or f"user-{uuid.uuid4().hex[:8]}"
            try:
                token = _make_livekit_token(identity, session["room_name"])
                return self._send_json_response(200, {
                    "session_id": session_id,
                    "room_name": session["room_name"],
                    "token": token,
                    "url": LIVEKIT_URL,
                    "identity": identity,
                })
            except Exception as e:
                return self._send_json_response(500, {"error": str(e)})

        # POST /api/settings
        if path == "/api/settings":
            body, err = self._read_json_body()
            if err or not isinstance(body, dict):
                return self._send_json_response(400, {"error": err or "Body must be a JSON object"})
            session_id = body.get("session_id", "global")
            updated = repo.update_settings(session_id, body)
            return self._send_json_response(200, updated)

        return self._send_json_response(404, {"error": f"POST {path} not found"})

    def do_PUT(self):
        client_ip = self._get_client_ip()
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if _is_rate_limited(client_ip):
            return self._send_json_response(429, {"error": "Rate limit exceeded. Please wait a moment."})

        # PUT /api/settings
        if path == "/api/settings":
            body, err = self._read_json_body()
            if err or not isinstance(body, dict):
                return self._send_json_response(400, {"error": err or "Body must be a JSON object"})
            session_id = body.get("session_id", "global")
            updated = repo.update_settings(session_id, body)
            return self._send_json_response(200, updated)

        return self._send_json_response(404, {"error": f"PUT {path} not found"})

    def end_headers(self):
        self._send_security_headers()
        super().end_headers()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5500))
    handler = functools.partial(ProductionServerHandler, directory=str(FRONTEND_DIR))
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    with http.server.ThreadingHTTPServer(("", port), handler) as httpd:
        print(f"DataForge production server running on http://localhost:{port}")
        print("API endpoints available: /api/session, /api/sessions, /api/settings, /health, /ready")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server gracefully...")