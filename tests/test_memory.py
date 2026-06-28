"""
Phase 3: Memory Layer Tests
Tests for Postgres DB and Redis cache — uses real local connections
(requires docker compose up -d to be running).
Skipped gracefully if DB/Redis is unavailable.
"""
import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

# ── Try to import memory modules (skip all if SQLAlchemy not installed) ──
pytest.importorskip("sqlalchemy")
pytest.importorskip("asyncpg")
pytest.importorskip("redis")

from src.memory.database import (
    init_db,
    save_session,
    get_recent_sessions,
    save_revenue_alert,
    get_open_alerts,
    resolve_alert,
    close_engine,
    get_engine,
)
from src.memory.cache import (
    ping_redis,
    cache_scan_result,
    get_cached_scan,
    invalidate_cache,
    cache_session_context,
    get_session_context,
    close_redis,
)


# ─────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
async def ensure_db_schema():
    """
    Session-scoped fixture: runs init_db() once before all tests so
    that the sessions and revenue_alerts tables exist.
    Skipped silently if Postgres isn't running.
    """
    try:
        await init_db()
    except Exception:
        pass  # DB not reachable — individual tests will skip themselves
    yield
    await close_engine()


@pytest.fixture(autouse=True)
async def cleanup_connections():
    """
    Function-scoped fixture: resets DB and Redis client singletons between tests.
    This prevents 'Event loop is closed' or 'attached to a different loop' errors
    caused by event loop replacement across tests.
    """
    import src.memory.cache as cache
    import src.memory.database as database
    # Reset before test
    cache._redis_client = None
    database._engine = None
    yield
    # Close and reset after test
    try:
        await cache.close_redis()
    except Exception:
        pass
    try:
        await database.close_engine()
    except Exception:
        pass
    cache._redis_client = None
    database._engine = None


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def unique_session() -> str:
    """Generates a unique session_id so tests don't conflict with each other."""
    return f"test_session_{uuid.uuid4().hex[:8]}"


async def _db_reachable() -> bool:
    """Returns True if Postgres is reachable."""
    try:
        engine = get_engine()
        from sqlalchemy import text
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────
# POSTGRES TESTS
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_init():
    """Schema bootstraps without error."""
    if not await _db_reachable():
        pytest.skip("Postgres not running — start with: docker compose up -d")
    # Should be idempotent — calling twice should not raise
    await init_db()
    await init_db()


@pytest.mark.asyncio
async def test_save_and_retrieve_session():
    """A saved session can be retrieved in order."""
    if not await _db_reachable():
        pytest.skip("Postgres not running — start with: docker compose up -d")

    session_id = unique_session()
    await save_session(session_id, "test_user", "What are stalled deals?", "Here are the stalled deals...")
    await save_session(session_id, "test_user", "What about churn risks?", "Here are the churn risks...")

    history = await get_recent_sessions(session_id, limit=5)

    assert len(history) == 2
    assert history[0]["query"] == "What are stalled deals?"
    assert history[1]["query"] == "What about churn risks?"
    assert "response" in history[0]
    assert "created_at" in history[0]


@pytest.mark.asyncio
async def test_session_limit():
    """get_recent_sessions respects the limit parameter."""
    if not await _db_reachable():
        pytest.skip("Postgres not running — start with: docker compose up -d")

    session_id = unique_session()
    for i in range(7):
        await save_session(session_id, "test_user", f"Query {i}", f"Response {i}")

    history = await get_recent_sessions(session_id, limit=3)
    assert len(history) == 3


@pytest.mark.asyncio
async def test_save_revenue_alert():
    """A revenue alert is saved and appears in open alerts."""
    if not await _db_reachable():
        pytest.skip("Postgres not running — start with: docker compose up -d")

    alert_id = await save_revenue_alert(
        alert_type="stalled_deal",
        customer_name="Acme Corp Test",
        financial_impact=125000.00,
        reasoning="Deal idle for 45 days in Proposal stage.",
        recommended_action="Schedule executive call."
    )

    assert alert_id is not None
    assert isinstance(alert_id, int)

    # Should appear in open alerts
    alerts = await get_open_alerts(limit=50)
    ids = [a["id"] for a in alerts]
    assert alert_id in ids


@pytest.mark.asyncio
async def test_resolve_alert():
    """A resolved alert no longer appears in open alerts."""
    if not await _db_reachable():
        pytest.skip("Postgres not running — start with: docker compose up -d")

    alert_id = await save_revenue_alert(
        alert_type="overdue_invoice",
        customer_name="Beta Inc Test",
        financial_impact=8500.00,
        reasoning="Invoice INV-999 overdue by 40 days.",
    )
    assert alert_id is not None

    success = await resolve_alert(alert_id)
    assert success is True

    # Should no longer be in open alerts
    alerts = await get_open_alerts(limit=100)
    ids = [a["id"] for a in alerts]
    assert alert_id not in ids


