"""
Provider factory for STT / LLM / TTS, covering every extra you listed:

    cartesia, deepgram, openai, elevenlabs, groq, google, azure, sarvam,
    soundfile, silero, webrtc, speechmatics, openrouter, camb, mcp,
    inworld, smallest

Why this file exists (the actual "better performance" lever):
1. Picking the *right* provider per box matters more than code — e.g.
   Sarvam and Smallest are purpose-built for Indian languages/Hinglish
   and beat generic providers on both latency and pronunciation there.
2. Wiring automatic failover (ServiceSwitcher / LLMSwitcher with
   ServiceSwitcherStrategyFailover) means a slow/erroring provider
   doesn't take the whole call down — it fails over mid-call.
3. Everything is chosen via env vars so you can A/B two providers in
   production without touching code.

`silero` -> VAD (already used in bot.py, not a swappable "box").
`soundfile` -> transitive dependency some services use for audio
   encoding; nothing to wire here.
`webrtc` -> see README "Local testing over WebRTC" for a browser-based
   dev loop that's much faster to iterate on than dialing a real phone
   number for every latency tweak.
`mcp` -> see app/tools.py `maybe_register_mcp_tools`.

Set these in .env:
    STT_PROVIDER=deepgram          # deepgram | sarvam | azure | google | speechmatics | groq
    STT_FAILOVER_PROVIDER=sarvam   # optional, same choices, blank = no failover
    LLM_PROVIDER=openai            # openai | groq | google | azure | openrouter | sarvam
    LLM_FAILOVER_PROVIDER=groq     # optional
    TTS_PROVIDER=cartesia          # cartesia | sarvam | elevenlabs | azure | google | smallest | inworld | camb | groq
    TTS_FAILOVER_PROVIDER=sarvam   # optional
"""

import os
from typing import Optional

from loguru import logger

from pipecat.pipeline.llm_switcher import LLMSwitcher
from pipecat.pipeline.service_switcher import ServiceSwitcher, ServiceSwitcherStrategyFailover


# --------------------------------------------------------------------------
# STT
# --------------------------------------------------------------------------

def _build_single_stt(provider: str):
    provider = provider.lower()

    if provider == "deepgram":
        from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions

        return DeepgramSTTService(
            api_key=os.environ["DEEPGRAM_API_KEY"],
            live_options=LiveOptions(
                model="nova-3",
                language="hi",  # Hindi/English code-switching
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                interim_results=True,
                smart_format=True,
                punctuate=True,
                endpointing=250,
            ),
        )

    if provider == "sarvam":
        # Purpose-built for Indian languages — best raw accuracy on
        # Hinglish of anything in this list. saaras:v3 supports a
        # "codemix" mode specifically for Hindi/English mixed speech.
        from pipecat.services.sarvam.stt import SarvamSTTService

        return SarvamSTTService(
            api_key=os.environ["SARVAM_API_KEY"],
            mode="codemix",
            settings=SarvamSTTService.Settings(
                model="saaras:v3",
                vad_signals=True,
                high_vad_sensitivity=True,
            ),
        )

    if provider == "azure":
        from pipecat.services.azure.stt import AzureSTTService

        return AzureSTTService(
            api_key=os.environ["AZURE_SPEECH_API_KEY"],
            region=os.environ["AZURE_SPEECH_REGION"],
            language="hi-IN",
        )

    if provider == "google":
        from pipecat.services.google.stt import GoogleSTTService

        return GoogleSTTService(
            credentials=os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
            language="hi-IN",
        )

    if provider == "speechmatics":
        from pipecat.services.speechmatics.stt import SpeechmaticsSTTService

        return SpeechmaticsSTTService(api_key=os.environ["SPEECHMATICS_API_KEY"])

    if provider == "groq":
        # Groq-hosted Whisper — very fast batch/segment transcription.
        from pipecat.services.groq.stt import GroqSTTService

        return GroqSTTService(api_key=os.environ["GROQ_API_KEY"])

    raise ValueError(f"Unknown STT provider: {provider}")


