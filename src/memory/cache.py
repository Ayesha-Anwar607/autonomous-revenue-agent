"""
Phase 3: Redis Cache Layer
Provides fast short-term caching for revenue scan results.
Avoids re-running expensive business logic for repeated queries
within a short window (TTL: 10 minutes by default).
"""
import json
import logging
from typing import Optional, Any

import redis.asyncio as aioredis

from src.config.config import REDIS_URL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client — One singleton async Redis client
# ---------------------------------------------------------------------------
_redis_client: Optional[aioredis.Redis] = None

# Default TTL: 10 minutes (revenue data changes infrequently)
DEFAULT_TTL_SECONDS = 600


def get_redis() -> aioredis.Redis:
    """Returns (or lazily creates) the shared async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------
async def ping_redis() -> bool:
    """
    Checks whether Redis is reachable. Safe to call at startup.

    Returns:
        True if Redis responds, False if not available.
    """
    try:
        result = await get_redis().ping()
        if result:
            print("[Cache] ✅ Redis is connected.")
        return result
    except Exception as e:
        logger.warning(f"[Cache] Redis not reachable: {e}. Continuing without cache.")
        return False


# ---------------------------------------------------------------------------
# Revenue scan result cache
# ---------------------------------------------------------------------------
async def cache_scan_result(
    scan_key: str,
    data: Any,
    ttl: int = DEFAULT_TTL_SECONDS
) -> None:
    """
    Stores a revenue scan result in Redis under the given key.

    Args:
        scan_key: A unique string key (e.g. 'stalled_deals', 'churn_risks').
        data: Any JSON-serializable data (list, dict, etc.).
        ttl: Time-to-live in seconds before the cache entry expires.
    """
    try:
        serialized = json.dumps(data, default=str)
        await get_redis().set(scan_key, serialized, ex=ttl)
        logger.debug(f"[Cache] Cached '{scan_key}' (TTL={ttl}s)")
    except Exception as e:
        logger.warning(f"[Cache] Failed to cache '{scan_key}': {e}")


async def get_cached_scan(scan_key: str) -> Optional[Any]:
    """
    Retrieves a cached revenue scan result.

    Args:
        scan_key: The key used when the result was cached.

    Returns:
        The deserialized data if the key exists and hasn't expired, else None.
    """
    try:
        raw = await get_redis().get(scan_key)
        if raw is None:
            logger.debug(f"[Cache] Cache miss for '{scan_key}'")
            return None
        logger.debug(f"[Cache] Cache hit for '{scan_key}'")
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Cache] Failed to read cache for '{scan_key}': {e}")
        return None


async def invalidate_cache(scan_key: str) -> None:
    """
    Force-clears a cache entry so the next scan fetches fresh data.

    Args:
        scan_key: The key to delete.
    """
    try:
        await get_redis().delete(scan_key)
        logger.info(f"[Cache] Invalidated cache key: '{scan_key}'")
    except Exception as e:
        logger.warning(f"[Cache] Failed to invalidate '{scan_key}': {e}")


async def invalidate_all_scans() -> None:
    """Clears all known revenue scan cache entries at once."""
    keys = ["stalled_deals", "churn_risks", "overdue_invoices", "revenue_summary"]
    for key in keys:
        await invalidate_cache(key)
    logger.info("[Cache] All revenue scan caches cleared.")


# ---------------------------------------------------------------------------
# Session state cache (fast context window retrieval)
# ---------------------------------------------------------------------------
async def cache_session_context(session_id: str, context: list[dict], ttl: int = 3600) -> None:
    """
    Caches a session's recent conversation context for ultra-fast retrieval.
    Falls back to DB if this cache misses.

    Args:
        session_id: Unique session identifier.
        context: List of recent {query, response} dicts.
        ttl: Cache lifetime in seconds (default: 1 hour).
    """
    key = f"ctx:{session_id}"
    await cache_scan_result(key, context, ttl)


async def get_session_context(session_id: str) -> Optional[list[dict]]:
    """
    Retrieves the cached session context (if still fresh).

    Args:
        session_id: The session to look up.

    Returns:
        List of recent {query, response} dicts, or None on cache miss.
    """
    return await get_cached_scan(f"ctx:{session_id}")


async def close_redis() -> None:
    """Gracefully closes the Redis connection on shutdown."""
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("[Cache] Redis connection closed.")