@pytest.mark.asyncio
async def test_open_alerts_sorted_by_impact():
    """Open alerts are returned highest financial impact first."""
    if not await _db_reachable():
        pytest.skip("Postgres not running — start with: docker compose up -d")

    session_prefix = unique_session()
    id_low = await save_revenue_alert("churn_risk", f"Low Corp {session_prefix}", 1000.0, "Low risk")
    id_high = await save_revenue_alert("churn_risk", f"High Corp {session_prefix}", 99000.0, "High risk")

    alerts = await get_open_alerts(limit=100)
    impacts = [a["financial_impact"] for a in alerts]

    # Verify descending order
    assert impacts == sorted(impacts, reverse=True)


# ─────────────────────────────────────────────────────────────────
# REDIS CACHE TESTS
# ─────────────────────────────────────────────────────────────────

async def _redis_reachable() -> bool:
    """Returns True if Redis is reachable."""
    try:
        return await ping_redis()
    except Exception:
        return False


@pytest.mark.asyncio
async def test_redis_ping():
    """Redis responds to ping."""
    if not await _redis_reachable():
        pytest.skip("Redis not running — start with: docker compose up -d")
    result = await ping_redis()
    assert result is True


@pytest.mark.asyncio
async def test_cache_set_and_get():
    """A cached value is retrievable before TTL expires."""
    if not await _redis_reachable():
        pytest.skip("Redis not running — start with: docker compose up -d")

    key = f"test_scan_{uuid.uuid4().hex[:6]}"
    data = [{"customer": "Acme", "risk": "high"}, {"customer": "Beta", "risk": "medium"}]

    await cache_scan_result(key, data, ttl=30)
    result = await get_cached_scan(key)

    assert result is not None
    assert len(result) == 2
    assert result[0]["customer"] == "Acme"


@pytest.mark.asyncio
async def test_cache_miss_returns_none():
    """A non-existent cache key returns None."""
    if not await _redis_reachable():
        pytest.skip("Redis not running — start with: docker compose up -d")

    result = await get_cached_scan("this_key_does_not_exist_xyz_123")
    assert result is None


@pytest.mark.asyncio
async def test_cache_invalidation():
    """An invalidated cache key returns None on next get."""
    if not await _redis_reachable():
        pytest.skip("Redis not running — start with: docker compose up -d")

    key = f"test_invalidate_{uuid.uuid4().hex[:6]}"
    await cache_scan_result(key, {"test": True}, ttl=60)

    # Confirm it exists
    assert await get_cached_scan(key) is not None

    # Invalidate it
    await invalidate_cache(key)

    # Should now be gone
    assert await get_cached_scan(key) is None


@pytest.mark.asyncio
async def test_session_context_cache():
    """Session context is cached and retrieved correctly."""
    if not await _redis_reachable():
        pytest.skip("Redis not running — start with: docker compose up -d")

    session_id = f"ctx_test_{uuid.uuid4().hex[:6]}"
    context = [
        {"query": "Find stalled deals", "response": "Found 3 stalled deals."},
        {"query": "What's the total risk?", "response": "$45,000 at risk."},
    ]

    await cache_session_context(session_id, context, ttl=60)
    retrieved = await get_session_context(session_id)

    assert retrieved is not None
    assert len(retrieved) == 2
    assert retrieved[0]["query"] == "Find stalled deals"
    assert retrieved[1]["response"] == "$45,000 at risk."


# ─────────────────────────────────────────────────────────────────
# UNIT TESTS (mocked — no DB/Redis required)
# ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_session_handles_db_error_gracefully():
    """save_session does not raise even if the DB call fails."""
    with patch("src.memory.database.get_engine") as mock_engine:
        mock_conn = AsyncMock()
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        mock_conn.execute = AsyncMock(side_effect=Exception("DB connection refused"))
        mock_engine.return_value.begin = AsyncMock(return_value=mock_conn)

        # Should not raise
        await save_session("s1", "u1", "test query", "test response")


@pytest.mark.asyncio
async def test_cache_handles_redis_error_gracefully():
    """cache_scan_result does not raise even if Redis is unavailable."""
    with patch("src.memory.cache.get_redis") as mock_redis:
        mock_r = AsyncMock()
        mock_r.setex = AsyncMock(side_effect=Exception("Redis connection refused"))
        mock_redis.return_value = mock_r

        # Should not raise
        await cache_scan_result("some_key", {"data": 1}, ttl=30)
