"""
Phase 2: MCP (Model Context Protocol) Server
Exposes the revenue recovery business logic tools as a structured MCP
server using FastMCP. This allows any MCP-compatible agent or client to
discover and call our tools via a standard JSON-RPC protocol.
"""
import json
from mcp.server.fastmcp import FastMCP
from src.tools.tools import fetch_crm_deals, fetch_invoices, real_time_market_risk_analysis
from src.tools.business_logic import (
    detect_stalled_deals,
    detect_churn_risks,
    detect_overdue_invoices,
    prioritize_revenue_leakages,
    calculate_total_revenue_at_risk
)

# Initialize FastMCP server
mcp = FastMCP(
    name="RevenueRecoveryMCP",
    instructions=(
        "Enterprise Revenue Recovery MCP Server. Exposes structured tools "
        "for detecting stalled deals, churn risks, and overdue invoices from "
        "operational enterprise streams."
    )
)


# ─────────────────────────────────────────────────────────────
# MCP TOOL DEFINITIONS
# ─────────────────────────────────────────────────────────────

@mcp.tool()
def mcp_fetch_crm_deals(limit: int = 10) -> str:
    """
    Fetch real-time CRM sales pipeline data.
    Returns a JSON string containing deal records with stage, value, and health scores.
    """
    deals = fetch_crm_deals(limit=limit)
    return json.dumps(deals, indent=2)


@mcp.tool()
def mcp_fetch_invoices(limit: int = 10) -> str:
    """
    Fetch accounts receivable invoice status records.
    Returns a JSON string with invoice IDs, amounts, statuses, and overdue days.
    """
    invoices = fetch_invoices(limit=limit)
    return json.dumps(invoices, indent=2)


@mcp.tool()
def mcp_market_risk_analysis(query: str) -> str:
    """
    Perform a real-time market/competitor risk analysis search.
    Returns a text summary of external risk signals for a given query.
    """
    return real_time_market_risk_analysis(query=query)


@mcp.tool()
def mcp_detect_stalled_deals(limit: int = 10) -> str:
    """
    Run stalled deal detection on the current CRM pipeline.
    Returns a JSON list of deals that have exceeded the inactivity threshold, sorted by financial impact.
    """
    deals = fetch_crm_deals(limit=limit)
    alerts = detect_stalled_deals(deals)
    return json.dumps(alerts, indent=2)


@mcp.tool()
def mcp_detect_churn_risks(limit: int = 10) -> str:
    """
    Identify customer accounts at high churn risk from the CRM pipeline.
    Returns a JSON list of accounts with low health scores and their estimated revenue at risk.
    """
    deals = fetch_crm_deals(limit=limit)
    alerts = detect_churn_risks(deals)
    return json.dumps(alerts, indent=2)


@mcp.tool()
def mcp_detect_overdue_invoices(limit: int = 10) -> str:
    """
    Surface overdue and unpaid invoices from the accounts receivable system.
    Returns a JSON list of invoices ranked by overdue amount and urgency.
    """
    invoices = fetch_invoices(limit=limit)
    alerts = detect_overdue_invoices(invoices)
    return json.dumps(alerts, indent=2)


@mcp.tool()
def mcp_full_revenue_recovery_scan(limit: int = 10) -> str:
    """
    Run a FULL autonomous revenue recovery scan across all operational streams.
    Detects stalled deals, churn risks, and overdue invoices simultaneously.
    Returns a unified priority-ranked JSON report with a financial risk summary.
    """
    deals = fetch_crm_deals(limit=limit)
    invoices = fetch_invoices(limit=limit)

    stalled = detect_stalled_deals(deals)
    churn = detect_churn_risks(deals)
    overdue = detect_overdue_invoices(invoices)

    ranked = prioritize_revenue_leakages(stalled, churn, overdue)
    summary = calculate_total_revenue_at_risk(ranked)

    return json.dumps({
        "summary": summary,
        "prioritized_alerts": ranked
    }, indent=2)


# ─────────────────────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[MCP Server] Starting Revenue Recovery MCP Server via stdio transport...")
    mcp.run(transport="stdio")
