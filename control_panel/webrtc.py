"""Browser WebRTC audio sessions for the control panel.

Uses the visitor's microphone and speakers via WebRTC (works on HTTPS
deployments like Render). Same bot pipeline as test_console_bot.py.
"""

import asyncio
import os
import uuid
from typing import Any, Awaitable, Callable, Optional

from fastapi import BackgroundTasks
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    ConnectionMode,
    IceCandidate,
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from app import db
from app.bot import build_bot_pipeline

TEST_CALLER_NUMBER = os.getenv("LOCAL_TEST_PHONE_NUMBER", "+911234567890")


class WebRTCSessionManager:
    """Manages browser WebRTC voice sessions."""

    def __init__(self) -> None:
        self._handler = SmallWebRTCRequestHandler(connection_mode=ConnectionMode.SINGLE)
        self._active_sessions: dict[str, dict[str, Any]] = {}
        self._bot_tasks: set[asyncio.Task] = set()
        self._pipeline_tasks: dict[str, Any] = {}
        self._connections: dict[str, SmallWebRTCConnection] = {}
        self._log_callback: Optional[Callable[[str], Awaitable[None]]] = None
        self._status_callback: Optional[Callable[[], Awaitable[None]]] = None

    def set_callbacks(
        self,
        on_log: Callable[[str], Awaitable[None]],
        on_status: Callable[[], Awaitable[None]],
    ) -> None:
        self._log_callback = on_log
        self._status_callback = on_status

    @property
    def running(self) -> bool:
        return bool(self._pipeline_tasks) or bool(self._handler._pcs_map)

    async def _emit_log(self, line: str) -> None:
        if self._log_callback:
            await self._log_callback(line)

    async def _emit_status(self) -> None:
        if self._status_callback:
            await self._status_callback()

    async def close(self) -> None:
        await self._stop_sessions("Shutting down WebRTC sessions…")

    async def _stop_sessions(self, log_line: str) -> None:
        await self._emit_log(log_line)

        for _pc_id, pipeline_task in list(self._pipeline_tasks.items()):
            try:
                await pipeline_task.cancel()
            except Exception as exc:
                logger.warning(f"Error cancelling pipeline: {exc}")

        for _pc_id, connection in list(self._connections.items()):
            try:
                await connection.disconnect()
            except Exception as exc:
                logger.warning(f"Error disconnecting peer: {exc}")

        for task in list(self._bot_tasks):
            task.cancel()
        self._bot_tasks.clear()

        await self._handler.close()
        self._active_sessions.clear()
        self._pipeline_tasks.clear()
        self._connections.clear()
        await self._emit_status()

    async def start_session(self, request_data: dict) -> dict:
        session_id = str(uuid.uuid4())
        self._active_sessions[session_id] = request_data.get("body", {})
        await self._emit_log(f"WebRTC session created: {session_id}")

        result: dict[str, Any] = {"sessionId": session_id}
        if request_data.get("enableDefaultIceServers"):
            result["iceConfig"] = {
                "iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}],
            }
        return result

    async def handle_offer(
        self,
        request: SmallWebRTCRequest,
        background_tasks: BackgroundTasks | None = None,
        session_id: Optional[str] = None,
    ) -> dict:
        resolved_session_id = session_id or str(uuid.uuid4())
        await self._emit_log("Browser connected — starting voice pipeline…")

        async def webrtc_connection_callback(connection: SmallWebRTCConnection) -> None:
            async def run_bot() -> None:
                try:
                    await self._run_bot(connection, resolved_session_id)
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    logger.exception("WebRTC bot session failed")
                    await self._emit_log(f"Error: {exc}")
                finally:
                    await self._emit_status()

            task = asyncio.create_task(run_bot())
            self._bot_tasks.add(task)
            task.add_done_callback(self._bot_tasks.discard)
            await self._emit_status()

        answer = await self._handler.handle_web_request(
            request=request,
            webrtc_connection_callback=webrtc_connection_callback,
        )
        return answer

    async def handle_patch(self, request: SmallWebRTCPatchRequest) -> None:
        await self._handler.handle_patch_request(request)

    async def stop_all(self) -> None:
        await self._stop_sessions("Stopping WebRTC session…")

    async def _run_bot(self, connection: SmallWebRTCConnection, session_id: str) -> None:
        pc_id = connection.pc_id
        self._connections[pc_id] = connection
        try:
            try:
                await db.init_db_pool()
            except Exception:
                logger.warning(
                    "DB pool not initialized (DATABASE_URL missing/unreachable) — "
                    "order/ticket tools will fail soft."
                )

            transport = SmallWebRTCTransport(
                webrtc_connection=connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5)),
                    vad_audio_passthrough=True,
                ),
            )

            call_sid = f"panel-{session_id[:8]}"
            pipeline_task = await build_bot_pipeline(
                transport, TEST_CALLER_NUMBER, call_sid, source="control_panel"
            )
            self._pipeline_tasks[pc_id] = pipeline_task
            await self._emit_log(
                f"Voice session active ({call_sid}) — Vedonica will greet you first."
            )
            runner = PipelineRunner(handle_sigint=False)
            await runner.run(pipeline_task)
            await self._emit_log("Voice session ended.")
        finally:
            self._pipeline_tasks.pop(pc_id, None)
            self._connections.pop(pc_id, None)
            await self._emit_status()
