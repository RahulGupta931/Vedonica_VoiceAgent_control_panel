"""
Local test entry point — talk to Vedonica through your computer's mic and
speakers, with NO Twilio, NO ngrok, NO webhook, NO WebSocket server.

Same pipeline as app/bot.py (STT -> Conversation Manager -> LLM -> Tools ->
TTS), just swapping the Telephony/Twilio box for Pipecat's LocalAudioTransport
(PyAudio), which reads straight from your default mic and writes straight to
your default speakers.

Setup (one-time):
    pip install "pipecat-ai[local]"     # adds PyAudio for mic/speaker I/O
    # Linux only, PyAudio needs PortAudio's headers to build:
    sudo apt-get install portaudio19-dev
    # macOS:
    brew install portaudio

Run:
    python local_run.py

Then just talk — press Ctrl+C to stop. Put on headphones so the bot's own
voice coming out of your speakers doesn't get picked back up by your mic.
"""

import asyncio
import os

from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

from app import db
from app.config import build_assistant_aggregator_params, build_llm, build_stt, build_tts
from app.prompt_store import get_active_prompt
from app.prompts import build_greeting
from app.tools import TOOLS_SCHEMA, maybe_register_mcp_tools, register_all_tools

load_dotenv(override=True)

# Used only to scope tool calls (check_order_status etc. take a phone number
# argument) — there's no real caller on a local mic test, so this is a
# stand-in. Point it at a row that exists in your `customers`/`orders`
# tables if you want to test the DB-backed tools end to end.
FAKE_CALLER_NUMBER = os.getenv("LOCAL_TEST_PHONE_NUMBER", "+911234567890")


async def main() -> None:
    # DB is optional for a local mic test — only the tools in app/tools.py
    # need it. If DATABASE_URL isn't set or Postgres isn't reachable, we
    # keep going; those specific tool calls will just fail soft (the LLM
    # gets an {"error": ...} back instead of the bot crashing).
    try:
        await db.init_db_pool()
    except Exception:
        logger.warning(
            "DB pool not initialized (DATABASE_URL missing/unreachable) — "
            "order/ticket tools will fail soft. Fine for testing the "
            "conversation flow itself."
        )

    # ---- Telephony -> local mic/speaker -----------------------------------
    IS_RENDER = os.getenv("RENDER") == "true"

    if not IS_RENDER:
        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                vad_analyzer=SileroVADAnalyzer(
                    params=VADParams(stop_secs=0.5)
                ),
                vad_audio_passthrough=True,
            )
        )
    else:
        print("Running on Render - LocalAudio disabled")
        return """  """

    # ---- STT / LLM / TTS (same provider factory as the phone flow) --------
    stt = build_stt()

    llm = build_llm()
    register_all_tools(llm)
    await maybe_register_mcp_tools(llm)

    tts = build_tts()

    # ---- Conversation Manager (context + turn aggregation) -----------------
    # Read fresh from the prompt store (not the app/prompts.py constant
    # directly) so whatever's currently saved in the dashboard's "System
    # prompt" tab is what you hear here too — see app/prompt_store.py.
    context = LLMContext(
        messages=[
            {"role": "system", "content": get_active_prompt()},
            {
                "role": "system",
                "content": f"Caller phone number for this call: {FAKE_CALLER_NUMBER}. "
                f"Use it automatically for tools that need phone_number — "
                f"don't ask the customer to repeat it.",
            },
        ],
        tools=TOOLS_SCHEMA,
    )
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_turn_stop_timeout=0.3),
        assistant_params=build_assistant_aggregator_params(),
    )

    pipeline = Pipeline(
        [
            transport.input(),               # audio in from your mic
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),              # audio out to your speakers
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info(
            "Local audio session started — start talking (Ctrl+C to stop)")
        greeting = build_greeting()
        context.add_message({"role": "assistant", "content": greeting})
        await task.queue_frames([TTSSpeakFrame(greeting)])

    runner = PipelineRunner()
    try:
        await runner.run(task)
    except KeyboardInterrupt:
        pass
    finally:
        await db.close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
