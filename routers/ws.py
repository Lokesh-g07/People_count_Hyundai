"""
routers/ws.py — WebSocket endpoint for FactoryEye
===================================================
Real-time video streaming, frame processing, and session management.
"""

import asyncio
import json
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from auth import verify_token
from config import MAX_SESSIONS
from people_counter import PeopleCounter
from sessions import _sessions
from validation import validate_source
import db

logger = logging.getLogger(__name__)

router = APIRouter()

TARGET_FPS = 25
FRAME_INTERVAL = 1.0 / TARGET_FPS


@router.websocket("/ws")
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
        await websocket.close(
            code=1013,
            reason="Maximum concurrent sessions reached. Try again later.",
        )
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
    # Token bucket: 5 tokens max, refills 2 per second (for general messages)
    rl_tokens = 5.0
    rl_last_time = time.time()

    # Expensive messages (model/roi changes) — max 1 per 2 seconds
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
                    await websocket.send_json(
                        {"type": "error", "message": str(e)}
                    )
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
                    "type": "frame",
                    "frame": frame_b64,
                    "stats": stats,
                })
            except Exception:
                streaming = False
                break

            elapsed = loop.time() - t0
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
                await websocket.send_json({
                    "type": "error",
                    "message": "Rate limit exceeded. Sending too fast.",
                })
                continue

            rl_tokens -= 1.0

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type", "")

            # Expensive Operations Rate Limiting
            if mtype in (
                "set_roi", "set_model", "set_conf", "set_capacity", "clear_roi"
            ):
                if now - rl_last_expensive_time < 2.0:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Rate limit exceeded for configuration changes.",
                    })
                    continue
                rl_last_expensive_time = now

            if mtype == "start":
                if has_started:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Session already started. Stop it first.",
                    })
                    continue

                source_raw = msg.get("source", "")
                try:
                    source = validate_source(source_raw)
                except ValueError as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Invalid source: {e}",
                    })
                    continue

                ok = await loop.run_in_executor(None, counter.start, source)
                if ok:
                    has_started = True
                    # Register dwell callback for this session
                    sid = session_id  # capture for closure

                    def on_dwell(
                        track_id: int, dwell_secs: float, _sid=sid
                    ):
                        if loop.is_running():
                            asyncio.run_coroutine_threadsafe(
                                db.record_dwell(_sid, track_id, dwell_secs),
                                loop,
                            )

                    counter.on_dwell_complete = on_dwell

                    # Create DB session record
                    await db.create_session(
                        session_id,
                        source,
                        counter.model_path,
                        counter.device,
                        counter.owner_sub,
                    )

                    info = counter.get_video_info()
                    # Clarify server-side camera usage
                    if source.isdigit():
                        await websocket.send_json({
                            "type": "status",
                            "message": (
                                f"Opening server-side camera device {source} "
                                "(not browser webcam)"
                            ),
                        })
                    await websocket.send_json({"type": "info", "data": info})
                    streaming = True
                    if frame_task and not frame_task.done():
                        frame_task.cancel()
                    frame_task = asyncio.create_task(stream_frames())
                else:
                    await websocket.send_json({
                        "type": "error",
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
                    space = msg.get("space", "normalised")
                    if not isinstance(points, list):
                        raise ValueError("points must be a list")
                    if len(points) < 3 and len(points) > 0:
                        raise ValueError("ROI requires at least 3 points")
                    if len(points) > 50:
                        raise ValueError("ROI has too many points")
                    for pt in points:
                        if not isinstance(pt, list) or len(pt) != 2:
                            raise ValueError("Each point must be [x, y]")
                        if not (
                            isinstance(pt[0], (int, float))
                            and isinstance(pt[1], (int, float))
                        ):
                            raise ValueError("Coordinates must be numeric")
                    counter.set_roi(points, coordinate_space=space)
                    await websocket.send_json({
                        "type": "status",
                        "message": f"ROI set with {len(points)} vertices",
                    })
                except (ValueError, TypeError) as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Invalid ROI input: {e}",
                    })

            elif mtype == "clear_roi":
                counter.clear_roi()
                await websocket.send_json({
                    "type": "status",
                    "message": "ROI cleared — counting all detected people",
                })

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
                        "type": "status",
                        "message": f"Confidence set to {counter.conf:.2f}",
                    })
                except (ValueError, TypeError):
                    await websocket.send_json({
                        "type": "error",
                        "message": "Invalid confidence value",
                    })

            elif mtype == "set_model":
                model_name = msg.get("model", "yolov8n.pt")
                try:
                    await loop.run_in_executor(
                        None, counter.set_model, model_name
                    )
                    await websocket.send_json({
                        "type": "status",
                        "message": f"Model switched to {model_name}",
                    })
                except ValueError as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": str(e),
                    })
                except Exception:
                    await websocket.send_json({
                        "type": "error",
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
                        "type": "status",
                        "message": (
                            f"Capacity threshold set to {counter.max_capacity}"
                        ),
                    })
                except (ValueError, TypeError):
                    await websocket.send_json({
                        "type": "error",
                        "message": "Invalid capacity value",
                    })

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
        if frame_task and not frame_task.done():
            frame_task.cancel()
        counter.stop()  # Ensure resources are released on abrupt disconnect

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