def build_stt():
    primary = os.getenv("STT_PROVIDER", "deepgram")
    failover = os.getenv("STT_FAILOVER_PROVIDER", "").strip()

    stt = _build_single_stt(primary)
    if not failover:
        return stt

    logger.info(f"STT failover enabled: {primary} -> {failover}")
    backup = _build_single_stt(failover)
    return ServiceSwitcher(services=[stt, backup], strategy_type=ServiceSwitcherStrategyFailover)


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------

def _build_single_llm(provider: str):
    provider = provider.lower()

    if provider == "openai":
        from pipecat.services.openai.llm import OpenAILLMService

        return OpenAILLMService(
            api_key=os.environ["OPENAI_API_KEY"],
            model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            params=OpenAILLMService.InputParams(temperature=0.4),
        )

    if provider == "groq":
        # Groq's inference speed (hundreds of tokens/sec) makes it the
        # fastest option here for time-to-first-token — good primary
        # *or* good low-latency failover for OpenAI.
        from pipecat.services.groq.llm import GroqLLMService

        return GroqLLMService(
            api_key=os.environ["GROQ_API_KEY"],
            model=os.getenv("GROQ_LLM_MODEL", "llama-3.3-70b-versatile"),
        )

    if provider == "google":
        from pipecat.services.google.llm import GoogleLLMService

        return GoogleLLMService(
            api_key=os.environ["GOOGLE_API_KEY"],
            model=os.getenv("GOOGLE_LLM_MODEL", "gemini-2.0-flash"),
        )

    if provider == "azure":
        from pipecat.services.azure.llm import AzureLLMService

        return AzureLLMService(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        )

    if provider == "openrouter":
        from pipecat.services.openrouter.llm import OpenRouterLLMService

        return OpenRouterLLMService(
            api_key=os.environ["OPENROUTER_API_KEY"],
            settings=OpenRouterLLMService.Settings(
                model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            ),
        )

    if provider == "sarvam":
        # OpenAI-compatible; strongest at natural code-mixed Hindi/English
        # generation specifically (not just understanding it).
        from pipecat.services.sarvam.llm import SarvamLLMService

        return SarvamLLMService(api_key=os.environ["SARVAM_API_KEY"])

    raise ValueError(f"Unknown LLM provider: {provider}")


def build_llm():
    """Returns either a single LLM service or an LLMSwitcher with
    failover. Both expose the same .register_function() interface used
    in app/tools.py, so callers don't need to care which one they got."""
    primary = os.getenv("LLM_PROVIDER", "openai")
    failover = os.getenv("LLM_FAILOVER_PROVIDER", "").strip()

    llm = _build_single_llm(primary)
    if not failover:
        return llm

    logger.info(f"LLM failover enabled: {primary} -> {failover}")
    backup = _build_single_llm(failover)
    return LLMSwitcher(llms=[llm, backup], strategy_type=ServiceSwitcherStrategyFailover)


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------

