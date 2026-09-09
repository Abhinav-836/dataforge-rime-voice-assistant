"""
Integration and security tests for frontend/serve.py HTTP endpoints and headers.
Uses an isolated test database to avoid polluting production data.
"""

import tempfile
import threading
import functools
import http.server
import pytest
import requests
from pathlib import Path
from frontend.serve import ProductionServerHandler, FRONTEND_DIR
from agent.db.repository import get_repository


@pytest.fixture(scope="module")
def test_db_path():
    """Create an isolated test database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path


@pytest.fixture(scope="module")
def repo(test_db_path):
    """Get a repository instance using the test database."""
    return get_repository(test_db_path)


@pytest.fixture(scope="module")
def live_server(repo):
    """Start a live HTTP server with the test database."""
    # Patch the repository to use our test database
    import frontend.serve
    original_repo = frontend.serve.repo
    
    # Replace the repo in the serve module
    frontend.serve.repo = repo
    
    handler = functools.partial(ProductionServerHandler, directory=str(FRONTEND_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    
    yield base_url
    
    server.shutdown()
    # Restore original repo
    frontend.serve.repo = original_repo


def test_health_endpoint(live_server):
    resp = requests.get(f"{live_server}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "timestamp" in data


def test_security_headers_present(live_server):
    resp = requests.get(f"{live_server}/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "microphone=(self)" in resp.headers.get("Permissions-Policy", "")
    assert "Content-Security-Policy" in resp.headers


def test_cors_allowed_origin(live_server):
    headers = {"Origin": "http://localhost:5500"}
    resp = requests.get(f"{live_server}/health", headers=headers)
    assert resp.headers.get("Access-Control-Allow-Origin") in ("http://localhost:5500", "*")


def test_cors_disallowed_origin(live_server):
    headers = {"Origin": "http://evil-unauthorized-site.com"}
    resp = requests.get(f"{live_server}/health", headers=headers)
    # Origin must NOT be reflected if not in allowed list (unless configured with wildcard '*')
    origin_header = resp.headers.get("Access-Control-Allow-Origin")
    assert origin_header in ("null", "*") or origin_header != "http://evil-unauthorized-site.com"


def test_create_isolated_session(live_server, repo):
    resp = requests.post(f"{live_server}/api/session", json={"identity": "test-user-1"})
    assert resp.status_code == 201
    data = resp.json()
    assert "session_id" in data
    assert "room_name" in data
    assert data["room_name"].startswith("room_")
    assert data["status"] == "active"

    # Verify session is persisted in test database
    db_session = repo.get_session(data["session_id"])
    assert db_session is not None
    assert db_session["room_name"] == data["room_name"]


def test_token_requires_valid_session(live_server, repo):
    # Nonexistent session should return 404
    resp = requests.post(f"{live_server}/api/token", json={"session_id": "nonexistent-999"})
    assert resp.status_code == 404

    # Valid session should return 200 with room token
    unique_sess = f"valid-{threading.get_ident()}-{requests.utils.quote(str(live_server))[-4:]}"
    repo.create_session(unique_sess, f"room_{unique_sess}")
    resp2 = requests.post(f"{live_server}/api/token", json={"session_id": unique_sess})
    assert resp2.status_code == 200
    token_data = resp2.json()
    assert token_data["session_id"] == unique_sess
    assert "token" in token_data


def test_get_sessions_list_and_detail(live_server, repo):
    sess_id = f"test-query-{threading.get_ident()}"
    repo.create_session(sess_id, f"room_{sess_id}")

    resp = requests.get(f"{live_server}/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert any(s["id"] == sess_id for s in data["sessions"])

    # Detail query
    resp2 = requests.get(f"{live_server}/api/sessions/{sess_id}")
    assert resp2.status_code == 200
    detail = resp2.json()
    assert detail["session"]["id"] == sess_id
    assert "turns" in detail
    assert "tools" in detail