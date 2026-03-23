"""Database connection management.

Provides a managed PostgreSQL connection with reconnection logic.
Extracted from Manhole_popup.py to eliminate global mutable state coupling.
"""
import os
import logging

import psycopg2
from psycopg2 import extras
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

# Module-level connection (replaces the global _conn in Manhole_popup.py)
_conn = None
_cursor = None


def get_connection():
    """Returns an existing connection or creates a new one."""
    global _conn

    if _conn and not _conn.closed:
        return _conn

    try:
        db_url = DATABASE_URL
        logger.info(f"[MANHOLE-DB] Database URL: {db_url}")
        if not db_url:
            logger.error("[MANHOLE-DB] No DATABASE_URL found.")
            return None

        logger.info("[MANHOLE-DB] Establishing new connection...")
        _conn = psycopg2.connect(db_url, connect_timeout=5)

        return _conn

    except Exception as e:
        logger.error(f"[MANHOLE-DB] ❌ Database connection failed: {e}")
        return None


def get_cursor():
    """Returns a RealDictCursor, creating a connection if needed."""
    global _cursor, _conn

    conn = get_connection()
    if not conn:
        return None

    if not _cursor or _cursor.closed:
        _cursor = conn.cursor(cursor_factory=extras.RealDictCursor)

    return _cursor


def reset_connection():
    """Close and reset the connection (e.g. after an error)."""
    global _cursor, _conn

    try:
        if _cursor:
            _cursor.close()
    except:
        pass

    try:
        if _conn:
            _conn.close()
    except:
        pass

    _cursor = None
    _conn = None
