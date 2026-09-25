"""
config.py — FactoryEye configuration and environment settings
==============================================================
All environment variables, constants, and startup guards.
"""

import logging
import os
import re
import sys
import ipaddress
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Directories ──────────────────────────────────────────────────────────────
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = Path("frontend")
FRONTEND_DIR.mkdir(exist_ok=True)

# ── Environment ──────────────────────────────────────────────────────────────
FACTORY_EYE_ENV = os.environ.get("FACTORY_EYE_ENV", "production")

# ── JWT ──────────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("FACTORY_EYE_SECRET", "dev-secret-change-me-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("FACTORY_EYE_JWT_HOURS", "24"))

# ── API Key ──────────────────────────────────────────────────────────────────
API_KEY = os.environ.get("FACTORY_EYE_API_KEY", "factory-eye-dev-key")

# ── Upload ───────────────────────────────────────────────────────────────────
MAX_UPLOAD_MB = int(os.environ.get("FACTORY_EYE_MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# ── Sessions ─────────────────────────────────────────────────────────────────
MAX_SESSIONS = int(os.environ.get("FACTORY_EYE_MAX_SESSIONS", "5"))

# ── CORS ─────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = os.environ.get(
    "FACTORY_EYE_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
).split(",")

# ── RTSP Allowlist ───────────────────────────────────────────────────────────
RTSP_ALLOWLIST_RAW = os.environ.get("FACTORY_EYE_RTSP_ALLOWLIST", "")
RTSP_ALLOWLIST: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
for _item in RTSP_ALLOWLIST_RAW.split(","):
    _item = _item.strip()
    if _item:
        try:
            RTSP_ALLOWLIST.append(ipaddress.ip_network(_item, strict=False))
        except ValueError:
            logger.warning(f"Invalid network in RTSP allowlist: {_item}")

# RTSP URL pattern
RTSP_PATTERN = re.compile(r"^rtsps?://[\w.\-]+(:\d+)?/")

# ── Auth Rate Limiting ───────────────────────────────────────────────────────
AUTH_RATE_LIMIT_MINS = int(os.environ.get("FACTORY_EYE_AUTH_RL_MINS", "10"))
AUTH_RATE_LIMIT_ATTEMPTS = int(os.environ.get("FACTORY_EYE_AUTH_RL_ATTEMPTS", "5"))

# ── Production Guard ─────────────────────────────────────────────────────────
if FACTORY_EYE_ENV == "production":
    if SECRET_KEY == "dev-secret-change-me-in-prod" or API_KEY == "factory-eye-dev-key":
        sys.exit(
            "CRITICAL: FACTORY_EYE_ENV is production but default FACTORY_EYE_SECRET "
            "or FACTORY_EYE_API_KEY is in use. Set secure values via environment "
            "variables to start."
        )
else:
    if SECRET_KEY == "dev-secret-change-me-in-prod":
        logger.warning("⚠  Using default JWT secret in development mode.")
