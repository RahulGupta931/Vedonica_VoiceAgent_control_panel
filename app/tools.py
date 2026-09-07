"""
"Business Logic | Tools" box from the architecture diagram.

Each tool:
- talks to Postgres (db.py) and/or External APIs (CRM/Calendar) via httpx
- returns a small, LLM-friendly dict (never raw HTML/XML, never huge blobs)
- fails soft: on error it returns {"error": "..."} instead of raising, so
  the LLM can say something sensible instead of the call going silent

Register these with the LLM service in bot.py via `register_all_tools`.
"""

import os
from typing import Any

import httpx
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from app import db
from app import knowledge_base as kb

CRM_BASE_URL = os.getenv("CRM_BASE_URL", "")
CRM_API_KEY = os.getenv("CRM_API_KEY", "")
CALENDAR_BASE_URL = os.getenv("CALENDAR_BASE_URL", "")
CALENDAR_API_KEY = os.getenv("CALENDAR_API_KEY", "")

_http = httpx.AsyncClient(timeout=4.0)  # tight timeout: a voice call can't wait


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------

async def check_order_status(params: FunctionCallParams) -> None:
    order_id = params.arguments.get("order_id")
    phone_number = params.arguments.get("phone_number")
    try:
        order = await db.get_order_status(order_id, phone_number)
        if not order:
            await params.result_callback(
                {"found": False, "message": "Order not found for this number."}
            )
            return
        await params.result_callback({"found": True, **order})
    except Exception:
        logger.exception("check_order_status failed")
        await params.result_callback({"error": "order_lookup_failed"})


async def create_support_ticket(params: FunctionCallParams) -> None:
    phone_number = params.arguments.get("phone_number")
    summary = params.arguments.get("summary")
    priority = params.arguments.get("priority", "normal")
    try:
        ticket_id = await db.create_support_ticket(phone_number, summary, priority)
        await params.result_callback({"ticket_id": ticket_id, "status": "created"})
    except Exception:
        logger.exception("create_support_ticket failed")
        await params.result_callback({"error": "ticket_creation_failed"})


async def get_crm_customer_profile(params: FunctionCallParams) -> None:
    """GET to the external CRM (the 'External APIs | CRM/Calendar' box)."""
    phone_number = params.arguments.get("phone_number")
    try:
        resp = await _http.get(
            f"{CRM_BASE_URL}/v1/customers",
            params={"phone": phone_number},
            headers={"Authorization": f"Bearer {CRM_API_KEY}"},
        )
        resp.raise_for_status()
        data = resp.json()
        await params.result_callback(
            {
                "found": bool(data),
                "tier": data.get("tier"),
                "open_tickets": data.get("open_tickets", 0),
            }
        )
    except Exception:
        logger.exception("get_crm_customer_profile failed")
        await params.result_callback({"error": "crm_lookup_failed"})


async def schedule_callback(params: FunctionCallParams) -> None:
    """POST to the external Calendar API to book a human follow-up call."""
    phone_number = params.arguments.get("phone_number")
    preferred_time_iso = params.arguments.get("preferred_time_iso")
    reason = params.arguments.get("reason")
    try:
        resp = await _http.post(
            f"{CALENDAR_BASE_URL}/v1/callbacks",
            json={
                "phone": phone_number,
                "time": preferred_time_iso,
                "reason": reason,
            },
            headers={"Authorization": f"Bearer {CALENDAR_API_KEY}"},
        )
        resp.raise_for_status()
        booking = resp.json()
        await params.result_callback(
            {"booked": True, "confirmation_id": booking.get("id")}
        )
    except Exception:
        logger.exception("schedule_callback failed")
        await params.result_callback({"error": "callback_scheduling_failed"})


async def search_knowledge_base(params: FunctionCallParams) -> None:
    """Look up product info / policies / T&Cs / About Us / FAQ on demand,
    instead of carrying all of it in the system prompt on every turn. See
    app/knowledge_base.py for why."""
    query = params.arguments.get("query", "")
    try:
        results = kb.search(query, top_k=2)
        if not results:
            await params.result_callback(
                {
                    "found": False,
                    "message": "No matching info in knowledge base for this query.",
                }
            )
            return
        await params.result_callback({"found": True, "results": results})
    except Exception:
        logger.exception("search_knowledge_base failed")
        await params.result_callback({"error": "knowledge_base_search_failed"})


async def escalate_to_human(params: FunctionCallParams) -> None:
    phone_number = params.arguments.get("phone_number")
    reason = params.arguments.get("reason", "customer requested a human agent")
    try:
        ticket_id = await db.create_support_ticket(
            phone_number, f"ESCALATION: {reason}", priority="high"
        )
        await params.result_callback(
            {"escalated": True, "ticket_id": ticket_id}
        )
    except Exception:
        logger.exception("escalate_to_human failed")
        await params.result_callback({"error": "escalation_failed"})


