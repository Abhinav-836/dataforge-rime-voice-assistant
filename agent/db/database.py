"""
SQLite Database manager with WAL mode, foreign keys, and automatic schema initialization.
"""

import sqlite3
import os
from pathlib import Path
from contextlib import contextmanager
from agent.config import config
from agent.utils.logger import get_logger

logger = get_logger(__name__)


def init_db(db_path: Path = None) -> None:
    """Initialize SQLite database tables and indexes."""
    path = db_path or config.database_path
    os.makedirs(path.parent, exist_ok=True)

    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA busy_timeout = 5000;")

        cursor = conn.cursor()

        # Sessions Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                room_name TEXT UNIQUE NOT NULL,
                client_ip TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);")

        # Turns Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS turns (
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
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id);")

        # Tool Executions Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tool_executions (
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
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tools_session ON tool_executions(session_id);")

        # Settings Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                session_id TEXT PRIMARY KEY,
                voice_model TEXT NOT NULL DEFAULT 'coda',
                voice_speaker TEXT NOT NULL DEFAULT 'celeste',
                voice_speed REAL NOT NULL DEFAULT 1.0,
                auto_speak INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );
        """)
        conn.commit()
        logger.info(f"Database initialized successfully at {path}")
    finally:
        conn.close()


@contextmanager
def get_db_connection(db_path: Path = None):
    """Context manager yielding a SQLite connection with row factories enabled."""
    path = db_path or config.database_path
    os.makedirs(path.parent, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
