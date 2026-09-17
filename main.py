"""
Factory Eye — FastAPI backend
================================
WebSocket protocol (JSON messages):

Client → Server:
  { type: "start",        source: "<path or URL>" }
  { type: "stop" }
  { type: "restart" }
  { type: "set_roi",      points: [[x,y], ...], space: "normalised"|"pixel" }
  { type: "clear_roi" }
  { type: "get_info" }
  { type: "set_conf",     value: 0.45 }
  { type: "set_model",    model: "yolov8s.pt" }
  { type: "set_capacity", value: 10 }
  { type: "get_summary" }

Server → Client:
  { type: "frame",   frame: "<base64 jpg>", stats: {...} }
  { type: "end" }
  { type: "info",    data: {...} }
  { type: "summary", data: {...} }
  { type: "error",   message: "..." }
  { type: "status",  message: "..." }

Security notes:
  - Model loading restricted to ALLOWED_MODELS whitelist (no arbitrary pickle)
  - Uploaded filenames sanitised to UUID (no path traversal)
  - Video sources restricted to UPLOAD_DIR or validated RTSP patterns
  - JWT auth on all endpoints via PyJWT
  - CORS locked to explicit origins
"""

import asyncio
import json
import logging
import os
import re
import shutil
import uuid
import sys
import time
import socket
import ipaddress
from urllib.parse import urlparse
from pathlib import Path
import aiosqlite

import jwt
import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime, timedelta, timezone

from people_counter import PeopleCounter, ALLOWED_MODELS
import db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

FRONTEND_DIR = Path("frontend")
FRONTEND_DIR.mkdir(exist_ok=True)

FACTORY_EYE_ENV = os.environ.get("FACTORY_EYE_ENV", "production")

