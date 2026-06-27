"""
Phase 2 + 3: Enterprise Revenue Recovery ADK Agent
Phase 3 upgrades:
- init_db() bootstraps Postgres tables on startup
- ping_redis() verifies Redis connection on startup
- get_recent_sessions() / get_session_context() injects past context before each query
- save_session() persists every Q+A pair after each response
- save_revenue_alert() stores detected leakages to Postgres
"""
import asyncio
import logging
from google.genai import types
from google.adk import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from src.config.config import (
    GEMINI_MODEL,
    REVENUE_RECOVERY_SYSTEM_PROMPT,
    STALLED_DEAL_DAYS_THRESHOLD,
    CHURN_RISK_HEALTH_SCORE_THRESHOLD
)
from src.tools.tools import (
    fetch_crm_deals,
    fetch_invoices,
    real_time_market_risk_analysis
)
from src.tools.business_logic import (
    detect_stalled_deals,
    detect_churn_risks,
    detect_overdue_invoices,
    prioritize_revenue_leakages,
    calculate_total_revenue_at_risk
)
from src.memory.database import (
    init_db,
    save_session,
    get_recent_sessions,
    save_revenue_alert,
    get_open_alerts,
    close_engine,
)
from src.memory.cache import (
    ping_redis,
    cache_session_context,
    get_session_context,
    close_redis,
)
from src.security.guardrails import validate_query

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Format system instruction with thresholds
# ─────────────────────────────────────────────
instructions = REVENUE_RECOVERY_SYSTEM_PROMPT.format(
    stalled_days=STALLED_DEAL_DAYS_THRESHOLD,
    health_threshold=CHURN_RISK_HEALTH_SCORE_THRESHOLD
)

# ─────────────────────────────────────────────
# Initialize the ADK Agent
# ─────────────────────────────────────────────
revenue_recovery_agent = Agent(
    name="revenue_recovery_agent",
    model=GEMINI_MODEL,
    instruction=instructions,
    tools=[
        fetch_crm_deals,
        fetch_invoices,
        real_time_market_risk_analysis,
        detect_stalled_deals,
        detect_churn_risks,
        detect_overdue_invoices,
        prioritize_revenue_leakages,
        calculate_total_revenue_at_risk,
    ]
)

# In-memory session service (ADK internal state)
session_service = InMemorySessionService()


# ─────────────────────────────────────────────────────────────────
# CORE: Run one agent turn with full memory read/write
# ─────────────────────────────────────────────────────────────────
async def run_revenue_agent(
    query: str,
    session_id: str = "session_1",
    user_id: str = "user_1"
) -> str:
    """
    Runs one turn of the Revenue Recovery Agent with persistent memory.

    Flow:
      1. Fetch recent conversation history from Redis (fast) → fallback to Postgres
      2. Build context-enriched prompt
      3. Run the ADK agent
      4. Persist the Q+A to Postgres + update Redis context cache
      5. Return the response text

    Args:
        query: The user's natural language instruction.
        session_id: Session identifier for continuity.
        user_id: User identifier.

    Returns:
        The agent's final text response.
    """

    # ── 0. Security guardrail — validate before hitting the LLM ──
    guard = validate_query(query)
    if not guard.is_safe:
        logger.warning(
            f"[SECURITY] Query blocked — category={guard.blocked_category}, "
            f"user={user_id}, session={session_id}"
        )
        return guard.warning_message or "⛔ Your request was blocked by the security guardrail."

    # ── 1. Retrieve recent history ─────────────────────────────
    recent = await get_session_context(session_id)
    if recent is None:
        # Cache miss — fall back to Postgres
        recent = await get_recent_sessions(session_id, limit=5)

    # ── 2. Build context-enriched query ───────────────────────
    if recent:
        history_lines = []
        for turn in recent:
            history_lines.append(f"User: {turn['query']}")
            history_lines.append(f"Agent: {turn['response']}")
        context_block = "\n".join(history_lines)
        enriched_query = (
            f"[Recent conversation history — use for context]\n"
            f"{context_block}\n\n"
            f"[New query]\n{query}"
        )
    else:
        enriched_query = query

    # ── 3. Verify/create ADK session ──────────────────────────
    session = await session_service.get_session(
        app_name="revenue_recovery",
        user_id=user_id,
        session_id=session_id
    )
    if not session:
        session = await session_service.create_session(
            session_id=session_id,
            app_name="revenue_recovery",
            user_id=user_id
        )

    # ── 4. Run the ADK runner ──────────────────────────────────
    runner = Runner(
        agent=revenue_recovery_agent,
        app_name="revenue_recovery",
        session_service=session_service
    )

    content = types.Content(
        role="user",
        parts=[types.Part(text=enriched_query)]
    )

    final_text = ""
    events = runner.run_async(
        session_id=session.id,
        user_id=user_id,
        new_message=content
    )
    async for event in events:
        if event.is_final_response() and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_text += part.text

    # ── 5. Persist to Postgres + update Redis context cache ────
    await save_session(
        session_id=session_id,
        user_id=user_id,
        query=query,
        response=final_text
    )

    # Update the cached context so the next turn is instant
    updated_context = recent + [{"query": query, "response": final_text}]
    updated_context = updated_context[-5:]  # Keep only last 5 turns
    await cache_session_context(session_id, updated_context)

    return final_text


