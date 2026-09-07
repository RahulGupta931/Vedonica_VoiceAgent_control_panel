"""
REST API backing the dashboard (see dashboard/index.html).

Mostly read-only: it reads from Postgres (app/db.py), the in-code knowledge
base (app/knowledge_data.py), or plain env vars. Every DB-backed endpoint
fails soft to an empty result if DATABASE_URL isn't configured, so the
dashboard is still browsable before you've set up Postgres (e.g. while just
wiring up UI locally).

The one exception is /prompt (GET/POST/POST reset): it *does* change what
the bot does on the next call — see app/prompt_store.py for how that's
persisted and picked up.
"""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app import knowledge_data as kb_data
from app import prompt_store

router = APIRouter()


class PromptUpdate(BaseModel):
    prompt: str


@router.get("/overview")
async def overview():
    stats = await db.get_overview_stats()
    return {"db_configured": db.db_configured(), **stats}


@router.get("/calls")
async def calls(limit: int = 50):
    return {"calls": await db.list_calls(limit=limit)}


@router.get("/calls/{call_sid}/transcript")
async def call_transcript(call_sid: str):
    return {"call_sid": call_sid, "turns": await db.get_call_transcript(call_sid)}


@router.get("/tickets")
async def tickets(limit: int = 100):
    return {"tickets": await db.list_tickets(limit=limit)}


@router.get("/orders")
async def orders(limit: int = 100):
    return {"orders": await db.list_orders(limit=limit)}


@router.get("/customers")
async def customers(limit: int = 100):
    return {"customers": await db.list_customers(limit=limit)}


@router.get("/knowledge")
async def knowledge():
    """Read-only view of app/knowledge_data.py. To edit products, policies,
    or FAQs, edit that file directly and restart the bot — see its
    module docstring for why it's plain Python data rather than DB rows."""
    return {
        "about_us": kb_data.ABOUT_US,
        "products": kb_data.PRODUCTS,
        "policies": kb_data.POLICIES,
        "faqs": kb_data.FAQS,
    }


@router.get("/config")
async def config():
    """Active provider/config summary — provider *names* and non-secret
    tuning knobs only, never API keys."""
    return {
        "stt": {
            "provider": os.getenv("STT_PROVIDER", "deepgram"),
            "failover": os.getenv("STT_FAILOVER_PROVIDER", "") or None,
        },
        "llm": {
            "provider": os.getenv("LLM_PROVIDER", "openai"),
            "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
            "failover": os.getenv("LLM_FAILOVER_PROVIDER", "") or None,
        },
        "tts": {
            "provider": os.getenv("TTS_PROVIDER", "cartesia"),
            "failover": os.getenv("TTS_FAILOVER_PROVIDER", "") or None,
        },
        "context_summarization": {
            "max_tokens": int(os.getenv("CONTEXT_MAX_TOKENS", "3000")),
            "max_messages": int(os.getenv("CONTEXT_MAX_MESSAGES", "12")),
            "summary_tokens": int(os.getenv("CONTEXT_SUMMARY_TOKENS", "600")),
            "min_recent_messages": int(os.getenv("CONTEXT_MIN_RECENT_MESSAGES", "4")),
        },
        "mcp_enabled": bool(os.getenv("MCP_SERVER_URL", "").strip()),
    }


@router.get("/prompt")
async def get_prompt():
    """Current system prompt (custom override if one's saved, else the
    built-in default from app/prompts.py), plus enough metadata for the
    dashboard's editor to show a diff/reset affordance."""
    return prompt_store.get_status()


@router.post("/prompt")
async def update_prompt(body: PromptUpdate):
    """Save a new system prompt. Takes effect on the *next* call or test
    console session — see app/prompt_store.get_active_prompt / app/bot.py."""
    try:
        return prompt_store.set_active_prompt(body.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/prompt/reset")
async def reset_prompt():
    """Discard the custom override and go back to app/prompts.py's default."""
    return prompt_store.reset_prompt()
