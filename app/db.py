"""
Postgres access layer (the "Database (PostgreSQL)" box in the architecture
diagram). One asyncpg pool shared across all calls, created once at process
startup and reused by every tool call so we don't pay connection-setup
latency mid-conversation.
"""

import os
from typing import Any, Optional

import asyncpg
from loguru import logger

_pool: Optional[asyncpg.Pool] = None


async def init_db_pool() -> None:
    global _pool
    if _pool is not None:
        return
    dsn = os.environ["DATABASE_URL"]
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=2,
        max_size=10,
        command_timeout=5,  # fail fast so the bot never goes silent
    )
    logger.info("Postgres pool ready")


async def close_db_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_db_pool() first")
    return _pool


async def get_order_status(order_id: str, phone_number: str) -> Optional[dict[str, Any]]:
    """Look up an order, scoped to the caller's phone number for safety."""
    pool = _require_pool()
    row = await pool.fetchrow(
        """
        SELECT order_id, status, eta_date, total_amount, item_summary
        FROM orders
        WHERE order_id = $1 AND customer_phone = $2
        """,
        order_id,
        phone_number,
    )
    return dict(row) if row else None


async def get_customer_by_phone(phone_number: str) -> Optional[dict[str, Any]]:
    pool = _require_pool()
    row = await pool.fetchrow(
        "SELECT customer_id, full_name, tier FROM customers WHERE phone = $1",
        phone_number,
    )
    return dict(row) if row else None


async def create_support_ticket(
    phone_number: str,
    summary: str,
    priority: str = "normal",
) -> str:
    pool = _require_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO support_tickets (customer_phone, summary, priority, status, created_at)
        VALUES ($1, $2, $3, 'open', now())
        RETURNING ticket_id
        """,
        phone_number,
        summary,
        priority,
    )
    return str(row["ticket_id"])


async def log_call_transcript_turn(call_sid: str, role: str, text: str) -> None:
    """Fire-and-forget style logging for QA / analytics. Never blocks the
    conversation on failure."""
    pool = _require_pool()
    try:
        await pool.execute(
            """
            INSERT INTO call_transcripts (call_sid, role, text, created_at)
            VALUES ($1, $2, $3, now())
            """,
            call_sid,
            role,
            text,
        )
    except Exception:
        logger.exception("Failed to log transcript turn (non-fatal)")


# --------------------------------------------------------------------------
# Call lifecycle + dashboard reads
#
# These all fail soft (log + return) rather than raising, on purpose: they
# run from event handlers in app/bot.py (call connect/disconnect) where an
# exception must never take the call down, and from app/api.py (dashboard)
# where the DB may simply not be configured for local UI-only testing.
# --------------------------------------------------------------------------

def db_configured() -> bool:
    return _pool is not None


async def start_call(call_sid: str, caller_number: str, source: str = "twilio") -> None:
    if _pool is None:
        return
    try:
        await _pool.execute(
            """
            INSERT INTO calls (call_sid, caller_number, source, started_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (call_sid) DO NOTHING
            """,
            call_sid,
            caller_number,
            source,
        )
    except Exception:
        logger.exception("Failed to record call start (non-fatal)")


async def end_call(call_sid: str) -> None:
    if _pool is None:
        return
    try:
        await _pool.execute(
            "UPDATE calls SET ended_at = now() WHERE call_sid = $1 AND ended_at IS NULL",
            call_sid,
        )
    except Exception:
        logger.exception("Failed to record call end (non-fatal)")


async def log_call_transcript(call_sid: str, messages: list[dict[str, Any]]) -> None:
    """Bulk-insert a whole call's turns once the call ends (see the
    on_client_disconnected handler in app/bot.py). Deliberately NOT called
    per-turn mid-call, so DB latency never sits in the audio loop."""
    if _pool is None:
        return
    rows = [
        (call_sid, m["role"], m["content"])
        for m in messages
        if m.get("role") in ("user", "assistant")
        and isinstance(m.get("content"), str)
        and m["content"].strip()
    ]
    if not rows:
        return
    try:
        await _pool.executemany(
            """
            INSERT INTO call_transcripts (call_sid, role, text, created_at)
            VALUES ($1, $2, $3, now())
            """,
            rows,
        )
    except Exception:
        logger.exception("Failed to log call transcript (non-fatal)")


async def list_calls(limit: int = 50) -> list[dict[str, Any]]:
    if _pool is None:
        return []
    rows = await _pool.fetch(
        """
        SELECT c.call_sid, c.caller_number, c.source, c.started_at, c.ended_at,
               COUNT(t.id) AS turn_count
        FROM calls c
        LEFT JOIN call_transcripts t ON t.call_sid = c.call_sid
        GROUP BY c.call_sid, c.caller_number, c.source, c.started_at, c.ended_at
        ORDER BY c.started_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def get_call_transcript(call_sid: str) -> list[dict[str, Any]]:
    if _pool is None:
        return []
    rows = await _pool.fetch(
        "SELECT role, text, created_at FROM call_transcripts "
        "WHERE call_sid = $1 ORDER BY id ASC",
        call_sid,
    )
    return [dict(r) for r in rows]


async def list_tickets(limit: int = 100) -> list[dict[str, Any]]:
    if _pool is None:
        return []
    rows = await _pool.fetch(
        "SELECT ticket_id, customer_phone, summary, priority, status, created_at "
        "FROM support_tickets ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def list_orders(limit: int = 100) -> list[dict[str, Any]]:
    if _pool is None:
        return []
    rows = await _pool.fetch(
        "SELECT order_id, customer_phone, status, eta_date, total_amount, item_summary, created_at "
        "FROM orders ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def list_customers(limit: int = 100) -> list[dict[str, Any]]:
    if _pool is None:
        return []
    rows = await _pool.fetch(
        "SELECT customer_id, phone, full_name, tier, created_at "
        "FROM customers ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return [dict(r) for r in rows]


async def get_overview_stats() -> dict[str, Any]:
    if _pool is None:
        return {
            "calls_24h": 0,
            "calls_total": 0,
            "open_tickets": 0,
            "high_priority_open": 0,
            "orders_total": 0,
            "customers_total": 0,
        }
    row = await _pool.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM calls WHERE started_at >= now() - interval '24 hours') AS calls_24h,
            (SELECT COUNT(*) FROM calls) AS calls_total,
            (SELECT COUNT(*) FROM support_tickets WHERE status = 'open') AS open_tickets,
            (SELECT COUNT(*) FROM support_tickets WHERE priority = 'high' AND status = 'open') AS high_priority_open,
            (SELECT COUNT(*) FROM orders) AS orders_total,
            (SELECT COUNT(*) FROM customers) AS customers_total
        """
    )
    return dict(row) if row else {}
