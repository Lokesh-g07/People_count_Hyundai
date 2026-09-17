"""
people_counter.py — YOLOv8 + ByteTrack people counter with polygon ROI
=======================================================================
Enhancements over initial version:
 - Dwell-time tracking per track-ID (seconds each person spent inside zone)
 - max_capacity alert flag surfaced in every stats dict
 - Peak-count and session-duration tracking
 - Hot-swap model path and confidence threshold at runtime
 - export_csv() — returns a full session summary as CSV text
 - get_session_summary() — structured dict for the sidebar
 - Automatic RTSP reconnect (up to N retries on frame-read failure)
 - Model whitelist to prevent arbitrary code execution via pickle
 - Explicit GPU/CPU device pinning for reproducible performance
 - Counts all detected people when no ROI polygon is set
 - on_dwell_complete callback for external persistence (SQLite)
"""

import base64
import csv
import io
import logging
import time
from typing import Callable
import threading

import cv2
import numpy as np
import torch
from ultralytics import YOLO

logger = logging.getLogger(__name__)

# ── Security: model whitelist ───────────────────────────────────────────────
# Only these bundled model names are allowed.  Never load client-supplied paths.
ALLOWED_MODELS = frozenset({
    "yolov8n.pt",
    "yolov8s.pt",
    "yolov8m.pt",
    "yolov8l.pt",
    "yolov8x.pt",
})


