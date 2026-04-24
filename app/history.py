"""
Transfer history storage using SQLite.
"""
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any
from contextlib import contextmanager

log = logging.getLogger(__name__)

# Default history location
HISTORY_DIR = Path.home() / ".phantomshare"
HISTORY_DB = HISTORY_DIR / "history.db"
MAX_HISTORY_ENTRIES = 100


def _ensure_dir():
    """Ensure history directory exists."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def _get_connection():
    """Get database connection with auto-commit."""
    _ensure_dir()
    conn = sqlite3.connect(str(HISTORY_DB))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Initialize the history database."""
    with _get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                filename TEXT NOT NULL,
                size INTEGER NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                sha256 TEXT,
                session_code TEXT,
                peer_emoji TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON transfers(timestamp DESC)
        """)


def add_transfer(
    filename: str,
    size: int,
    direction: str,
    status: str,
    sha256: Optional[str] = None,
    session_code: Optional[str] = None,
    peer_emoji: Optional[str] = None,
) -> int:
    """Add a transfer record to history. Returns the record ID."""
    init_db()
    with _get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO transfers 
            (timestamp, filename, size, direction, status, sha256, session_code, peer_emoji)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().isoformat(),
            filename,
            size,
            direction,
            status,
            sha256,
            session_code,
            peer_emoji,
        ))
        record_id = cursor.lastrowid
        
        # Cleanup old entries
        conn.execute("""
            DELETE FROM transfers WHERE id NOT IN (
                SELECT id FROM transfers ORDER BY timestamp DESC LIMIT ?
            )
        """, (MAX_HISTORY_ENTRIES,))
        
        return record_id


def update_transfer_status(record_id: int, status: str, sha256: Optional[str] = None):
    """Update transfer status (e.g., from 'in_progress' to 'completed')."""
    with _get_connection() as conn:
        if sha256:
            conn.execute("""
                UPDATE transfers SET status = ?, sha256 = ? WHERE id = ?
            """, (status, sha256, record_id))
        else:
            conn.execute("""
                UPDATE transfers SET status = ? WHERE id = ?
            """, (status, record_id))


def get_recent_transfers(limit: int = 20) -> List[Dict[str, Any]]:
    """Get recent transfer history."""
    init_db()
    with _get_connection() as conn:
        cursor = conn.execute("""
            SELECT * FROM transfers ORDER BY timestamp DESC LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def clear_history():
    """Clear all transfer history."""
    with _get_connection() as conn:
        conn.execute("DELETE FROM transfers")


def get_stats() -> Dict[str, Any]:
    """Get transfer statistics."""
    init_db()
    with _get_connection() as conn:
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN direction = 'sent' THEN 1 ELSE 0 END) as sent,
                SUM(CASE WHEN direction = 'received' THEN 1 ELSE 0 END) as received,
                SUM(CASE WHEN status = 'completed' THEN size ELSE 0 END) as total_bytes
            FROM transfers
        """)
        row = cursor.fetchone()
        return {
            'total': row['total'] or 0,
            'sent': row['sent'] or 0,
            'received': row['received'] or 0,
            'total_bytes': row['total_bytes'] or 0,
        }