# --------------------------------------------------------------------------
# Schemas (what the LLM sees) — kept small and specific for accuracy + speed
# --------------------------------------------------------------------------

_check_order_status_schema = FunctionSchema(
    name="check_order_status",
    description=(
        "Look up a customer's order status, ETA and amount. Call this "
        "before saying anything about an order — never guess."
    ),
    properties={
        "order_id": {"type": "string", "description": "The order ID the customer gave."},
        "phone_number": {
            "type": "string",
            "description": "Caller's phone number in E.164 format, used to verify ownership.",
        },
    },
    required=["order_id", "phone_number"],
)

_create_support_ticket_schema = FunctionSchema(
    name="create_support_ticket",
    description="File a support ticket for an issue that needs follow-up.",
    properties={
        "phone_number": {"type": "string", "description": "Caller's phone number."},
        "summary": {"type": "string", "description": "One-line summary of the issue."},
        "priority": {
            "type": "string",
            "enum": ["low", "normal", "high"],
            "description": "Urgency of the ticket.",
        },
    },
    required=["phone_number", "summary"],
)

_get_crm_customer_profile_schema = FunctionSchema(
    name="get_crm_customer_profile",
    description="Fetch the caller's CRM profile (tier, open tickets) by phone number.",
    properties={
        "phone_number": {"type": "string", "description": "Caller's phone number."},
    },
    required=["phone_number"],
)

_schedule_callback_schema = FunctionSchema(
    name="schedule_callback",
    description="Book a callback slot on the calendar for a human agent to call the customer back.",
    properties={
        "phone_number": {"type": "string", "description": "Caller's phone number."},
        "preferred_time_iso": {
            "type": "string",
            "description": "Preferred callback time, ISO 8601.",
        },
        "reason": {"type": "string", "description": "Why the callback is needed."},
    },
    required=["phone_number", "preferred_time_iso", "reason"],
)

_search_knowledge_base_schema = FunctionSchema(
    name="search_knowledge_base",
    description=(
        "Look up product details (price, benefits, ingredients), shipping/"
        "returns/refund policy, terms and conditions, About Us, or FAQ "
        "answers. ALWAYS call this before answering any question about "
        "products, pricing, policies, or the company — never answer these "
        "from memory, you don't have them memorized."
    ),
    properties={
        "query": {
            "type": "string",
            "description": (
                "A few keywords for what the customer is asking, e.g. "
                "'collagen tablet price', 'return policy', 'about vedonica'."
            ),
        },
    },
    required=["query"],
)

_escalate_to_human_schema = FunctionSchema(
    name="escalate_to_human",
    description=(
        "Escalate the call to a human specialist. Use when the customer "
        "explicitly asks for a human, or the issue is outside support scope, "
        "or the customer is very frustrated."
    ),
    properties={
        "phone_number": {"type": "string", "description": "Caller's phone number."},
        "reason": {"type": "string", "description": "Why this needs a human."},
    },
    required=["phone_number", "reason"],
)

TOOLS_SCHEMA = ToolsSchema(
    standard_tools=[
        _check_order_status_schema,
        _create_support_ticket_schema,
        _get_crm_customer_profile_schema,
        _schedule_callback_schema,
        _search_knowledge_base_schema,
        _escalate_to_human_schema,
    ]
)

_HANDLERS: dict[str, Any] = {
    "check_order_status": check_order_status,
    "create_support_ticket": create_support_ticket,
    "get_crm_customer_profile": get_crm_customer_profile,
    "schedule_callback": schedule_callback,
    "search_knowledge_base": search_knowledge_base,
    "escalate_to_human": escalate_to_human,
}


def register_all_tools(llm) -> None:
    """Wire every tool handler onto the LLM service instance. Works whether
    `llm` is a single LLMService or an LLMSwitcher — both expose the same
    register_function() interface."""
    for name, handler in _HANDLERS.items():
        llm.register_function(name, handler)


# --------------------------------------------------------------------------
# Optional: MCP (Model Context Protocol) tools
# --------------------------------------------------------------------------
# If you have an MCP server exposing extra tools (internal wikis, ticketing
# systems, other company APIs), point MCP_SERVER_URL at it and its tools
# get merged in automatically alongside the ones defined above — no code
# changes needed per tool.

async def maybe_register_mcp_tools(llm) -> None:
    mcp_url = os.getenv("MCP_SERVER_URL", "").strip()
    if not mcp_url:
        return

    from mcp.client.session_group import StreamableHttpParameters
    from pipecat.services.mcp_service import MCPClient

    logger.info(f"Connecting to MCP server: {mcp_url}")
    mcp_client = MCPClient(
        server_params=StreamableHttpParameters(url=mcp_url),
    )
    # Registers every tool the MCP server exposes directly onto the LLM,
    # using the same FunctionCallParams-based interface as the tools above.
    mcp_tools_schema = await mcp_client.register_tools(llm)
    logger.info(f"Registered {len(mcp_tools_schema.standard_tools)} MCP tool(s)")
