"""
db.py — SQLite persistence layer for Factory Eye
==================================================
Uses aiosqlite for async-friendly writes from FastAPI.

Tables:
  sessions  — one row per WebSocket session
  events    — one row per completed dwell (person entered then exited zone)
"""

import aiosqlite
import logging
import time
from pathlib import Path
import io
import csv
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

DB_PATH = Path("factory_eye.db")

@asynccontextmanager
async def get_db():
    """Yield a configured aiosqlite connection with WAL and a long busy_timeout."""
    db = await aiosqlite.connect(DB_PATH, timeout=20.0)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA busy_timeout=20000")
        yield db
    finally:
        await db.close()

async def init_db() -> None:
    """Create tables if they don't exist and run migrations."""
    async with get_db() as db:
        # Create sessions
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                started_at  REAL NOT NULL,
                ended_at    REAL,
                source      TEXT,
                model       TEXT,
                device      TEXT,
                peak_count  INTEGER DEFAULT 0,
                unique_count INTEGER DEFAULT 0
            )
        """)
        
        # Add owner_sub column if it doesn't exist (migration for v1.0->v2.0)
        try:
            await db.execute("ALTER TABLE sessions ADD COLUMN owner_sub TEXT DEFAULT 'user'")
        except aiosqlite.OperationalError:
            pass # column already exists
            
        # Create events
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                track_id    INTEGER NOT NULL,
                dwell_secs  REAL NOT NULL,
                recorded_at REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_session
            ON events(session_id)
        """)
        await db.commit()
    logger.info(f"Database initialised at {DB_PATH}")


async def create_session(
    session_id: str,
    source: str = "",
    model: str = "",
    device: str = "",
    owner_sub: str = "user"
) -> None:
    """Insert a new session row."""
    async with get_db() as db:
        await db.execute(
            "INSERT INTO sessions (id, started_at, source, model, device, owner_sub) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, time.time(), source, model, device, owner_sub),
        )
        await db.commit()


async def end_session(
    session_id: str,
    peak_count: int = 0,
    unique_count: int = 0,
) -> None:
    """Mark a session as ended and write final stats."""
    async with get_db() as db:
        await db.execute(
            "UPDATE sessions SET ended_at = ?, peak_count = ?, unique_count = ? "
            "WHERE id = ?",
            (time.time(), peak_count, unique_count, session_id),
        )
        await db.commit()


async def record_dwell(session_id: str, track_id: int, dwell_secs: float) -> None:
    """Write a single completed dwell event."""
    try:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO events (session_id, track_id, dwell_secs, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, track_id, dwell_secs, time.time()),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Failed to record dwell for session {session_id}: {e}")


async def get_session_history(limit: int = 50) -> list[dict]:
    """Return the most recent sessions with their event counts."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT
                s.id,
                s.started_at,
                s.ended_at,
                s.source,
                s.model,
                s.device,
                s.peak_count,
                s.unique_count,
                s.owner_sub,
                COUNT(e.id) AS total_events,
                COALESCE(AVG(e.dwell_secs), 0) AS avg_dwell
            FROM sessions s
            LEFT JOIN events e ON e.session_id = s.id
            GROUP BY s.id
            ORDER BY s.started_at DESC
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

async def get_session_export_data(session_id: str) -> str | None:
    """Generate a historical CSV export for a session straight from DB."""
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        # Get session info
        s_cursor = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        session = await s_cursor.fetchone()
        
        if not session:
            return None
            
        # Get events
        e_cursor = await db.execute("SELECT dwell_secs FROM events WHERE session_id = ? ORDER BY id ASC", (session_id,))
        events = await e_cursor.fetchall()
        
    total_visits = len(events)
    avg_dwell = sum(e["dwell_secs"] for e in events) / total_visits if total_visits > 0 else 0.0
    session_secs = (session["ended_at"] - session["started_at"]) if session["ended_at"] else 0.0
    
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Session summary header
    writer.writerow(["FACTORY EYE — Historical Session Summary (DB Export)"])
    writer.writerow(["Generated", time.strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])

    # Summary rows
    writer.writerow(["Metric", "Value"])
    writer.writerow(["Unique Visitors",     session["unique_count"]])
    writer.writerow(["Peak Count",          session["peak_count"]])
    writer.writerow(["Avg Dwell (s)",       round(avg_dwell, 1)])
    writer.writerow(["Total Visits",        total_visits])
    writer.writerow(["Session Duration (s)", round(session_secs, 0)])
    writer.writerow(["Max Capacity",        "N/A (Historical)"])
    writer.writerow(["Model",               session["model"]])
    writer.writerow(["Confidence",          "N/A (Historical)"])
    writer.writerow([])

    # Individual dwell times
    writer.writerow(["Visit #", "Dwell Time (s)"])
    for i, e in enumerate(events, 1):
        writer.writerow([i, round(e["dwell_secs"], 2)])

    return buf.getvalue()

