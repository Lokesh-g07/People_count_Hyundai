"""
sessions.py — Shared session state for FactoryEye
===================================================
Holds the global _sessions dict used by both REST and WebSocket routers.
"""

from people_counter import PeopleCounter

# Dict keyed by session_id → PeopleCounter instance
_sessions: dict[str, PeopleCounter] = {}
