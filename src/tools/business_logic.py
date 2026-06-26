"""
Phase 2: Business Logic Engine
Revenue leakage calculation, scoring, and prioritization functions.
These are the core revenue intelligence tools exposed to the ADK agent.
"""
from datetime import datetime, timezone
from config.config import (
    STALLED_DEAL_DAYS_THRESHOLD,
    CHURN_RISK_HEALTH_SCORE_THRESHOLD,
    OVERDUE_INVOICE_DAYS_THRESHOLD
)


# ─────────────────────────────────────────────────────────────
# 1. REVENUE LEAKAGE DETECTION FUNCTIONS
# ─────────────────────────────────────────────────────────────

def detect_stalled_deals(deals: list[dict]) -> list[dict]:
    """
    Scans CRM deal records and flags deals that have been inactive beyond
    the configured stall threshold.

    Args:
        deals: List of deal records from the CRM feed.

    Returns:
        A prioritized list of stalled deal alerts sorted by financial impact (desc).
    """
    alerts = []
    for deal in deals:
        days_idle = deal.get("last_activity_days_ago", 0)
        if days_idle > STALLED_DEAL_DAYS_THRESHOLD:
            urgency = "CRITICAL" if days_idle > 50 else "HIGH"
            alerts.append({
                "alert_type": "Stalled Deal",
                "deal_id": deal["deal_id"],
                "customer_name": deal["customer_name"],
                "deal_value": deal["deal_value"],
                "stage": deal["stage"],
                "days_stalled": days_idle,
                "owner": deal.get("owner", "Unknown"),
                "urgency": urgency,
                "financial_impact": deal["deal_value"],
                "reasoning": (
                    f"Deal '{deal['deal_id']}' for {deal['customer_name']} has had "
                    f"NO activity in {days_idle} days (threshold: {STALLED_DEAL_DAYS_THRESHOLD} days). "
                    f"Current stage: '{deal['stage']}'. Assigned to {deal.get('owner', 'Unknown')}. "
                    f"Risk of deal loss escalates significantly after 30 days of inactivity."
                ),
                "recommended_actions": [
                    f"Immediately schedule a follow-up call with {deal['customer_name']}.",
                    f"Escalate to Sales Manager if {deal.get('owner', 'rep')} does not respond in 24h.",
                    "Offer a time-limited proposal incentive (discount or extended trial).",
                ]
            })
    # Sort by deal value descending for financial impact prioritization
    return sorted(alerts, key=lambda x: x["financial_impact"], reverse=True)


def detect_churn_risks(deals: list[dict]) -> list[dict]:
    """
    Identifies customer accounts at risk of churning based on low health scores
    and engagement patterns from the CRM feed.

    Args:
        deals: List of deal records (also used for account health scoring).

    Returns:
        A prioritized list of churn risk alerts sorted by financial impact (desc).
    """
    alerts = []
    for deal in deals:
        health = deal.get("account_health_score", 100)
        if health < CHURN_RISK_HEALTH_SCORE_THRESHOLD:
            severity = "CRITICAL" if health < 30 else "HIGH"
            revenue_at_risk = int(deal["deal_value"] * 1.5)  # Account for LTV multiplier
            alerts.append({
                "alert_type": "Churn Risk",
                "deal_id": deal["deal_id"],
                "customer_name": deal["customer_name"],
                "current_deal_value": deal["deal_value"],
                "account_health_score": health,
                "severity": severity,
                "financial_impact": revenue_at_risk,
                "reasoning": (
                    f"Account '{deal['customer_name']}' has a health score of {health}/100 "
                    f"(threshold: {CHURN_RISK_HEALTH_SCORE_THRESHOLD}). "
                    f"Low score indicates declining engagement, potential support issues, or "
                    f"competitive pressure. Estimated lifetime revenue at risk: ${revenue_at_risk:,}."
                ),
                "recommended_actions": [
                    f"Assign a dedicated Customer Success Manager to {deal['customer_name']}.",
                    "Schedule an Executive Business Review (EBR) within 7 days.",
                    "Review open support tickets and resolve all P1/P2 issues immediately.",
                    "Offer a health-check consultation and usage optimization session.",
                ]
            })
    return sorted(alerts, key=lambda x: x["financial_impact"], reverse=True)