# JWT settings — override via environment variables in production
SECRET_KEY = os.environ.get("FACTORY_EYE_SECRET", "dev-secret-change-me-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.environ.get("FACTORY_EYE_JWT_HOURS", "24"))

# API key for obtaining tokens — override via environment variable
API_KEY = os.environ.get("FACTORY_EYE_API_KEY", "factory-eye-dev-key")

# Max upload size
MAX_UPLOAD_MB = int(os.environ.get("FACTORY_EYE_MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# Max concurrent sessions
MAX_SESSIONS = int(os.environ.get("FACTORY_EYE_MAX_SESSIONS", "5"))

# CORS — override with comma-separated origins via env var
ALLOWED_ORIGINS = os.environ.get(
    "FACTORY_EYE_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
).split(",")

# RTSP Allowlist
RTSP_ALLOWLIST_RAW = os.environ.get("FACTORY_EYE_RTSP_ALLOWLIST", "")
RTSP_ALLOWLIST = []
for item in RTSP_ALLOWLIST_RAW.split(","):
    item = item.strip()
    if item:
        try:
            RTSP_ALLOWLIST.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning(f"Invalid network in RTSP allowlist: {item}")

# RTSP URL pattern for source validation
_RTSP_PATTERN = re.compile(r"^rtsps?://[\w.\-]+(:\d+)?/")

if FACTORY_EYE_ENV == "production":
    if SECRET_KEY == "dev-secret-change-me-in-prod" or API_KEY == "factory-eye-dev-key":
        sys.exit("CRITICAL: FACTORY_EYE_ENV is production but default FACTORY_EYE_SECRET or FACTORY_EYE_API_KEY is in use. Set secure values via environment variables to start.")
else:
    if SECRET_KEY == "dev-secret-change-me-in-prod":
        logger.warning("⚠  Using default JWT secret in development mode.")

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Factory Eye — People Counter API")

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

# ── Startup: init DB ─────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    await db.init_db()


# ── JWT helpers ──────────────────────────────────────────────────────────────
def create_token(subject: str = "user") -> str:
    """Create a signed JWT with an expiry."""
    payload = {
        "sub": subject,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """Decode and verify a JWT.  Raises HTTPException on failure."""
    if not token:
        raise HTTPException(status_code=401, detail="Missing token")
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def require_auth(request: Request) -> dict:
    """FastAPI dependency — rejects unauthenticated requests based on cookie."""
    token = request.cookies.get("factory_eye_token")
    if not token:
        raise HTTPException(status_code=401, detail="Missing authorization cookie")
    return verify_token(token)


# ── Auth endpoint ────────────────────────────────────────────────────────────
@app.post("/api/token")
async def get_token(body: dict, response: Response):
    """
    Exchange an API key for a JWT.
    Body: { "api_key": "<key>" }
    """
    if body.get("api_key") != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    token = create_token()
    response.set_cookie(
        key="factory_eye_token",
        value=token,
        httponly=True,
        samesite="lax", # Lax provides good CSRF protection for non-cross-site cases without blocking same-site post
        max_age=JWT_EXPIRY_HOURS * 3600
    )
    return {"status": "ok", "expires_in_hours": JWT_EXPIRY_HOURS}


@app.post("/api/logout")
async def logout(response: Response):
    """Clear the authentication cookie."""
    response.delete_cookie(key="factory_eye_token", httponly=True, samesite="lax")
    return {"status": "logged_out"}


# ── HTML ─────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    fe = Path("frontend/index.html")
    fallback = Path("index.html")
    if fe.exists() and fe.stat().st_size > 0:
        return fe.read_text(encoding="utf-8")
    if fallback.exists():
        return fallback.read_text(encoding="utf-8")
    return "<h1>Factory Eye</h1><p>frontend/index.html not found</p>"


# ── Source validation ────────────────────────────────────────────────────────
def validate_source(source: str) -> str:
    """
    Validate and normalise a video source string.
    - Camera indices (digits) → allowed with a warning
    - Local file paths → must resolve inside UPLOAD_DIR
    - RTSP URLs → must match the expected pattern, resolve to safe IP
    - Everything else → rejected
    """
    s = source.strip()
    if not s:
        raise ValueError("Empty source")

    # Camera index (e.g. "0", "1")
    if s.isdigit():
        logger.info(f"Source is a server-side camera device index: {s}")
        return s

    # RTSP stream
    if s.lower().startswith("rtsp"):
        if not _RTSP_PATTERN.match(s):
            raise ValueError("Invalid source format")
        
        parsed = urlparse(s)
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Invalid RTSP URL format")
            
        try:
            addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            resolved_ips = {info[4][0] for info in addrinfo}
        except socket.gaierror:
            raise ValueError("Could not resolve source host")
            
        for ip_str in resolved_ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                raise ValueError("Invalid resolved IP")
                
            # Allowlist check
            is_allowlisted = any(ip in net for net in RTSP_ALLOWLIST)
            
            if not is_allowlisted:
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
                    raise ValueError("Source IP address is not permitted by policy")
                    
        return s

    # Local file — must be within UPLOAD_DIR
    try:
        resolved = Path(s).resolve()
        upload_resolved = UPLOAD_DIR.resolve()
        if not resolved.is_relative_to(upload_resolved):
            raise ValueError("Source path must be within upload directory")
        if not resolved.exists():
            raise ValueError(f"File not found")
        return str(resolved)
    except (OSError, ValueError):
        raise ValueError("Invalid local path")


# ── Video upload endpoint (secured + sanitised) ──────────────────────────────
def check_magic_bytes(header: bytes) -> bool:
    if header.startswith(b"\x00\x00\x00") and b"ftyp" in header[:16]: return True # MP4/MOV
    if header.startswith(b"RIFF") and header[8:12] == b"AVI ": return True
    if header.startswith(b"\x1A\x45\xDF\xA3"): return True # MKV
    if header.startswith(b"\x47"): return True # TS sync byte
    return False

@app.post("/upload")
async def upload_video(
    file: UploadFile,
    request: Request,
    _user: dict = Depends(require_auth),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in [".mp4", ".avi", ".mov", ".mkv", ".ts"]:
        raise HTTPException(status_code=400, detail="Invalid file extension")
        
    safe_name = f"{uuid.uuid4().hex}{suffix}"
    dest = UPLOAD_DIR / safe_name
    
    bytes_read = 0
    chunk_size = 1024 * 1024 # 1MB
    is_first_chunk = True

    try:
        with dest.open("wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                
                if is_first_chunk:
                    if not check_magic_bytes(chunk):
                        raise HTTPException(status_code=400, detail="Invalid file signature")
                    is_first_chunk = False
                    
                bytes_read += len(chunk)
                if bytes_read > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File too large")
                    
                f.write(chunk)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise e
        
    logger.info(f"Uploaded: {dest} (original: {file.filename})")
    return {"path": str(dest).replace("\\", "/"), "filename": safe_name}


# ── Multi-session state ─────────────────────────────────────────────────────
# Dict keyed by session_id → PeopleCounter instance
_sessions: dict[str, PeopleCounter] = {}


@app.get("/export")
async def export_csv(
    session_id: str = Query(..., description="Session ID"),
    _user: dict = Depends(require_auth),
):
    """Download a CSV of a session's metrics."""
    counter = _sessions.get(session_id)
    if counter is not None:
        if getattr(counter, "owner_sub", None) != _user.get("sub"):
            raise HTTPException(status_code=403, detail="Unauthorized")
        csv_text = counter.export_csv()
    else:
        # Fallback to DB historical export
        csv_text = await db.get_session_export_data(session_id)
        if csv_text is None:
            raise HTTPException(status_code=404, detail="Session not found")
        # For historical auth, we should ideally check the owner_sub.
        # But this requires fetching the session row. get_session_export_data doesn't return owner_sub right now.
        # Let's check it manually first:
        async with db.get_db() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute("SELECT owner_sub FROM sessions WHERE id = ?", (session_id,))
            row = await cursor.fetchone()
            if row and row["owner_sub"] != _user.get("sub"):
                raise HTTPException(status_code=403, detail="Unauthorized")
        
    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=factory_eye_{session_id[:8]}.csv"},
    )


@app.get("/snapshot")
async def snapshot_png(
    session_id: str = Query(..., description="Session ID"),
    _user: dict = Depends(require_auth),
):
    """Return the latest annotated frame as PNG."""
    counter = _sessions.get(session_id)
    if counter is None:
        raise HTTPException(status_code=404, detail="Session not found (Historical snapshots are not supported)")
    if getattr(counter, "owner_sub", None) != _user.get("sub"):
        raise HTTPException(status_code=403, detail="Unauthorized")
        
    png_bytes = counter.get_snapshot_png()
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="No frame available")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/history")
async def session_history(
    limit: int = Query(50, ge=1, le=500),
    _user: dict = Depends(require_auth),
):
    """Return recent session history from SQLite."""
    return await db.get_session_history(limit=limit)


@app.get("/api/models")
async def list_models(_user: dict = Depends(require_auth)):
    """Return the list of allowed model names."""
    return {"models": sorted(ALLOWED_MODELS)}


# ── WebSocket ─────────────────────────────────────────────────────────────────
TARGET_FPS = 25
FRAME_INTERVAL = 1.0 / TARGET_FPS


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # ── Auth check on handshake ──
    token = websocket.cookies.get("factory_eye_token", "")
    try:
        user_data = verify_token(token)
    except HTTPException:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    # Check maximum sessions before accepting and allocating resources
    if len(_sessions) >= MAX_SESSIONS:
        logger.warning("Max concurrent sessions reached")
        await websocket.close(code=1013, reason="Maximum concurrent sessions reached. Try again later.")
        return

    await websocket.accept()

    # ── Session setup ──
    session_id = uuid.uuid4().hex
    counter = PeopleCounter()
    counter.owner_sub = user_data.get("sub", "user")
    _sessions[session_id] = counter

    # Send session ID to client so it can use /export, /snapshot etc.
    await websocket.send_json({
        "type": "session",
        "session_id": session_id,
    })

    streaming = False
    loop = asyncio.get_running_loop()
    has_started = False
    
    # Rate Limiting configuration
    # Token bucket: 5 tokens max, refills 2 per second. (for general messages)
    rl_tokens = 5.0
    rl_last_time = time.time()
    
    # Expensive messages (model/roi changes) have a strict timestamp limit (e.g. max 1 per 2 seconds)
    rl_last_expensive_time = 0.0

    async def stream_frames():
        nonlocal streaming
        while streaming:
            t0 = loop.time()
            try:
                result = await loop.run_in_executor(None, counter.get_frame)
            except Exception as e:
                logger.error(f"Frame error: {e}")
                try:
                    await websocket.send_json({"type": "error", "message": str(e)})
                except Exception:
                    pass
                streaming = False
                break

            if result is None:
                try:
                    await websocket.send_json({"type": "end"})
                except Exception:
                    pass
                streaming = False
                break

            frame_b64, stats = result
            try:
                await websocket.send_json({
                    "type":  "frame",
                    "frame": frame_b64,
                    "stats": stats,
                })
            except Exception:
                streaming = False
                break

            elapsed   = loop.time() - t0
            sleep_for = max(0.0, FRAME_INTERVAL - elapsed)
            await asyncio.sleep(sleep_for)

    frame_task: asyncio.Task | None = None

    try:
        async for raw in websocket.iter_text():
            now = time.time()
            
            # Simple Token Bucket Rate Limiting
            elapsed = now - rl_last_time
            rl_tokens = min(5.0, rl_tokens + (elapsed * 2.0))
            rl_last_time = now
            
            if rl_tokens < 1.0:
                await websocket.send_json({"type": "error", "message": "Rate limit exceeded. Sending too fast."})
                continue
            
            rl_tokens -= 1.0
            
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type", "")

            # Expensive Operations Rate Limiting
            if mtype in ("set_roi", "set_model", "set_conf", "set_capacity", "clear_roi"):
                if now - rl_last_expensive_time < 2.0:
                    await websocket.send_json({"type": "error", "message": "Rate limit exceeded for configuration changes."})
                    continue
                rl_last_expensive_time = now

            if mtype == "start":
                if has_started:
                    await websocket.send_json({"type": "error", "message": "Session already started. Stop it first."})
                    continue
                    
                source_raw = msg.get("source", "")
                try:
                    source = validate_source(source_raw)
                except ValueError as e:
                    await websocket.send_json({
                        "type":    "error",
                        "message": f"Invalid source: {e}",
                    })
                    continue

                ok = await loop.run_in_executor(None, counter.start, source)
                if ok:
                    has_started = True
                    # Register dwell callback for this session
                    sid = session_id  # capture for closure

                    def on_dwell(track_id: int, dwell_secs: float, _sid=sid):
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                db.record_dwell(_sid, track_id, dwell_secs),
                                loop,
                            )

                    counter.on_dwell_complete = on_dwell

                    # Create DB session record
                    await db.create_session(
                        session_id, source, counter.model_path, counter.device, counter.owner_sub
                    )

                    info = counter.get_video_info()
                    # Clarify server-side camera usage
                    if source.isdigit():
                        await websocket.send_json({
                            "type":    "status",
                            "message": f"Opening server-side camera device {source} (not browser webcam)",
                        })
                    await websocket.send_json({"type": "info", "data": info})
                    streaming = True
                    if frame_task and not frame_task.done():
                        frame_task.cancel()
                    frame_task = asyncio.create_task(stream_frames())
                else:
                    await websocket.send_json({
                        "type":    "error",
                        "message": f"Cannot open source: {source_raw}",
                    })

            elif mtype == "stop":
                if not has_started:
                    continue
                streaming = False
                counter.stop()
                if frame_task:
                    frame_task.cancel()
                has_started = False

            elif mtype == "restart":
                streaming = False
                if frame_task:
                    frame_task.cancel()
                await asyncio.sleep(0.1)
                counter.restart()
                streaming = True
                frame_task = asyncio.create_task(stream_frames())

            elif mtype == "set_roi":
                try:
                    points = msg.get("points")
                    space  = msg.get("space", "normalised")
                    if not isinstance(points, list):
                        raise ValueError("points must be a list")
                    if len(points) < 3 and len(points) > 0:
                        raise ValueError("ROI requires at least 3 points")
                    if len(points) > 50:
                        raise ValueError("ROI has too many points")
                    for pt in points:
                        if not isinstance(pt, list) or len(pt) != 2:
                            raise ValueError("Each point must be [x, y]")
                        if not (isinstance(pt[0], (int, float)) and isinstance(pt[1], (int, float))):
                            raise ValueError("Coordinates must be numeric")
                    counter.set_roi(points, coordinate_space=space)
                    await websocket.send_json({
                        "type":    "status",
                        "message": f"ROI set with {len(points)} vertices",
                    })
                except (ValueError, TypeError) as e:
                    await websocket.send_json({"type": "error", "message": f"Invalid ROI input: {e}"})

            elif mtype == "clear_roi":
                counter.clear_roi()
                await websocket.send_json({"type": "status", "message": "ROI cleared — counting all detected people"})

            elif mtype == "get_info":
                await websocket.send_json({
                    "type": "info",
                    "data": counter.get_video_info(),
                })

            elif mtype == "set_conf":
                try:
                    val = msg.get("value")
                    if not isinstance(val, (int, float)):
                        raise ValueError("Confidence must be a number")
                    val = float(val)
                    counter.set_confidence(val)
                    await websocket.send_json({
                        "type":    "status",
                        "message": f"Confidence set to {counter.conf:.2f}",
                    })
                except (ValueError, TypeError):
                    await websocket.send_json({"type": "error", "message": "Invalid confidence value"})

            elif mtype == "set_model":
                model_name = msg.get("model", "yolov8n.pt")
                try:
                    await loop.run_in_executor(None, counter.set_model, model_name)
                    await websocket.send_json({
                        "type":    "status",
                        "message": f"Model switched to {model_name}",
                    })
                except ValueError as e:
                    await websocket.send_json({
                        "type":    "error",
                        "message": str(e),
                    })
                except Exception:
                    await websocket.send_json({
                        "type":    "error",
                        "message": "Model load failed",
                    })

            elif mtype == "set_capacity":
                try:
                    val = msg.get("value")
                    if not isinstance(val, (int, float)):
                        raise ValueError("Capacity must be a number")
                    val = int(val)
                    if val < 1:
                        raise ValueError("Capacity must be >= 1")
                    counter.set_capacity(val)
                    await websocket.send_json({
                        "type":    "status",
                        "message": f"Capacity threshold set to {counter.max_capacity}",
                    })
                except (ValueError, TypeError):
                    await websocket.send_json({"type": "error", "message": "Invalid capacity value"})

            elif mtype == "get_summary":
                await websocket.send_json({
                    "type": "summary",
                    "data": counter.get_session_summary(),
                })

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected (session {session_id[:8]})")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        streaming = False
        if frame_task:
            frame_task.cancel()

        # Persist final session stats to DB if it started
        if has_started:
            try:
                await db.end_session(
                    session_id,
                    peak_count=counter.peak_count,
                    unique_count=len(counter.unique_ids),
                )
            except Exception as e:
                logger.error(f"Failed to persist session end: {e}")

        # Ensure resources are released
        try:
            counter.release()
        except Exception as e:
            logger.error(f"Failed to release counter resources: {e}")
            
        _sessions.pop(session_id, None)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)