# ─────────────────────────────────────────────────────────────────
# HELPER: Persist detected revenue alerts from a scan
# ─────────────────────────────────────────────────────────────────
async def persist_detected_alerts(alerts: list[dict], alert_type: str) -> None:
    """
    Saves a list of detected revenue alerts to Postgres.

    Args:
        alerts: List of alert dicts (from detect_stalled_deals etc.)
        alert_type: 'stalled_deal' | 'churn_risk' | 'overdue_invoice'
    """
    for alert in alerts:
        customer = alert.get("customer_name") or alert.get("account_name") or "Unknown"
        raw_val = alert.get("deal_value")
        if raw_val is None:
            raw_val = alert.get("amount_due")
        if raw_val is None:
            raw_val = alert.get("monthly_revenue")
        if raw_val is None:
            raw_val = 0.0
        impact = float(raw_val)
        reason = alert.get("reasoning") or alert.get("risk_reason") or "Flagged by autonomous scan"
        action = alert.get("recommended_action") or ""

        await save_revenue_alert(
            alert_type=alert_type,
            customer_name=customer,
            financial_impact=impact,
            reasoning=reason,
            recommended_action=action,
        )


# ─────────────────────────────────────────────────────────────────
# STARTUP: Initialize all connections + DB schema
# ─────────────────────────────────────────────────────────────────
async def startup() -> None:
    """Called once at agent launch. Sets up DB tables and checks Redis."""
    print("\n[Startup] Initializing memory layer...")
    await init_db()
    await ping_redis()
    print("[Startup] Memory layer ready.\n")


# ─────────────────────────────────────────────────────────────────
# SHUTDOWN: Gracefully close all connections
# ─────────────────────────────────────────────────────────────────
async def shutdown() -> None:
    """Called on agent exit. Closes DB pool and Redis connection."""
    await close_engine()
    await close_redis()
    print("\n[Shutdown] Connections closed. Goodbye!")


# ─────────────────────────────────────────────────────────────────
# INTERACTIVE CLI LOOP
# ─────────────────────────────────────────────────────────────────
async def interactive_loop() -> None:
    await startup()

    print("=" * 52)
    print("🤖  Enterprise Revenue Recovery Agent  (Phase 3)")
    print("    Memory: PostgreSQL + Redis enabled")
    print("    Commands: 'alerts' | 'quit'")
    print("=" * 52)

    session_id = "local_session_1"
    user_id = "local_admin"

    while True:
        query = ""
        try:
            query = input("\nUser: ").strip()

            if not query:
                continue

            if query.lower() in ("quit", "exit", "q"):
                break

            # Special command: show open alerts from DB
            if query.lower() == "alerts":
                open_alerts = await get_open_alerts()
                if not open_alerts:
                    print("\n[Memory] No open revenue alerts in database.")
                else:
                    print(f"\n[Memory] 📋 {len(open_alerts)} open alert(s) from Postgres:")
                    for a in open_alerts:
                        print(
                            f"  [{a['alert_type'].upper()}] {a['customer_name']} "
                            f"— ${a['financial_impact']:,.0f} at risk | {a['detected_at']}"
                        )
                continue

            print("\n⏳ Agent is thinking...")
            response = await run_revenue_agent(
                query=query,
                session_id=session_id,
                user_id=user_id
            )
            print(f"\nAgent: {response}")

        except KeyboardInterrupt:
            break
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                # Extract retry delay if present
                import re
                delay_match = re.search(r'retry in (\d+\.?\d*)', err_str, re.IGNORECASE)
                retry_secs = int(float(delay_match.group(1))) + 2 if delay_match else 30

                if "PerDay" in err_str or "per_day" in err_str:
                    print(
                        "\n⚠️  Daily Gemini free-tier quota exhausted (20 requests/day)."
                        "\n    Your quota resets at midnight Pacific Time."
                        "\n    💡 Tip: Enable pay-as-you-go billing at https://aistudio.google.com"
                        "\n         to remove this limit (~$0.15/million tokens)."
                    )
                else:
                    print(
                        f"\n⚠️  Rate limited — too many requests per minute."
                        f"\n    Auto-retrying in {retry_secs} seconds..."
                    )
                    await asyncio.sleep(retry_secs)
                    # Retry the same query automatically
                    try:
                        print("⏳ Retrying...")
                        response = await run_revenue_agent(
                            query=query,
                            session_id=session_id,
                            user_id=user_id
                        )
                        print(f"\nAgent: {response}")
                    except Exception as retry_err:
                        print(f"\n[!] Retry also failed: {retry_err}")
            else:
                print(f"\n[!] Error: {e}")
                logger.exception("Agent loop error")

    await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(interactive_loop())
    except KeyboardInterrupt:
        pass
