"""
Browser test console for Vedonica — talk to the bot from a browser tab
(mic in, speaker out) instead of dialing a real phone number.

This is the "Test Console" in the dashboard. It runs the *exact* same
pipeline as the real phone flow (see app/bot.py: build_bot_pipeline) — same
STT/LLM/TTS provider config, same prompt, same tools, same knowledge base —
so a conversation here behaves identically to a real call. Only the
transport differs (WebRTC in a browser tab vs. Twilio Media Streams).

This uses Pipecat's built-in development runner, which is explicitly a
LOCAL DEV TOOL, not a production server: it accepts unauthenticated
requests and has no rate limiting. Keep it off the public internet — run it
on your machine or an internal network, same as you would `local_run.py`.

Setup (one-time):
    pip install "pipecat-ai[runner]"   # already in requirements.txt

Run (the runner defaults to port 7860, same as server.py — use 7861 so you
can run the real server and the test console side by side):
    python test_console_bot.py -t webrtc --port 7861
    # then open http://localhost:7861/client in a browser, allow mic
    # access, and start talking.

The dashboard's "Test Console" tab just links to that URL — it doesn't
proxy the audio itself (WebRTC needs a direct browser<->process
connection).
"""

import os

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.runner.types import RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from app import db
from app.bot import build_bot_pipeline

load_dotenv(override=True)

# Same stand-in used by local_run.py: DB-backed tools (check_order_status
# etc.) need *a* phone number to scope queries by. Point it at a row that
# exists in your `customers`/`orders` tables to test those tools for real.
TEST_CALLER_NUMBER = os.getenv("LOCAL_TEST_PHONE_NUMBER", "+911234567890")


async def bot(runner_args: RunnerArguments) -> None:
    """Entry point the Pipecat dev runner calls once per browser session."""

    # DB is optional here too — see local_run.py's note. Order/ticket tools
    # just fail soft ({"error": ...}) if it's not configured.
    try:
        await db.init_db_pool()
    except Exception:
        logger.warning(
            "DB pool not initialized (DATABASE_URL missing/unreachable) — "
            "order/ticket tools will fail soft in the test console."
        )

    if not isinstance(runner_args, SmallWebRTCRunnerArguments):
        raise RuntimeError(
            f"test_console_bot.py only supports the WebRTC transport, got "
            f"{type(runner_args).__name__}. Run with: python test_console_bot.py -t webrtc"
        )

    transport = SmallWebRTCTransport(
        webrtc_connection=runner_args.webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5)),
            vad_audio_passthrough=True,
        ),
    )

    call_sid = f"test-{runner_args.session_id}"
    task = await build_bot_pipeline(
        transport, TEST_CALLER_NUMBER, call_sid, source="test_console"
    )

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