def detect_overdue_invoices(invoices: list[dict]) -> list[dict]:
    """
    Scans accounts receivable invoices and surfaces unpaid or overdue balances.

    Args:
        invoices: List of invoice records from the billing system.

    Returns:
        A prioritized list of overdue invoice alerts sorted by amount (desc).
    """
    alerts = []
    for inv in invoices:
        days_overdue = inv.get("days_overdue", 0)
        status = inv.get("status", "Paid")
        if status in ("Unpaid", "Partially Paid") and days_overdue > OVERDUE_INVOICE_DAYS_THRESHOLD:
            urgency = "CRITICAL" if days_overdue > 30 else "HIGH" if days_overdue > 14 else "MEDIUM"
            alerts.append({
                "alert_type": "Overdue Invoice",
                "invoice_id": inv["invoice_id"],
                "customer_name": inv["customer_name"],
                "amount": inv["amount"],
                "status": status,
                "days_overdue": days_overdue,
                "due_date": inv["due_date"],
                "urgency": urgency,
                "financial_impact": inv["amount"],
                "reasoning": (
                    f"Invoice '{inv['invoice_id']}' for {inv['customer_name']} is {days_overdue} days overdue. "
                    f"Balance: ${inv['amount']:,} | Status: {status}. "
                    f"Unpaid invoices directly reduce cash flow and may indicate a collections issue "
                    f"or customer dissatisfaction requiring escalation."
                ),
                "recommended_actions": [
                    f"Send an automated payment reminder to {inv['customer_name']} immediately.",
                    "Escalate to Collections team if overdue > 30 days.",
                    "Flag account for credit hold if payment not received within 5 business days.",
                    "Initiate a finance-led call to discuss payment plan or dispute resolution.",
                ]
            })
    return sorted(alerts, key=lambda x: x["financial_impact"], reverse=True)


# ─────────────────────────────────────────────────────────────
# 2. PRIORITIZATION ENGINE
# ─────────────────────────────────────────────────────────────

def prioritize_revenue_leakages(
    stalled_deals: list[dict],
    churn_risks: list[dict],
    overdue_invoices: list[dict]
) -> list[dict]:
    """
    Aggregates all revenue leakage signals across stalled deals, churn risks,
    and overdue invoices into a single prioritized recovery action plan.

    Args:
        stalled_deals: Alerts from detect_stalled_deals().
        churn_risks: Alerts from detect_churn_risks().
        overdue_invoices: Alerts from detect_overdue_invoices().

    Returns:
        A unified priority-ranked list of revenue recovery actions.
    """
    all_alerts = stalled_deals + churn_risks + overdue_invoices
    ranked = sorted(all_alerts, key=lambda x: x["financial_impact"], reverse=True)

    # Add priority rank index
    for i, alert in enumerate(ranked):
        alert["priority_rank"] = i + 1
        alert["timestamp"] = datetime.now(timezone.utc).isoformat()

    return ranked


def calculate_total_revenue_at_risk(prioritized_alerts: list[dict]) -> dict:
    """
    Computes a summary dashboard of total revenue exposure across all leakage categories.

    Args:
        prioritized_alerts: The combined and ranked alert list from prioritize_revenue_leakages().

    Returns:
        A summary dict with totals per category and grand total.
    """
    summary = {
        "Stalled Deal": {"count": 0, "total_impact": 0},
        "Churn Risk": {"count": 0, "total_impact": 0},
        "Overdue Invoice": {"count": 0, "total_impact": 0},
    }
    for alert in prioritized_alerts:
        alert_type = alert.get("alert_type", "Unknown")
        if alert_type in summary:
            summary[alert_type]["count"] += 1
            summary[alert_type]["total_impact"] += alert.get("financial_impact", 0)

    grand_total = sum(v["total_impact"] for v in summary.values())

    return {
        "breakdown": summary,
        "grand_total_at_risk": grand_total,
        "total_alerts": len(prioritized_alerts),
        "generated_at": datetime.now(timezone.utc).isoformat()
    }
