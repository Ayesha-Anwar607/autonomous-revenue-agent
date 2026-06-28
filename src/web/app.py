"""
Phase 5: FastAPI Web Backend
Enterprise Revenue Recovery AI Agent — REST API + SSE Event Stream

Endpoints:
  GET  /health              — Health check (DB + Redis ping)
  GET  /api/alerts          — All open revenue alerts from Postgres
  GET  /api/stats           — Dashboard summary KPIs
  GET  /api/sessions        — Recent agent session histories
  POST /api/scan            — Trigger a full agent scan (blocking)
  GET  /api/stream          — SSE live stream of agent scan events
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.config.config import GEMINI_API_KEY
from src.memory.database import (
    close_engine,
    get_open_alerts,
    get_recent_sessions,
    init_db,
    resolve_alert,
    save_revenue_alert,
)
from src.memory.cache import close_redis, ping_redis
from src.security.guardrails import validate_query
from src.tools.business_logic import (
    calculate_total_revenue_at_risk,
    detect_churn_risks,
    detect_overdue_invoices,
    detect_stalled_deals,
    prioritize_revenue_leakages,
)
from src.tools.tools import fetch_crm_deals, fetch_invoices

# ─────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# LIFESPAN — replaces deprecated on_event
# ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap on startup, clean up on shutdown."""
    # ── Startup ──────────────────────────────────────────────────
    logger.info("[API] Starting Enterprise Revenue Recovery API...")
    try:
        await init_db()
        logger.info("[API] ✅ Postgres schema ready.")
    except Exception as e:
        logger.warning(f"[API] ⚠️  Postgres unavailable on startup: {e}")

    redis_ok = await ping_redis()
    if redis_ok:
        logger.info("[API] ✅ Redis connection healthy.")
    else:
        logger.warning("[API] ⚠️  Redis unavailable — caching disabled.")

    yield  # App runs here

    # ── Shutdown ─────────────────────────────────────────────────
    await close_engine()
    await close_redis()
    logger.info("[API] 👋 Server shutting down cleanly.")


# ─────────────────────────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Enterprise Revenue Recovery API",
    description=(
        "Production-grade API for the Autonomous Revenue Recovery AI Agent. "
        "Exposes real-time revenue leakage detection, alert management, "
        "and agent session history."
    ),
    version="5.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Allow frontend (any origin during dev; tighten in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────
# REQUEST / RESPONSE MODELS
# ─────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    session_id: str = "api_session_1"
    user_id: str = "api_user"


class ResolveRequest(BaseModel):
    alert_id: int
    resolved_by: str = "api_user"


class ScanResult(BaseModel):
    session_id: str
    total_revenue_at_risk: float
    stalled_deals_count: int
    churn_risks_count: int
    overdue_invoices_count: int
    top_leakages: list[dict]
    scanned_at: str


