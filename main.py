"""
Factory Eye — FastAPI backend
================================
Slim application entrypoint.

Configuration  → config.py
Authentication → auth.py
Validation     → validation.py
Sessions       → sessions.py
REST routes    → routers/api.py
WebSocket      → routers/ws.py
People counter → people_counter.py
Database       → db.py
"""

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import ALLOWED_ORIGINS
import db
from routers import api, ws

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Lifespan ─────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    yield


# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Factory Eye — People Counter API", lifespan=lifespan)

# ── CORS (locked down) ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── Static files ─────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="frontend"), name="static")

# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(api.router)
app.include_router(ws.router)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)