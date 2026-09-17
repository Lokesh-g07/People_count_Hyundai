# FactoryEye — People Analytics System

Real-time people counting with custom polygon ROI for factory floors (paint workshops, engine assembly, etc.).  
Powered by **YOLOv8 + ByteTrack** via FastAPI + WebSocket streaming.

---

## Features

| Feature | Details |
|---------|---------|
| **Active in Zone** | People currently inside the drawn ROI polygon |
| **Exited Zone** | People who entered the ROI and then left |
| **Unique Total** | All unique individuals who ever entered the zone |
| **Peak Count** | Highest simultaneous occupancy this session |
| **Avg Dwell Time** | Average seconds each person spent in the zone |
| **Session Duration** | How long the current monitoring session has been running |
| **Capacity Alert** | Red banner + warning dot when zone exceeds max capacity |
| **Sparkline Chart** | Live 60-second count history chart |
| **Polygon ROI** | Draw any shape directly on the video canvas |
| **Model Selector** | Switch between YOLOv8n/s/m/l from the UI |
| **Confidence Slider** | Tune detection threshold live (no restart needed) |
| **Shift Selector** | Tag sessions as Morning / Afternoon / Night |
| **CSV Export** | Download full session report (metrics + per-visit dwell times) |
| **PNG Snapshot** | Save the current annotated frame |
| **RTSP Reconnect** | Auto-retries up to 5× on dropped stream |
| **Multi-source** | Video files, RTSP URLs, webcam index |

---

## Setup

### 1. Install dependencies
```bash
cd PEOPLE_COUNT
pip install -r requirements.txt
```

> YOLOv8 will auto-download `yolov8n.pt` (~6 MB) on first run.

### 2. Run the server
```bash
python main.py
```

Open **http://localhost:8000** in your browser.

---

## Usage

### Step 1 — Load a video source
- Click **📁 Browse** to upload a video file, OR
- Type a path/RTSP URL in the source box: `rtsp://192.168.1.100:554/stream`
- Type `0` for your default webcam
- Click **▶ START**

### Step 2 — Configure detection (optional)
- Select a **model** from the dropdown (n = fastest, l = most accurate)
- Adjust the **CONF** slider (default 0.40 is good for factory floors)

### Step 3 — Draw your ROI polygon
- Click **✏ DRAW ROI** → canvas enters drawing mode (grid overlay appears)
- **Click** to place polygon vertices
- **Right-click** to undo last point
- **Double-click** or press **Enter** to finish (min. 3 points)
- Click **✓ APPLY** to send ROI to backend

### Step 4 — Set zone capacity
- Enter max allowed people in the **CAPACITY** box in the ROI bar
- Press Enter or click away — the backend updates immediately
- When active count exceeds capacity: red banner + warning pulse

### Step 5 — Monitor
- 🟢 **Green boxes** = person inside ROI
- ⚪ **Grey boxes**  = person outside ROI
- All metrics update live in the sidebar
- Sparkline tracks count over the last 60 seconds

### Step 6 — Export
- **⬇ Export CSV** — downloads a timestamped CSV with session metrics and individual dwell times
- **📷 Snapshot** — saves current annotated frame as PNG

---

## Counting Logic

| Metric | Definition |
|--------|-----------|
| Active in Zone | Track IDs whose **foot position** (bottom-centre of bbox) is currently inside the ROI polygon |
| Exited Zone | Track IDs that **entered** the ROI at least once and are **no longer** inside it |
| Unique Total | All unique track IDs that have **ever** been inside the ROI (cumulative) |
| Peak Count | Maximum simultaneous `active` count seen this session |
| Avg Dwell | `sum(dwell_durations) / len(dwell_durations)` — only counts **completed** visits |

> Foot position (bottom-centre of bbox) is used instead of centroid for more accurate zone detection in crowded scenes.

---

## Architecture

```
Browser (WebSocket + REST)
      │
      ▼
main.py  (FastAPI — WS /ws, POST /upload, GET /export, GET /snapshot)
      │
      ▼
people_counter.py  (YOLOv8 tracking + ROI logic + dwell/session stats)
      │
      ▼
YOLOv8n/s/m/l + ByteTrack (ultralytics)
```

---

## REST API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serve dashboard |
| `/upload` | POST | Upload video file |
| `/export` | GET | Download session CSV |
| `/snapshot` | GET | Download latest annotated PNG frame |
| `/ws` | WS | Main bidirectional stream |

---

## WebSocket Messages

### Client → Server

```json
{ "type": "start",        "source": "video.mp4" }
{ "type": "stop" }
{ "type": "restart" }
{ "type": "set_roi",      "points": [[x,y], ...], "space": "normalised" }
{ "type": "clear_roi" }
{ "type": "get_info" }
{ "type": "set_conf",     "value": 0.45 }
{ "type": "set_model",    "model": "yolov8s.pt" }
{ "type": "set_capacity", "value": 8 }
{ "type": "get_summary" }
```

### Server → Client

```json
{ "type": "frame",   "frame": "<base64 jpg>", "stats": { ... } }
{ "type": "end" }
{ "type": "info",    "data": { ... } }
{ "type": "summary", "data": { ... } }
{ "type": "status",  "message": "..." }
{ "type": "error",   "message": "..." }
```

### Stats object (per frame)

```json
{
  "active":       3,
  "exited":       1,
  "unique":       4,
  "peak":         5,
  "avg_dwell":    12.3,
  "session_secs": 45,
  "alert":        false,
  "max_capacity": 10,
  "roi_set":      true
}
```

---

## Model Selection

| Model | Speed | Accuracy | Use case |
|-------|-------|----------|----------|
| yolov8n.pt | ⚡⚡⚡ | ⭐⭐ | High-FPS RTSP streams |
| yolov8s.pt | ⚡⚡ | ⭐⭐⭐ | Balanced (recommended) |
| yolov8m.pt | ⚡ | ⭐⭐⭐⭐ | High accuracy factory |
| yolov8l.pt | 🐢 | ⭐⭐⭐⭐⭐ | GPU-only, max accuracy |

Switch at runtime from the Model dropdown — no server restart needed.