# ─────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    """
    Health check endpoint — verifies connectivity to Postgres and Redis.
    Returns 200 if the API is operational.
    """
    postgres_ok = False
    redis_ok = await ping_redis()

    try:
        # A lightweight check: just fetch alerts (empty is fine)
        await get_open_alerts(limit=1)
        postgres_ok = True
    except Exception:
        pass

    status = "healthy" if (postgres_ok and redis_ok) else "degraded"
    return {
        "status": status,
        "api_version": "5.0.0",
        "gemini_configured": bool(GEMINI_API_KEY and "YOUR_" not in (GEMINI_API_KEY or "")),
        "postgres": "connected" if postgres_ok else "unavailable",
        "redis": "connected" if redis_ok else "unavailable",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/alerts", tags=["Revenue Alerts"])
async def get_alerts(limit: int = 50):
    """
    Retrieve all open revenue alerts from Postgres, sorted by financial impact.

    Returns a list of unresolved revenue leakage alerts with full details.
    """
    try:
        alerts = await get_open_alerts(limit=limit)
        total_at_risk = sum(a.get("financial_impact", 0) for a in alerts)
        return {
            "count": len(alerts),
            "total_revenue_at_risk": total_at_risk,
            "alerts": alerts,
        }
    except Exception as e:
        logger.error(f"[API] Failed to fetch alerts: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/api/stats", tags=["Dashboard"])
async def get_stats():
    """
    Dashboard summary KPIs — total revenue at risk, alert breakdown by type,
    and latest scan metadata. Suitable for populating dashboard header cards.
    """
    try:
        alerts = await get_open_alerts(limit=200)
        total_at_risk = sum(a.get("financial_impact", 0) for a in alerts)

        # Breakdown by category
        by_type: dict[str, dict] = {}
        for alert in alerts:
            t = alert.get("alert_type", "unknown")
            if t not in by_type:
                by_type[t] = {"count": 0, "total_impact": 0.0}
            by_type[t]["count"] += 1
            by_type[t]["total_impact"] += alert.get("financial_impact", 0)

        return {
            "total_open_alerts": len(alerts),
            "total_revenue_at_risk": total_at_risk,
            "breakdown_by_type": by_type,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"[API] Failed to compute stats: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/api/sessions", tags=["Session History"])
async def get_sessions(session_id: str = "api_session_1", limit: int = 20):
    """
    Retrieve recent agent conversation history for a given session.
    Useful for displaying the interaction log in the dashboard.
    """
    try:
        history = await get_recent_sessions(session_id=session_id, limit=limit)
        return {
            "session_id": session_id,
            "count": len(history),
            "history": history,
        }
    except Exception as e:
        logger.error(f"[API] Failed to fetch sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.post("/api/scan", response_model=ScanResult, tags=["Revenue Scan"])
async def run_scan(request: ScanRequest):
    """
    Trigger a full autonomous revenue scan.

    Runs all detection tools (stalled deals, churn risks, overdue invoices)
    and saves detected alerts to Postgres. Returns a structured scan summary.

    Note: Does not call the Gemini LLM — runs the business logic engine directly
    so this endpoint works even when the API quota is exhausted.
    """
    logger.info(f"[API] Scan triggered by session={request.session_id}, user={request.user_id}")

    try:
        # 1. Pull mock data from all operational streams
        deals = fetch_crm_deals()
        invoices = fetch_invoices()

        # 2. Run all detectors
        stalled = detect_stalled_deals(deals)
        churn = detect_churn_risks(deals)
        overdue = detect_overdue_invoices(invoices)

        # 3. Merge and prioritize
        all_leakages = prioritize_revenue_leakages(stalled, churn, overdue)
        risk_summary = calculate_total_revenue_at_risk(all_leakages)
        total_at_risk = risk_summary["grand_total_at_risk"]

        # 4. Persist each detected alert to Postgres
        persisted_count = 0
        for item in all_leakages:
            try:
                await save_revenue_alert(
                    alert_type=item.get("alert_type", "unknown"),
                    customer_name=item.get("customer_name", "Unknown"),
                    financial_impact=float(item.get("financial_impact", 0)),
                    reasoning=item.get("reasoning", ""),
                    recommended_action="; ".join(item.get("recommended_actions", [])),
                )
                persisted_count += 1
            except Exception as db_err:
                logger.warning(f"[API] Could not persist alert: {db_err}")

        logger.info(
            f"[API] Scan complete — {len(all_leakages)} leakages found, "
            f"{persisted_count} persisted. Total at risk: ${total_at_risk:,.0f}"
        )

        return ScanResult(
            session_id=request.session_id,
            total_revenue_at_risk=total_at_risk,
            stalled_deals_count=len(stalled),
            churn_risks_count=len(churn),
            overdue_invoices_count=len(overdue),
            top_leakages=all_leakages[:10],  # Return top 10 in response
            scanned_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as e:
        logger.error(f"[API] Scan failed: {e}")
        raise HTTPException(status_code=500, detail=f"Scan error: {str(e)}")


@app.get("/api/stream", tags=["Live Stream"])
async def stream_scan(session_id: str = "stream_session"):
    """
    Server-Sent Events (SSE) endpoint — streams real-time revenue scan events.

    Connect from JavaScript using EventSource('/api/stream') to receive
    live detection events as they are processed. Each event is a JSON payload.
    """
    async def event_generator() -> AsyncGenerator[str, None]:
        scan_id = str(uuid.uuid4())[:8]

        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        # Stream: scan started
        yield sse("scan_started", {
            "scan_id": scan_id,
            "session_id": session_id,
            "message": "🚀 Revenue recovery scan started",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await asyncio.sleep(0.3)

        # Stream: loading CRM data
        yield sse("progress", {
            "step": "crm_load",
            "message": "📊 Loading CRM deal pipeline...",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await asyncio.sleep(0.5)
        deals = fetch_crm_deals()
        yield sse("data_loaded", {
            "source": "crm",
            "count": len(deals),
            "message": f"✅ Loaded {len(deals)} CRM deal records",
        })
        await asyncio.sleep(0.3)

        # Stream: loading invoice data
        yield sse("progress", {
            "step": "invoice_load",
            "message": "🧾 Loading invoice ledger...",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await asyncio.sleep(0.5)
        invoices = fetch_invoices()
        yield sse("data_loaded", {
            "source": "invoices",
            "count": len(invoices),
            "message": f"✅ Loaded {len(invoices)} invoice records",
        })
        await asyncio.sleep(0.3)

        # Stream: running detectors
        for detector_name, detector_fn, data_arg in [
            ("Stalled Deal Detector", detect_stalled_deals, deals),
            ("Churn Risk Detector",   detect_churn_risks,   deals),
            ("Overdue Invoice Detector", detect_overdue_invoices, invoices),
        ]:
            yield sse("progress", {
                "step": "detection",
                "message": f"🔍 Running {detector_name}...",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            await asyncio.sleep(0.5)
            findings = detector_fn(data_arg)
            yield sse("detection_result", {
                "detector": detector_name,
                "findings_count": len(findings),
                "findings": findings[:5],  # Stream first 5 findings
                "message": f"⚠️  {len(findings)} issue(s) detected by {detector_name}",
            })
            await asyncio.sleep(0.3)

        # Stream: prioritize
        yield sse("progress", {
            "step": "prioritize",
            "message": "📈 Prioritizing by financial impact...",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await asyncio.sleep(0.5)
        stalled_f = detect_stalled_deals(deals)
        churn_f   = detect_churn_risks(deals)
        overdue_f = detect_overdue_invoices(invoices)
        top = prioritize_revenue_leakages(stalled_f, churn_f, overdue_f)
        risk_summary = calculate_total_revenue_at_risk(top)
        total = risk_summary["grand_total_at_risk"]

        # Stream: scan complete
        yield sse("scan_complete", {
            "scan_id": scan_id,
            "total_leakages": len(top),
            "total_revenue_at_risk": total,
            "top_3": top[:3],
            "message": f"✅ Scan complete — ${total:,.0f} total revenue at risk",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/alerts/{alert_id}/resolve", tags=["Revenue Alerts"])
async def resolve_revenue_alert(alert_id: int, request: ResolveRequest):
    """
    Mark a revenue alert as resolved (HITL approval action).
    Updates the status in Postgres and records the resolution timestamp.
    """
    try:
        success = await resolve_alert(alert_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found or already resolved.")
        logger.info(f"[API] Alert {alert_id} resolved by {request.resolved_by}")
        return {
            "success": True,
            "alert_id": alert_id,
            "resolved_by": request.resolved_by,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
            "message": f"✅ Alert #{alert_id} successfully resolved.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[API] Failed to resolve alert {alert_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.post("/api/query", tags=["Agent Query"])
async def query_guardrail_check(request: Request):
    """
    Security guardrail pre-check endpoint.
    Validates a user query before sending it to the agent.
    Returns is_safe=True/False so the frontend can display rejection reason.
    """
    body = await request.json()
    query = body.get("query", "")
    result = validate_query(query)
    return {
        "is_safe": result.is_safe,
        "blocked_category": result.blocked_category,
        "warning_message": result.warning_message,
        "sanitized_query": result.sanitized_query,
    }
