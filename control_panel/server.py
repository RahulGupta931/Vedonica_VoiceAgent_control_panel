"""
Vedonica Control Panel
-----------------------
A dashboard for testing the Vedonica voice bot in the browser.

Install (nothing new — these are already in requirements.txt):
    pip install fastapi "uvicorn[standard]"

Run locally:
    python control_panel/server.py

Deploy (Render, etc.):
    Set PORT env var — server binds 0.0.0.0 automatically.

Then open the URL in your browser. Click "Run Vedonica" to start a voice
session — the browser will ask for microphone permission and route audio
through your speakers via WebRTC (no server-side mic needed).
"""

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from http import HTTPMethod
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger

from control_panel.webrtc import WebRTCSessionManager

load_dotenv(override=True)
PROMPTS_FILE = PROJECT_ROOT / "app" / "prompts.py"
PROMPTS_BACKUP = PROJECT_ROOT / "app" / "prompts.py.bak"
STATIC_DIR = Path(__file__).resolve().parent / "static"

PROMPT_PATTERN = re.compile(r'^VEDONICA_SYSTEM_PROMPT = """(.*?)"""', re.MULTILINE | re.DOTALL)

webrtc = WebRTCSessionManager()
log_buffer: list[str] = []
max_buffer = 2000
ws_clients: set[WebSocket] = set()


async def _send(ws: WebSocket, payload: dict) -> bool:
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False


async def broadcast_log(line: str) -> None:
    log_buffer.append(line)
    if len(log_buffer) > max_buffer:
        log_buffer.pop(0)
    dead = [ws for ws in ws_clients if not await _send(ws, {"type": "log", "line": line})]
    for ws in dead:
        ws_clients.discard(ws)


async def broadcast_status() -> None:
    payload = {"type": "status", "running": webrtc.running}
    dead = [ws for ws in ws_clients if not await _send(ws, payload)]
    for ws in dead:
        ws_clients.discard(ws)


webrtc.set_callbacks(broadcast_log, broadcast_status)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await webrtc.close()


