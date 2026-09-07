"""
This module builds the exact pipeline from the architecture diagram:

Customer Call -> Telephony (Twilio) -> STT -> Conversation Manager -> LLM
<-> Business Logic/Tools <-> DB / External APIs -> TTS -> customer

STT/LLM/TTS providers are built by app/config.py (multi-provider, with
optional automatic failover) instead of being hardcoded here, so this
file stays the same regardless of which providers you're running.

Pipecat is both the "Conversation Manager" box (turn-taking, interruptions,
context aggregation) and the pipeline runtime that wires every other box
together.
"""

import os

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
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from app import db
from app.config import build_assistant_aggregator_params, build_llm, build_stt, build_tts
from app.prompt_store import get_active_prompt
from app.prompts import build_greeting
from app.tools import TOOLS_SCHEMA, maybe_register_mcp_tools, register_all_tools


async def build_bot_pipeline(
    transport,
    caller_number: str,
    call_sid: str,
    source: str = "twilio",
) -> PipelineTask:
    """Transport-agnostic pipeline build: the STT -> Conversation Manager ->
    LLM/Tools -> TTS chain, plus call logging. Used by both the real Twilio
    flow (run_bot, below) and the browser test console (test_console_bot.py)
    so the two ever run the exact same bot logic — no drift between what you
    test and what customers get.

    `source` is just a dashboard label ('twilio' | 'test_console').
    """

    # ---- STT ---------------------------------------------------------------
    # Provider (Deepgram / Sarvam / Azure / Google / Speechmatics / Groq) is
    # chosen via STT_PROVIDER, with optional automatic failover — see
    # app/config.py.
    stt = build_stt()

    # ---- LLM (+ Business Logic / Tools) ------------------------------------
    llm = build_llm()  # OpenAI / Groq / Google / Azure / OpenRouter / Sarvam
    register_all_tools(llm)  # check_order_status, create_support_ticket, etc.
    await maybe_register_mcp_tools(llm)  # optional: pulls in an MCP tool server

    # ---- TTS ----------------------------------------------------------------
    # Provider (Cartesia / Sarvam / Smallest / ElevenLabs / Azure / Google /
    # Inworld / Camb / Groq) is chosen via TTS_PROVIDER, with optional
    # automatic failover — see app/config.py.
    tts = build_tts()

    # ---- Conversation Manager (context + turn aggregation) ------------------
    # Read fresh (not imported at module load) so a prompt saved from the
    # dashboard's "System prompt" tab applies to the very next call/session
    # without restarting anything — see app/prompt_store.py.
    system_prompt = get_active_prompt()
    context = LLMContext(
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "system",
                "content": f"Caller phone number for this call: {caller_number}. "
                f"Use it automatically for tools that need phone_number — "
                f"don't ask the customer to repeat it.",
            },
        ],
        tools=TOOLS_SCHEMA,
    )
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(user_turn_stop_timeout=0.3),
        # Bounds token usage on long calls — see app/config.py for how this
        # works. Memory stays temporary (this call only, nothing persisted).
        assistant_params=build_assistant_aggregator_params(),
    )

    pipeline = Pipeline(
        [
            transport.input(),               # audio in
            stt,                              # audio -> text
            context_aggregator.user(),        # add user turn to context
            llm,                              # text -> response / tool calls
            tts,                              # text -> audio
            transport.output(),               # audio out
            context_aggregator.assistant(),   # add assistant turn to context
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,   # barge-in: customer can cut the bot off
            enable_metrics=True,        # latency metrics per turn, for tuning
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        logger.info(f"Call connected: {call_sid}")
        await db.start_call(call_sid, caller_number, source=source)
        # First line is scripted (not LLM-generated) so it plays instantly —
        # zero LLM round-trip latency for the very first thing the customer
        # hears. It still gets added to context so the LLM has continuity.
        greeting = build_greeting()
        context.add_message({"role": "assistant", "content": greeting})
        await task.queue_frames([TTSSpeakFrame(greeting)])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info(f"Call ended: {call_sid}")
        await db.end_call(call_sid)
        # Bulk-logged here (call end), not per-turn, so DB writes never sit
        # in the audio loop — see db.log_call_transcript. Feeds the
        # dashboard's call/transcript views.
        await db.log_call_transcript(call_sid, context.messages)
        await task.cancel()

    return task


async def run_bot(websocket_client, stream_sid: str, call_sid: str, caller_number: str) -> None:
    """Wire up and run one Twilio call's pipeline. One instance per active call."""

    await db.init_db_pool()  # no-op if already initialized

    # ---- Telephony -------------------------------------------------------
    # FastAPIWebsocketTransport + TwilioFrameSerializer = the "Telephony |
    # Twilio/SIP" box. SileroVADAnalyzer gives us fast, local voice-activity
    # detection so we know the instant the customer starts/stops talking —
    # this is what makes barge-in (interrupting the bot) feel natural.
    serializer = TwilioFrameSerializer(
        stream_sid=stream_sid,
        call_sid=call_sid,
        account_sid=os.environ["TWILIO_ACCOUNT_SID"],
        auth_token=os.environ["TWILIO_AUTH_TOKEN"],
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket_client,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5)),
            vad_audio_passthrough=True,
            serializer=serializer,
        ),
    )

    task = await build_bot_pipeline(transport, caller_number, call_sid, source="twilio")

    runner = PipelineRunner()
    await runner.run(task)
