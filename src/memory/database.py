"""
Phase 3: Async PostgreSQL Memory Layer
Handles all persistent read/write operations for the agent's long-term memory.
Uses SQLAlchemy async core for non-blocking DB I/O (plays well with asyncio).
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncConnection

from src.config.config import POSTGRES_URL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine — One singleton async engine for the whole application lifetime.
# ---------------------------------------------------------------------------
_engine: Optional[AsyncEngine] = None


def get_engine() -> AsyncEngine:
    """Returns (or lazily creates) the shared async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            POSTGRES_URL,
            echo=False,          # Set True to log every SQL statement for debugging
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,  # Reconnect automatically on stale connections
        )
    return _engine


# ---------------------------------------------------------------------------
# INIT — Create tables from schema.sql on first startup
# ---------------------------------------------------------------------------
async def init_db() -> None:
    """
    Bootstraps the database schema. Safe to call every startup —
    uses CREATE TABLE IF NOT EXISTS so it's idempotent.
    """
    schema_path = "db/schema.sql"
    try:
        with open(schema_path, "r") as f:
            schema_sql = f.read()

        engine = get_engine()
        # Use a raw asyncpg connection to execute the full schema in one shot
        async with engine.connect() as conn:
            # Get the underlying asyncpg connection and run the raw SQL
            raw_conn = await conn.get_raw_connection()
            driver_conn = getattr(raw_conn, "driver_connection", None)
            if driver_conn is not None:
                await driver_conn.execute(schema_sql)
            else:
                raise RuntimeError("Could not retrieve raw driver connection")
            await conn.commit()

        logger.info("[DB] Schema initialized successfully.")
        print("[DB] ✅ Database schema ready.")

    except FileNotFoundError:
        logger.error(f"[DB] Schema file not found: {schema_path}")
        raise
    except Exception as e:
        logger.error(f"[DB] Failed to initialize schema: {e}")
        raise


# ---------------------------------------------------------------------------
# SESSIONS — Conversational memory
# ---------------------------------------------------------------------------
async def save_session(
    session_id: str,
    user_id: str,
    query: str,
    response: str
) -> None:
    """
    Persists a single query + agent response pair to the sessions table.
    Called after every agent response to build the conversation history.
    """
    sql = text("""
        INSERT INTO sessions (session_id, user_id, query, response, created_at)
        VALUES (:session_id, :user_id, :query, :response, :created_at)
    """)
    try:
        async with get_engine().begin() as conn:
            await conn.execute(sql, {
                "session_id": session_id,
                "user_id": user_id,
                "query": query,
                "response": response,
                "created_at": datetime.now(timezone.utc),
            })
        logger.debug(f"[DB] Session saved: {session_id}")
    except Exception as e:
        logger.error(f"[DB] Failed to save session: {e}")


async def get_recent_sessions(
    session_id: str,
    limit: int = 5
) -> list[dict]:
    """
    Retrieves the N most recent query/response pairs for a session.
    Used to inject recent conversation history into the agent's context.

    Args:
        session_id: The current session to look up.
        limit: How many past turns to retrieve (default: 5).

    Returns:
        List of dicts with keys: query, response, created_at
    """
    sql = text("""
        SELECT query, response, created_at
        FROM sessions
        WHERE session_id = :session_id
        ORDER BY created_at DESC
        LIMIT :limit
    """)
    try:
        async with get_engine().connect() as conn:
            result = await conn.execute(sql, {"session_id": session_id, "limit": limit})
            rows = result.fetchall()
            # Reverse so chronological order (oldest first)
            return [
                {"query": r.query, "response": r.response, "created_at": r.created_at}
                for r in reversed(rows)
            ]
    except Exception as e:
        logger.error(f"[DB] Failed to retrieve sessions: {e}")
        return []


# ---------------------------------------------------------------------------
# REVENUE ALERTS — Persistent leakage tracking
# ---------------------------------------------------------------------------
async def save_revenue_alert(
    alert_type: str,
    customer_name: str,
    financial_impact: float,
    reasoning: str,
    recommended_action: str = ""
) -> Optional[int]:
    """
    Persists a detected revenue alert to the revenue_alerts table.

    Args:
        alert_type: One of 'stalled_deal', 'churn_risk', 'overdue_invoice'.
        customer_name: Name of the at-risk customer/account.
        financial_impact: Dollar amount at risk.
        reasoning: Explainable rationale from the agent.
        recommended_action: Suggested recovery action.

    Returns:
        The new alert's database ID, or None on failure.
    """
    sql = text("""
        INSERT INTO revenue_alerts
            (alert_type, customer_name, financial_impact, reasoning, recommended_action, detected_at)
        VALUES
            (:alert_type, :customer_name, :financial_impact, :reasoning, :recommended_action, :detected_at)
        RETURNING id
    """)
    try:
        async with get_engine().begin() as conn:
            result = await conn.execute(sql, {
                "alert_type": alert_type,
                "customer_name": customer_name,
                "financial_impact": financial_impact,
                "reasoning": reasoning,
                "recommended_action": recommended_action,
                "detected_at": datetime.now(timezone.utc),
            })
            alert_id = result.scalar()
            logger.info(f"[DB] Alert saved: ID={alert_id}, type={alert_type}, customer={customer_name}")
            return alert_id
    except Exception as e:
        logger.error(f"[DB] Failed to save revenue alert: {e}")
        return None


async def get_open_alerts(limit: int = 20) -> list[dict]:
    """
    Retrieves all open (unresolved) revenue alerts, sorted by financial impact (highest first).

    Args:
        limit: Max number of alerts to return.

    Returns:
        List of alert dicts.
    """
    sql = text("""
        SELECT id, alert_type, customer_name, financial_impact,
               reasoning, recommended_action, detected_at
        FROM revenue_alerts
        WHERE status = 'open'
        ORDER BY financial_impact DESC
        LIMIT :limit
    """)
    try:
        async with get_engine().connect() as conn:
            result = await conn.execute(sql, {"limit": limit})
            rows = result.fetchall()
            return [
                {
                    "id": r.id,
                    "alert_type": r.alert_type,
                    "customer_name": r.customer_name,
                    "financial_impact": float(r.financial_impact),
                    "reasoning": r.reasoning,
                    "recommended_action": r.recommended_action,
                    "detected_at": r.detected_at.isoformat(),
                }
                for r in rows
            ]
    except Exception as e:
        logger.error(f"[DB] Failed to fetch open alerts: {e}")
        return []


async def resolve_alert(alert_id: int) -> bool:
    """
    Marks a revenue alert as resolved.

    Args:
        alert_id: The DB ID of the alert to resolve.

    Returns:
        True if successfully resolved, False otherwise.
    """
    sql = text("""
        UPDATE revenue_alerts
        SET status = 'resolved', resolved_at = :resolved_at
        WHERE id = :id
    """)
    try:
        async with get_engine().begin() as conn:
            await conn.execute(sql, {
                "id": alert_id,
                "resolved_at": datetime.now(timezone.utc),
            })
        logger.info(f"[DB] Alert ID={alert_id} marked as resolved.")
        return True
    except Exception as e:
        logger.error(f"[DB] Failed to resolve alert {alert_id}: {e}")
        return False


async def close_engine() -> None:
    """Gracefully disposes the DB connection pool on shutdown."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
        logger.info("[DB] Connection pool closed.")