app = FastAPI(title="Vedonica Control Panel", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# WebRTC routes (browser mic + speaker)
# ---------------------------------------------------------------------------

try:
    from pipecat.transports.smallwebrtc.request_handler import (
        IceCandidate,
        SmallWebRTCPatchRequest,
        SmallWebRTCRequest,
    )

    _WEBRTC_AVAILABLE = True
except ImportError:
    logger.warning("WebRTC dependencies missing — install pipecat-ai[webrtc]")
    _WEBRTC_AVAILABLE = False


if _WEBRTC_AVAILABLE:

    @app.get("/status")
    async def transport_status():
        return {"status": "ready", "transports": ["webrtc"]}

    @app.post("/start")
    async def start_agent(request: Request):
        """Start a browser WebRTC session (Pipecat client protocol)."""
        try:
            request_data = await request.json()
        except Exception:
            request_data = {}
        if request_data.get("transport", "webrtc") != "webrtc":
            return JSONResponse({"error": "Only webrtc transport is supported."}, status_code=400)
        return await webrtc.start_session(request_data)

    @app.post("/api/offer")
    async def webrtc_offer(
        request: SmallWebRTCRequest,
        background_tasks: BackgroundTasks,
        session_id: str | None = None,
    ):
        answer = await webrtc.handle_offer(request, background_tasks, session_id=session_id)
        return answer

    @app.patch("/api/offer")
    async def webrtc_ice_candidate(request: SmallWebRTCPatchRequest):
        await webrtc.handle_patch(request)
        return {"status": "success"}

    @app.api_route(
        "/sessions/{session_id}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_session(
        session_id: str,
        path: str,
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        if not path.endswith("api/offer"):
            return Response(status_code=404)

        try:
            request_data = await request.json()
        except Exception as exc:
            logger.error(f"Failed to parse WebRTC proxy request: {exc}")
            return Response(content="Invalid WebRTC request", status_code=400)

        if request.method == HTTPMethod.POST.value:
            webrtc_request = SmallWebRTCRequest(
                sdp=request_data["sdp"],
                type=request_data["type"],
                pc_id=request_data.get("pc_id"),
                restart_pc=request_data.get("restart_pc"),
                request_data=request_data.get("request_data")
                or request_data.get("requestData"),
            )
            return await webrtc_offer(webrtc_request, background_tasks, session_id=session_id)

        if request.method == HTTPMethod.PATCH.value:
            patch_request = SmallWebRTCPatchRequest(
                pc_id=request_data["pc_id"],
                candidates=[IceCandidate(**c) for c in request_data.get("candidates", [])],
            )
            return await webrtc_ice_candidate(patch_request)

        return Response(status_code=405)


# ---------------------------------------------------------------------------
# Control panel routes
# ---------------------------------------------------------------------------


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def get_status():
    return {"running": webrtc.running}


@app.post("/api/run")
async def run_bot():
    if webrtc.running:
        return JSONResponse({"error": "Vedonica is already running."}, status_code=409)
    if not _WEBRTC_AVAILABLE:
        return JSONResponse(
            {"error": "WebRTC is not available on this server. Install pipecat-ai[webrtc]."},
            status_code=503,
        )
    await broadcast_log("Ready — click Run Vedonica in the browser to connect your mic.")
    return {"running": False, "mode": "webrtc", "message": "Use the Run button to start a browser voice session."}


@app.post("/api/stop")
async def stop_bot():
    await webrtc.stop_all()
    return {"running": False}


@app.get("/api/prompt")
async def get_prompt():
    if not PROMPTS_FILE.exists():
        return JSONResponse({"error": f"{PROMPTS_FILE} not found"}, status_code=404)
    text = PROMPTS_FILE.read_text(encoding="utf-8")
    match = PROMPT_PATTERN.search(text)
    if not match:
        return JSONResponse(
            {"error": "Couldn't find VEDONICA_SYSTEM_PROMPT in app/prompts.py"},
            status_code=500,
        )
    return {"prompt": match.group(1).strip("\n"), "has_backup": PROMPTS_BACKUP.exists()}


@app.post("/api/prompt")
async def save_prompt(payload: dict):
    new_prompt = payload.get("prompt", "")
    if not isinstance(new_prompt, str) or not new_prompt.strip():
        return JSONResponse({"error": "Prompt can't be empty."}, status_code=400)
    if '"""' in new_prompt:
        return JSONResponse(
            {"error": 'Prompt can\'t contain a triple-quote (""") — it would break app/prompts.py.'},
            status_code=400,
        )
    if not PROMPTS_FILE.exists():
        return JSONResponse({"error": f"{PROMPTS_FILE} not found"}, status_code=404)

    text = PROMPTS_FILE.read_text(encoding="utf-8")
    if not PROMPT_PATTERN.search(text):
        return JSONResponse(
            {"error": "Couldn't find VEDONICA_SYSTEM_PROMPT in app/prompts.py"},
            status_code=500,
        )

    if not PROMPTS_BACKUP.exists():
        PROMPTS_BACKUP.write_text(text, encoding="utf-8")

    replacement = f'VEDONICA_SYSTEM_PROMPT = """\n{new_prompt.strip()}\n"""'
    new_text = PROMPT_PATTERN.sub(lambda _m: replacement, text, count=1)
    PROMPTS_FILE.write_text(new_text, encoding="utf-8")
    return {"saved": True}


@app.post("/api/prompt/revert")
async def revert_prompt():
    if not PROMPTS_BACKUP.exists():
        return JSONResponse(
            {"error": "No backup yet — save a change at least once first."}, status_code=404
        )
    text = PROMPTS_BACKUP.read_text(encoding="utf-8")
    PROMPTS_FILE.write_text(text, encoding="utf-8")
    match = PROMPT_PATTERN.search(text)
    return {"reverted": True, "prompt": match.group(1).strip("\n") if match else ""}


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        await websocket.send_json({"type": "status", "running": webrtc.running})
        for line in log_buffer:
            await websocket.send_json({"type": "log", "line": line})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_clients.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8765"))
    uvicorn.run(app, host=host, port=port)
