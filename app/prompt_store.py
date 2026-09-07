"""
Lets the system prompt be edited from the dashboard instead of only by
editing app/prompts.py and restarting.

Storage is a single JSON file on local disk (PROMPT_OVERRIDE_PATH, default
data/system_prompt_override.json) rather than a DB row, on purpose: the
Test Console and the dashboard always run in the same process/filesystem
as each other (see README's "Local testing" notes), so this works whether
or not DATABASE_URL is configured — same fail-soft spirit as app/db.py.

app/bot.py calls get_active_prompt() fresh inside build_bot_pipeline() for
*every* call/session, not once at import time — so saving a new prompt
here takes effect on the next call (real or test-console) without
restarting the server or test console process. Calls already in progress
keep the prompt they started with.
"""

import json
import os
import time
from typing import Optional

from loguru import logger

from app.prompts import VEDONICA_SYSTEM_PROMPT

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "system_prompt_override.json"
)
_PATH = os.getenv("PROMPT_OVERRIDE_PATH", _DEFAULT_PATH)


def get_default_prompt() -> str:
    """The prompt baked into app/prompts.py — used as the fallback and as
    what "Reset to default" restores."""
    return VEDONICA_SYSTEM_PROMPT


def _read_override() -> Optional[dict]:
    if not os.path.isfile(_PATH):
        return None
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("prompt"), str) and data["prompt"].strip():
            return data
    except Exception:
        logger.exception(f"Failed to read prompt override at {_PATH} (falling back to default)")
    return None


def get_active_prompt() -> str:
    """What the bot should actually use right now: the saved override if
    one exists and is non-empty, else the built-in default."""
    override = _read_override()
    return override["prompt"] if override else get_default_prompt()


def get_status() -> dict:
    """Everything the dashboard's prompt editor needs in one shot."""
    override = _read_override()
    return {
        "prompt": override["prompt"] if override else get_default_prompt(),
        "default_prompt": get_default_prompt(),
        "is_custom": override is not None,
        "updated_at": override.get("updated_at") if override else None,
    }


def set_active_prompt(prompt: str) -> dict:
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Prompt can't be empty")
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    payload = {"prompt": prompt, "updated_at": time.time()}
    tmp_path = f"{_PATH}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, _PATH)  # atomic swap, no half-written file on crash
    logger.info("System prompt updated from dashboard")
    return get_status()


def reset_prompt() -> dict:
    if os.path.isfile(_PATH):
        os.remove(_PATH)
        logger.info("System prompt reset to default")
    return get_status()
