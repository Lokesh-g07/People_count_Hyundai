"""
routers/api.py — REST API endpoints for FactoryEye
====================================================
Authentication, uploads, exports, snapshots, history, models.
"""

import logging
import uuid
from pathlib import Path

import aiosqlite
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import HTMLResponse, StreamingResponse

from auth import check_auth_rate_limit, create_token, require_auth
from config import API_KEY, JWT_EXPIRY_HOURS, MAX_UPLOAD_BYTES, UPLOAD_DIR
from people_counter import ALLOWED_MODELS
from sessions import _sessions
from validation import check_magic_bytes
import db

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Auth ─────────────────────────────────────────────────────────────────────
@router.post("/api/token")
async def get_token(body: dict, request: Request, response: Response):
    """
    Exchange an API key for a JWT.
    Body: { "api_key": "<key>" }
    """
    ip = request.client.host if request.client else "unknown"
    check_auth_rate_limit(ip)

    if body.get("api_key") != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")
    token = create_token()
    response.set_cookie(
        key="factory_eye_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=JWT_EXPIRY_HOURS * 3600,
    )
    return {"status": "ok", "expires_in_hours": JWT_EXPIRY_HOURS}


@router.post("/api/logout")
async def logout(response: Response):
    """Clear the authentication cookie."""
    response.delete_cookie(key="factory_eye_token", httponly=True, samesite="lax")
    return {"status": "logged_out"}


# ── HTML ─────────────────────────────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def root():
    fe = Path("frontend/index.html")
    if fe.exists() and fe.stat().st_size > 0:
        return fe.read_text(encoding="utf-8")
    return "<h1>Factory Eye</h1><p>frontend/index.html not found</p>"


# ── Upload ───────────────────────────────────────────────────────────────────
@router.post("/upload")
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
    chunk_size = 1024 * 1024  # 1MB
    is_first_chunk = True

    try:
        with dest.open("wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break

                if is_first_chunk:
                    if not check_magic_bytes(chunk):
                        raise HTTPException(
                            status_code=400, detail="Invalid file signature"
                        )
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


# ── Export ───────────────────────────────────────────────────────────────────
@router.get("/export")
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
        # Check ownership from DB
        async with db.get_db() as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT owner_sub FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if row and row["owner_sub"] != _user.get("sub"):
                raise HTTPException(status_code=403, detail="Unauthorized")

    return StreamingResponse(
        iter([csv_text]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=factory_eye_{session_id[:8]}.csv"
        },
    )


# ── Snapshot ─────────────────────────────────────────────────────────────────
@router.get("/snapshot")
async def snapshot_png(
    session_id: str = Query(..., description="Session ID"),
    _user: dict = Depends(require_auth),
):
    """Return the latest annotated frame as PNG."""
    counter = _sessions.get(session_id)
    if counter is None:
        raise HTTPException(
            status_code=404,
            detail="Session not found (Historical snapshots are not supported)",
        )
    if getattr(counter, "owner_sub", None) != _user.get("sub"):
        raise HTTPException(status_code=403, detail="Unauthorized")

    png_bytes = counter.get_snapshot_png()
    if png_bytes is None:
        raise HTTPException(status_code=404, detail="No frame available")
    return Response(content=png_bytes, media_type="image/png")


# ── History & Models ─────────────────────────────────────────────────────────
@router.get("/api/history")
async def session_history(
    limit: int = Query(50, ge=1, le=500),
    _user: dict = Depends(require_auth),
):
    """Return recent session history from SQLite."""
    return await db.get_session_history(limit=limit)


@router.get("/api/models")
async def list_models(_user: dict = Depends(require_auth)):
    """Return the list of allowed model names."""
    return {"models": sorted(ALLOWED_MODELS)}