def _build_single_tts(provider: str):
    provider = provider.lower()

    if provider == "cartesia":
        from pipecat.services.cartesia.tts import CartesiaTTSService

        return CartesiaTTSService(
            api_key=os.environ["CARTESIA_API_KEY"],
            voice_id=os.environ["CARTESIA_VOICE_ID"],
            model="sonic-3.5",
            params=CartesiaTTSService.InputParams(language="hi"),
        )

    if provider == "sarvam":
        # Best-in-class Hindi/Hinglish voice quality + pronunciation;
        # a strong pick as either primary or the failover for Cartesia.
        from pipecat.services.sarvam.tts import SarvamTTSService

        return SarvamTTSService(
            api_key=os.environ["SARVAM_API_KEY"],
            settings=SarvamTTSService.Settings(
                model="bulbul:v2",
                voice=os.getenv("SARVAM_VOICE", "anushka"),
                language="hi-IN",
            ),
        )

    if provider == "smallest":
        # Smallest's Lightning model advertises ~64ms first-audio-byte —
        # worth benchmarking against Cartesia for your traffic.
        from pipecat.services.smallest.tts import SmallestTTSService

        return SmallestTTSService(
            api_key=os.environ["SMALLEST_API_KEY"],
            settings=SmallestTTSService.Settings(voice=os.getenv("SMALLEST_VOICE", "sophia")),
        )

    if provider == "elevenlabs":
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

        return ElevenLabsTTSService(
            api_key=os.environ["ELEVENLABS_API_KEY"],
            voice_id=os.environ["ELEVENLABS_VOICE_ID"],
        )

    if provider == "azure":
        from pipecat.services.azure.tts import AzureTTSService

        return AzureTTSService(
            api_key=os.environ["AZURE_SPEECH_API_KEY"],
            region=os.environ["AZURE_SPEECH_REGION"],
            voice=os.getenv("AZURE_TTS_VOICE", "hi-IN-SwaraNeural"),
        )

    if provider == "google":
        from pipecat.services.google.tts import GoogleTTSService

        return GoogleTTSService(
            credentials=os.environ["GOOGLE_APPLICATION_CREDENTIALS"],
            voice_id=os.getenv("GOOGLE_TTS_VOICE", "hi-IN-Wavenet-D"),
        )

    if provider == "inworld":
        from pipecat.services.inworld.tts import InworldTTSService

        return InworldTTSService(
            api_key=os.environ["INWORLD_API_KEY"],
            voice_id=os.getenv("INWORLD_VOICE", "Ashley"),
        )

    if provider == "camb":
        from pipecat.services.camb.tts import CambTTSService

        return CambTTSService(api_key=os.environ["CAMB_API_KEY"])

    if provider == "groq":
        from pipecat.services.groq.tts import GroqTTSService

        return GroqTTSService(api_key=os.environ["GROQ_API_KEY"])

    raise ValueError(f"Unknown TTS provider: {provider}")


def build_tts():
    primary = os.getenv("TTS_PROVIDER", "cartesia")
    failover = os.getenv("TTS_FAILOVER_PROVIDER", "").strip()

    tts = _build_single_tts(primary)
    if not failover:
        return tts

    logger.info(f"TTS failover enabled: {primary} -> {failover}")
    backup = _build_single_tts(failover)
    return ServiceSwitcher(services=[tts, backup], strategy_type=ServiceSwitcherStrategyFailover)


# --------------------------------------------------------------------------
# Context memory (temporary, in-call only) with token-usage capping
# --------------------------------------------------------------------------
#
# Why the terminal shows the whole conversation being re-sent every turn:
# that's inherent to how chat-completion APIs work — they're stateless, so
# "memory" only exists because we resend the transcript each time. There's
# no way to have the LLM remember without sending history. What we CAN
# control is how much history we send once a call runs long.
#
# This turns on Pipecat's built-in auto context summarization: once the
# conversation crosses a token/message threshold, older turns get
# collapsed into one short LLM-written summary (system prompt + summary +
# most recent messages), instead of the raw transcript growing forever.
# Nothing here is written to disk or any DB — it's still purely in-memory
# for the lifetime of this one call, which is exactly "remember during the
# call, forget after" — just capped so a long call doesn't get expensive.

def build_assistant_aggregator_params():
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMAssistantAggregatorParams,
    )
    from pipecat.utils.context.llm_context_summarization import (
        LLMAutoContextSummarizationConfig,
        LLMContextSummaryConfig,
    )

    return LLMAssistantAggregatorParams(
        enable_auto_context_summarization=True,
        auto_context_summarization_config=LLMAutoContextSummarizationConfig(
            # Whichever threshold hits first triggers a summarization pass.
            max_context_tokens=int(os.getenv("CONTEXT_MAX_TOKENS", "3000")),
            max_unsummarized_messages=int(os.getenv("CONTEXT_MAX_MESSAGES", "12")),
            summary_config=LLMContextSummaryConfig(
                # Cap on the summary the LLM writes to replace old turns.
                target_context_tokens=int(os.getenv("CONTEXT_SUMMARY_TOKENS", "600")),
                # Always keep this many of the most recent messages verbatim
                # (uncompressed) so the last couple of exchanges stay exact.
                min_messages_after_summary=int(
                    os.getenv("CONTEXT_MIN_RECENT_MESSAGES", "4")
                ),
            ),
        ),
    )
