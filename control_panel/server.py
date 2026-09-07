"""
Vedonica Control Panel
-----------------------
A small local dashboard for testing the Vedonica voice bot without living
in the terminal.

Install (nothing new — these are already in requirements.txt):
    pip install fastapi "uvicorn[standard]"

Run:
    python control_panel/server.py

Then open http://127.0.0.1:8765 — on the SAME machine that has the
microphone/speakers you want the bot to use. This dashboard just launches
`local_run.py` as a child process on this machine; no audio goes over the
network.

What it does:
  - Run / Stop button    -> starts/stops `python -u local_run.py` and
                             streams its console output live into the page.
  - System prompt editor -> reads/writes VEDONICA_SYSTEM_PROMPT in
                             app/prompts.py, so you can tweak Vedonica's
                             persona and re-run without opening an editor.
                             A one-time backup is kept at
                             app/prompts.py.bak before the first save, and
                             there's a "Revert" button to restore it.
"""

import asyncio
import os
import re
import signal
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_RUN_SCRIPT = PROJECT_ROOT / "local_run.py"
PROMPTS_FILE = PROJECT_ROOT / "app" / "prompts.py"
PROMPTS_BACKUP = PROJECT_ROOT / "app" / "prompts.py.bak"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Matches the ACTIVE `VEDONICA_SYSTEM_PROMPT = """...."""` assignment only —
# anchored at column 0, so the large commented-out `# VEDONICA_SYSTEM_PROMPT
# = """...."""` block above it in prompts.py is never touched.
PROMPT_PATTERN = re.compile(r'^VEDONICA_SYSTEM_PROMPT = """(.*?)"""', re.MULTILINE | re.DOTALL)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

app = FastAPI(title="Vedonica Control Panel")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Subprocess + log broadcasting
# ---------------------------------------------------------------------------

class BotProcess:
    def __init__(self) -> None:
        self.process: Optional[asyncio.subprocess.Process] = None
        self.log_buffer: list[str] = []
        self.max_buffer = 2000
        self.clients: set[WebSocket] = set()

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    async def _send(self, ws: WebSocket, payload: dict) -> bool:
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            return False

    async def broadcast_log(self, line: str) -> None:
        self.log_buffer.append(line)
        if len(self.log_buffer) > self.max_buffer:
            self.log_buffer.pop(0)
        dead = [ws for ws in self.clients if not await self._send(ws, {"type": "log", "line": line})]
        for ws in dead:
            self.clients.discard(ws)

    async def broadcast_status(self) -> None:
        payload = {"type": "status", "running": self.running}
        dead = [ws for ws in self.clients if not await self._send(ws, payload)]
        for ws in dead:
            self.clients.discard(ws)

    async def start(self) -> None:
        if self.running:
            raise RuntimeError("already running")
        if not LOCAL_RUN_SCRIPT.exists():
            raise FileNotFoundError(f"local_run.py not found at {LOCAL_RUN_SCRIPT}")

        self.log_buffer.clear()
        await self.broadcast_log(f"$ {sys.executable} local_run.py")

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(LOCAL_RUN_SCRIPT),
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        asyncio.create_task(self._read_output())
        await self.broadcast_status()

    async def _read_output(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            async for raw_line in self.process.stdout:
                line = ANSI_ESCAPE.sub("", raw_line.decode(errors="replace").rstrip("\n"))
                await self.broadcast_log(line)
        finally:
            code = await self.process.wait()
            await self.broadcast_log(f"--- process exited (code {code}) ---")
            await self.broadcast_status()

    async def stop(self) -> None:
        if not self.running or self.process is None:
            return
        # local_run.py's shutdown path listens for KeyboardInterrupt (Ctrl+C)
        # to close the DB pool / pipeline cleanly, so try SIGINT first and
        # only escalate if it doesn't exit in time.
        try:
            if sys.platform == "win32":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGINT)
            await asyncio.wait_for(self.process.wait(), timeout=6)
            return
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        try:
            self.process.terminate()
            await asyncio.wait_for(self.process.wait(), timeout=4)
        except (asyncio.TimeoutError, ProcessLookupError):
            self.process.kill()


bot = BotProcess()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def get_status():
    return {"running": bot.running}


@app.post("/api/run")
async def run_bot():
    try:
        await bot.start()
    except RuntimeError:
        return JSONResponse({"error": "Vedonica is already running."}, status_code=409)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return {"running": True}


@app.post("/api/stop")
async def stop_bot():
    if not bot.running:
        return JSONResponse({"error": "Vedonica isn't running."}, status_code=409)
    await bot.stop()
    return {"running": bot.running}


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
    bot.clients.add(websocket)
    try:
        await websocket.send_json({"type": "status", "running": bot.running})
        for line in bot.log_buffer:
            await websocket.send_json({"type": "log", "line": line})
        while True:
            # Client never sends anything meaningful; this just lets us
            # detect disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        bot.clients.discard(websocket)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8765)))