class PeopleCounter:
    """
    People counter with custom polygon ROI.

    Tracks:
      active_in_zone  — IDs currently inside the ROI polygon this frame
      exited          — IDs that entered the ROI at least once and have since left
      unique_total    — All unique IDs that have ever been inside the ROI
      avg_dwell_secs  — Average seconds each completed visit lasted
      peak_count      — Maximum simultaneous people ever seen inside zone
      alert           — True when active count exceeds max_capacity
    """

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        conf: float = 0.40,
        max_capacity: int = 10,
        device: str | None = None,
        on_dwell_complete: Callable[[int, float], None] | None = None,
    ):
        # ── Validate model against whitelist (prevents RCE via pickle) ──
        if model_path not in ALLOWED_MODELS:
            raise ValueError(
                f"Model '{model_path}' is not allowed. "
                f"Permitted models: {', '.join(sorted(ALLOWED_MODELS))}"
            )

        self._lock = threading.Lock()
        self.model_path = model_path
        self.conf = conf
        self.max_capacity = max_capacity

        # ── Device pinning ──────────────────────────────────────────────
        if device is None:
            self.device = "0" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device
        logger.info(f"Using device: {self.device}")

        self.model = YOLO(model_path)

        # ── Dwell callback for external persistence (e.g. SQLite) ──────
        self.on_dwell_complete = on_dwell_complete

        # Video source
        self.cap: cv2.VideoCapture | None = None
        self.frame_width: int = 640
        self.frame_height: int = 480
        self._is_running: bool = False

        # ROI
        self.roi_polygon: np.ndarray | None = None   # shape (N, 2), pixel coords

        # Counting state
        self.active_ids: set[int] = set()    # currently inside ROI
        self.unique_ids: set[int] = set()    # ever entered ROI
        self.exited_ids: set[int] = set()    # entered then left

        # Dwell-time tracking
        self.entry_times: dict[int, float] = {}   # tid → epoch when entered zone
        self.dwell_log: list[float] = []           # completed dwell durations (secs)

        # Session-level stats
        self.peak_count: int = 0
        self.session_start: float | None = None

        # RTSP reconnect
        self._source: str = ""
        self._reconnect_attempts: int = 0
        self._max_reconnect: int = 5

        # Last annotated frame (raw BGR) for snapshot endpoint
        self.last_frame: np.ndarray | None = None

    # ------------------------------------------------------------------ #
    # Source management
    # ------------------------------------------------------------------ #

    def start(self, source: str) -> bool:
        """Open a video source (file path, RTSP URL, or camera index)."""
        self._release_cap()
        self._source = source
        return self._open_source()

    def _open_source(self) -> bool:
        try:
            src = int(self._source) if self._source.isdigit() else self._source
            self.cap = cv2.VideoCapture(src)
            if not self.cap.isOpened():
                logger.error(f"Cannot open source: {self._source}")
                return False
            self.frame_width  = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            self._is_running = True
            self._reset_counts()
            self.session_start = time.time()
            # self._reconnect_attempts is reset on successful read in get_frame
            logger.info(f"Opened source {self._source}  {self.frame_width}x{self.frame_height}")
            return True
        except Exception as e:
            logger.error(f"Error opening source: {e}")
            return False

    def stop(self) -> None:
        self._is_running = False

    def restart(self) -> None:
        """Seek back to beginning (useful for video files)."""
        if self.cap:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._is_running = True
            self._reset_counts()
            self.session_start = time.time()

    @property
    def is_running(self) -> bool:
        return self._is_running and self.cap is not None and self.cap.isOpened()

    # ------------------------------------------------------------------ #
    # Runtime tuning
    # ------------------------------------------------------------------ #

    def set_confidence(self, conf: float) -> None:
        """Hot-swap detection confidence threshold (0–1)."""
        self.conf = max(0.05, min(0.99, float(conf)))
        logger.info(f"Confidence threshold set to {self.conf:.2f}")

    def set_model(self, model_path: str) -> None:
        """Hot-swap YOLO model — only whitelisted names are accepted."""
        if model_path not in ALLOWED_MODELS:
            raise ValueError(
                f"Model '{model_path}' is not allowed. "
                f"Permitted models: {', '.join(sorted(ALLOWED_MODELS))}"
            )
        try:
            new_model = YOLO(model_path)
            with self._lock:
                self.model = new_model
                self.model_path = model_path
            logger.info(f"Model switched to {model_path}")
        except Exception as e:
            logger.error(f"Failed to load model {model_path}: {e}")
            raise

    def set_capacity(self, capacity: int) -> None:
        self.max_capacity = max(1, int(capacity))
        logger.info(f"Max capacity set to {self.max_capacity}")

    # ------------------------------------------------------------------ #
    # ROI management
    # ------------------------------------------------------------------ #

    def set_roi(self, points: list[list[float]], coordinate_space: str = "pixel") -> None:
        """
        Set the polygon ROI.
        points           : list of [x, y]  — pixel coords OR normalised [0‑1]
        coordinate_space : 'pixel' | 'normalised'
        """
        if not points or len(points) < 3:
            return
        pts = np.array(points, dtype=np.float32)
        if coordinate_space == "normalised":
            pts[:, 0] *= self.frame_width
            pts[:, 1] *= self.frame_height
            
        self._finalize_active_dwells()
        self.roi_polygon = pts.astype(np.int32)
        self._reset_counts()
        logger.info(f"ROI set with {len(points)} vertices")

    def clear_roi(self) -> None:
        self._finalize_active_dwells()
        self.roi_polygon = None
        self._reset_counts()

    # ------------------------------------------------------------------ #
    # Frame processing
    # ------------------------------------------------------------------ #

    def get_frame(self) -> tuple[str, dict] | None:
        """
        Read one frame, run detection + tracking, annotate and return
        (base64_jpeg, stats_dict) or None on end-of-stream.
        """
        if not self.is_running:
            return None

        ret = False
        frame = None
        while self.is_running:
            ret, frame = self.cap.read()
            
            # --- Handle read failure (RTSP reconnect) ---
            if not ret:
                if self._source.startswith("rtsp") and self._reconnect_attempts < self._max_reconnect:
                    self._reconnect_attempts += 1
                    logger.warning(f"Frame read failed — reconnect attempt {self._reconnect_attempts}")
                    time.sleep(1.5)
                    self.cap.release()
                    self._open_source()
                    continue # Retry reading
                else:
                    self._is_running = False
                    return None
            else:
                self._reconnect_attempts = 0 # Reset attempts after a successful read
                break # Got a frame successfully

        if not ret or frame is None:
            self._is_running = False
            return None

        now = time.time()

        # --- YOLO tracking — person class only (class 0 in COCO) ---
        with self._lock:
            results = self.model.track(
                frame,
                persist=True,
                classes=[0],
                conf=self.conf,
                verbose=False,
                tracker="bytetrack.yaml",
                device=self.device,
            )

        current_frame_ids: set[int] = set()

        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            if boxes.id is not None:
                track_ids  = boxes.id.int().cpu().tolist()
                xyxy_list  = boxes.xyxy.cpu().numpy()
                conf_list  = boxes.conf.cpu().tolist()

                for box, tid, det_conf in zip(xyxy_list, track_ids, conf_list):
                    x1, y1, x2, y2 = map(int, box)
                    cx = (x1 + x2) // 2
                    cy = y2   # foot position — more stable for ROI

                    in_roi = self._point_in_roi(cx, cy)

                    if in_roi:
                        current_frame_ids.add(tid)
                        if tid not in self.unique_ids:
                            self.unique_ids.add(tid)
                            self.entry_times[tid] = now   # record entry

                    # Colour: green = inside ROI, grey = outside
                    color = (0, 220, 80) if in_roi else (160, 160, 160)

                    # Bounding box
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                    # ID + confidence label
                    label = f"#{tid}  {det_conf:.0%}"
                    lw, lh = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.50, 2)[0]
                    cv2.rectangle(frame, (x1, y1 - lh - 6), (x1 + lw + 6, y1), color, -1)
                    cv2.putText(frame, label, (x1 + 3, y1 - 4),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 0, 0), 2)

                    # Foot dot
                    cv2.circle(frame, (cx, cy), 5, color, -1)

        # --- Update counting state ---
        newly_exited = self.active_ids - current_frame_ids
        for tid in newly_exited:
            self.exited_ids.add(tid)
            # Record dwell time for this ID if we know when they entered
            if tid in self.entry_times:
                dwell = now - self.entry_times.pop(tid)
                self.dwell_log.append(dwell)
                # Notify external persistence layer (e.g. SQLite)
                if self.on_dwell_complete:
                    try:
                        self.on_dwell_complete(tid, dwell)
                    except Exception as e:
                        logger.error(f"Dwell callback error: {e}")

        self.active_ids = current_frame_ids

        # If someone re-enters, remove from exited
        self.exited_ids -= self.active_ids

        # Peak count
        if len(self.active_ids) > self.peak_count:
            self.peak_count = len(self.active_ids)

        # --- Draw ROI overlay ---
        if self.roi_polygon is not None:
            overlay = frame.copy()
            cv2.fillPoly(overlay, [self.roi_polygon], (0, 210, 255))
            cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
            cv2.polylines(frame, [self.roi_polygon], isClosed=True,
                          color=(0, 210, 255), thickness=2)
            for pt in self.roi_polygon:
                cv2.circle(frame, tuple(pt), 5, (0, 210, 255), -1)

        # --- Alert watermark when over capacity ---
        active_count = len(self.active_ids)
        alert = active_count > self.max_capacity
        if alert:
            h, w = frame.shape[:2]
            cv2.rectangle(frame, (0, 0), (w, h), (0, 0, 220), 6)
            warn_label = f"OVERCAPACITY! {active_count}/{self.max_capacity}"
            tw, th = cv2.getTextSize(warn_label, cv2.FONT_HERSHEY_DUPLEX, 1.0, 2)[0]
            cv2.rectangle(frame, (0, 0), (tw + 16, th + 16), (0, 0, 200), -1)
            cv2.putText(frame, warn_label, (8, th + 8),
                        cv2.FONT_HERSHEY_DUPLEX, 1.0, (255, 255, 255), 2)

        # --- Stats dict ---
        avg_dwell = (sum(self.dwell_log) / len(self.dwell_log)) if self.dwell_log else 0.0
        session_secs = (time.time() - self.session_start) if self.session_start else 0.0
        stats = {
            "active":        active_count,
            "exited":        len(self.exited_ids),
            "unique":        len(self.unique_ids),
            "roi_set":       self.roi_polygon is not None,
            "alert":         alert,
            "peak":          self.peak_count,
            "avg_dwell":     round(avg_dwell, 1),
            "session_secs":  round(session_secs, 0),
            "max_capacity":  self.max_capacity,
        }

        # Keep last frame for snapshot endpoint
        self.last_frame = frame.copy()

        # Encode
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        frame_b64 = base64.b64encode(buf).decode("utf-8")
        return frame_b64, stats

    # ------------------------------------------------------------------ #
    # Info / Export
    # ------------------------------------------------------------------ #

    def get_video_info(self) -> dict:
        if not self.cap:
            return {}
        fps   = self.cap.get(cv2.CAP_PROP_FPS) or 25
        total = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        return {
            "width":        self.frame_width,
            "height":       self.frame_height,
            "fps":          round(fps, 2),
            "total_frames": total,
            "model":        self.model_path,
            "conf":         self.conf,
            "max_capacity": self.max_capacity,
            "device":       self.device,
        }

    def get_session_summary(self) -> dict:
        avg_dwell = (sum(self.dwell_log) / len(self.dwell_log)) if self.dwell_log else 0.0
        session_secs = (time.time() - self.session_start) if self.session_start else 0.0
        return {
            "unique_visitors":  len(self.unique_ids),
            "peak_count":       self.peak_count,
            "avg_dwell_secs":   round(avg_dwell, 1),
            "total_visits":     len(self.dwell_log),
            "session_duration": round(session_secs, 0),
            "max_capacity":     self.max_capacity,
            "model":            self.model_path,
            "conf":             self.conf,
        }

    def export_csv(self) -> str:
        """Return a CSV string with the full session summary."""
        buf = io.StringIO()
        writer = csv.writer(buf)

        # Session summary header
        writer.writerow(["FACTORY EYE — Session Summary"])
        writer.writerow(["Generated", time.strftime("%Y-%m-%d %H:%M:%S")])
        writer.writerow([])

        # Summary rows
        summary = self.get_session_summary()
        writer.writerow(["Metric", "Value"])
        writer.writerow(["Unique Visitors",     summary["unique_visitors"]])
        writer.writerow(["Peak Count",          summary["peak_count"]])
        writer.writerow(["Avg Dwell (s)",       summary["avg_dwell_secs"]])
        writer.writerow(["Total Visits",        summary["total_visits"]])
        writer.writerow(["Session Duration (s)", summary["session_duration"]])
        writer.writerow(["Max Capacity",        summary["max_capacity"]])
        writer.writerow(["Model",               summary["model"]])
        writer.writerow(["Confidence",          summary["conf"]])
        writer.writerow([])

        # Individual dwell times
        writer.writerow(["Visit #", "Dwell Time (s)"])
        for i, dwell in enumerate(self.dwell_log, 1):
            writer.writerow([i, round(dwell, 2)])

        return buf.getvalue()

    def get_snapshot_png(self) -> bytes | None:
        """Return the last annotated frame as PNG bytes."""
        if self.last_frame is None:
            return None
        _, buf = cv2.imencode(".png", self.last_frame)
        return buf.tobytes()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _point_in_roi(self, x: int, y: int) -> bool:
        # When no ROI is set, treat the entire frame as the zone
        # so users see counts immediately without drawing a polygon.
        if self.roi_polygon is None or len(self.roi_polygon) < 3:
            return True
        result = cv2.pointPolygonTest(
            self.roi_polygon.astype(np.float32),
            (float(x), float(y)),
            measureDist=False,
        )
        return result >= 0

    def _finalize_active_dwells(self) -> None:
        now = time.time()
        for track_id, entry_time in self.entry_times.items():
            if track_id not in self.exited_ids:
                dwell = now - entry_time
                self.dwell_log.append(dwell)
                self.exited_ids.add(track_id)
                if self.on_dwell_complete:
                    try:
                        self.on_dwell_complete(track_id, dwell)
                    except Exception as e:
                        logger.error(f"Dwell callback failed: {e}")

    def _reset_counts(self) -> None:
        self.active_ids.clear()
        self.exited_ids.clear()
        self.unique_ids.clear()
        self.entry_times.clear()
        self.dwell_log.clear()
        self.peak_count = 0
        self.session_start = time.time()

    def _release_cap(self) -> None:
        if self.cap:
            self.cap.release()
            self.cap = None

    def release(self) -> None:
        self._is_running = False
        self._release_cap()