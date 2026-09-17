# FactoryEye — People Analytics System

Real-time people counting with custom polygon ROI for factory floors (paint workshops, engine assembly, etc.). Powered by **YOLOv8 + ByteTrack** via FastAPI + WebSocket streaming.

## Table of Contents
- [Problem Statement](#problem-statement)
- [Solution Overview](#solution-overview)
- [Functional Capabilities](#functional-capabilities)
- [System Architecture](#system-architecture)
- [Technical Stack](#technical-stack)
- [Quick Start](#quick-start)
- [Failure & Edge Case Handling](#failure--edge-case-handling)
- [API & WebSocket Examples](#api--websocket-examples)
- [Test Suite](#test-suite)
- [Configuration](#configuration)
- [Project Structure](#project-structure)

---

## Problem Statement
Factory floors require real-time occupancy monitoring for safety, compliance, and operational efficiency. Traditional camera systems record video but lack actionable, real-time analytics. Manual counting is error-prone and scales poorly. An automated system is needed to accurately track dwell times, enforce capacity limits, and measure unique versus active visitors in dynamic, custom regions of interest (ROIs).

## Solution Overview
FactoryEye is a high-performance computer vision backend that consumes RTSP camera streams or local video files. It runs real-time object detection and tracking using YOLOv8 and ByteTrack, and streams live annotated video and statistics to a web client via WebSockets. Historical session data and event metrics are persisted in SQLite, and the system is secured via JWT authentication and rigorous connection lifecycle management.

---

## Functional Capabilities

| Feature | Details |
|---------|---------|
| **Active in Zone** | People currently inside the drawn ROI polygon (based on foot position) |
| **Exited Zone** | People who entered the ROI and then left |
| **Unique Total** | All unique individuals who ever entered the zone |
| **Peak Count** | Highest simultaneous occupancy this session |
| **Avg Dwell Time** | Average seconds each person spent in the zone |
| **Capacity Alert** | Red banner + warning dot when zone exceeds max capacity |
| **Polygon ROI** | Draw any shape directly on the video canvas |
| **Model Selector** | Hot-swap YOLOv8n/s/m/l at runtime |
| **CSV Export** | Download full session report (metrics + per-visit dwell times) |
| **RTSP Reconnect** | Auto-retries up to 5× on dropped network streams |

---

## System Architecture

```text
Browser (WebSocket + REST)
      │ (JWT Cookie Auth)
      ▼
main.py (FastAPI — WS /ws, POST /api/token, GET /export)
      │
      ▼
people_counter.py (YOLOv8 tracking + ROI logic + thread locks)
      │
      ├──▶ ultralytics (YOLOv8n/s/m/l + ByteTrack)
      │
      ▼
db.py (aiosqlite WAL mode persistence)
```

## Technical Stack
- **API Framework:** FastAPI, Uvicorn, Starlette
- **Computer Vision:** Ultralytics YOLOv8, OpenCV (cv2), PyTorch
- **Tracking:** ByteTrack
- **Database:** SQLite (async via `aiosqlite`)
- **Security:** PyJWT for Cookie-based JWT Auth
- **Frontend:** Vanilla HTML, CSS, JavaScript

---

## Quick Start

### 1. Clone and enter the project
```bash
git clone https://github.com/Lokesh-g07/People_count_Hyundai.git
cd People_count_Hyundai
```

### 2. Create a virtual environment and install dependencies
```bash
python -m venv .venv
source .venv/bin/activate  # Or `.venv\Scripts\activate` on Windows
pip install -r requirements.txt
```
*(YOLOv8 will auto-download `yolov8n.pt` (~6 MB) on the first run.)*

### 3. Start the Unified API
Ensure you provide secure credentials if running in production:
```bash
FACTORY_EYE_ENV=development python main.py
```

### 4. Verify it works
- **Dashboard:** Open `http://localhost:8000`
- **Login:** The default development API key is `factory-eye-dev-key`
- **Start Stream:** Enter `0` for webcam or a valid video/RTSP path and hit "Start".

---

## Failure & Edge Case Handling

- **RTSP Connection Drops:** Automatically retries reading from the source up to 5 times with exponential backoff before cleanly terminating the session.
- **Concurrent DB Writes:** SQLite is configured with `PRAGMA journal_mode=WAL` and `busy_timeout=20000` to prevent `SQLITE_BUSY` deadlocks during high-traffic dwell logging.
- **Hot-swapping Models:** Handled safely via thread locks (`threading.Lock()`) around PyTorch inference to prevent race conditions or memory corruption.
- **WebSocket Disconnections:** Client disconnects immediately trigger asynchronous teardown of YOLO models and OpenCV captures to prevent resource exhaustion.
- **Authentication Abuse:** `/api/token` limits login attempts via an IP-based token-bucket rate limiter.
- **SSRF Protection:** Malicious or internal loopback RTSP streams are blocked by default. Legitimate factory internal IPs must be explicitly whitelisted.

---

## API & WebSocket Examples

### REST API
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/token` | POST | Exchange API key for HttpOnly JWT Cookie |
| `/api/logout` | POST | Clear session cookie |
| `/export` | GET | Download historical session CSV |
| `/upload` | POST | Upload a local video file (Sanitized UUID) |

### WebSocket Protocol (`/ws`)
**Client → Server (Control):**
```json
{ "type": "start", "source": "video.mp4" }
{ "type": "set_roi", "points": [[100,100], [200,100], [150,200]], "space": "pixel" }
{ "type": "set_conf", "value": 0.45 }
{ "type": "set_model", "model": "yolov8s.pt" }
```

**Server → Client (Telemetry):**
```json
{
  "type": "frame",
  "frame": "<base64_encoded_jpeg>",
  "stats": {
    "active": 3,
    "unique": 14,
    "peak": 5,
    "avg_dwell": 12.3,
    "alert": false
  }
}
```

---

## Test Suite
FactoryEye includes a comprehensive test suite testing both the API boundaries and internal concurrency logic. The tests use a `FakeVideoCapture` deterministic mock to prevent hardware deadlocks during CI/CD.

```bash
# Run Security and Architecture tests
pytest -v scratch/test_security_remediations.py scratch/test_batch2.py
```

---

## Configuration
FactoryEye behavior is heavily customizable via Environment Variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FACTORY_EYE_ENV` | `production` | Set to `development` to bypass strict secret checks |
| `FACTORY_EYE_SECRET` | `dev-secret-...` | JWT signing secret (Required in Prod) |
| `FACTORY_EYE_API_KEY` | `factory-eye-...` | API Key for generating tokens |
| `FACTORY_EYE_MAX_SESSIONS`| `5` | Hard limit on concurrent ML inferences |
| `FACTORY_EYE_RTSP_ALLOWLIST`| `""` | Comma-separated CIDR blocks for local cameras |
| `FACTORY_EYE_AUTH_RL_ATTEMPTS`| `5` | Allowed failed logins before HTTP 429 |

---

## Project Structure
```text
PEOPLE_COUNT/
├── main.py                 # FastAPI Application & WS Routers
├── people_counter.py       # YOLOv8 Tracking & Vision Logic
├── db.py                   # Async SQLite Persistence
├── requirements.txt        # Python Dependencies
├── frontend/
│   └── index.html          # Vanilla JS/CSS Dashboard
├── uploads/                # Sanitized temporary video uploads
└── scratch/                # Test suites and mocks
```