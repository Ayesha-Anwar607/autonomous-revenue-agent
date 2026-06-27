import os
import asyncio
import pytest
from src.config.config import GEMINI_MODEL
from src.tools.tools import fetch_crm_deals, fetch_invoices, real_time_market_risk_analysis
from src.tools.business_logic import (
    detect_stalled_deals,
    detect_churn_risks,
    detect_overdue_invoices,
    prioritize_revenue_leakages,
    calculate_total_revenue_at_risk,
)
from src.agent.hitl_gate import (
    HITLGate,
    ACTION_RISK_REGISTRY,
    get_hitl_audit_log,
)
from src.agent.agent import revenue_recovery_agent, run_revenue_agent


# ─────────────────────────────────────────────────────────────
# PHASE 1 TOOL TESTS (regression)
# ─────────────────────────────────────────────────────────────

def test_fetch_crm_deals():
    deals = fetch_crm_deals(limit=5)
    assert len(deals) <= 5
    assert all(k in deals[0] for k in ["deal_id", "customer_name", "deal_value", "stage"])


def test_fetch_invoices():
    invoices = fetch_invoices(limit=5)
    assert len(invoices) <= 5
    assert all(k in invoices[0] for k in ["invoice_id", "customer_name", "amount", "status"])


def test_market_risk_analysis():
    result = real_time_market_risk_analysis("Beta Solutions")
    assert "Search Signal" in result


# ─────────────────────────────────────────────────────────────
# PHASE 2 BUSINESS LOGIC TESTS
# ─────────────────────────────────────────────────────────────

def test_detect_stalled_deals():
    """Stalled deals must be sorted by deal_value desc and have required fields."""
    deals = fetch_crm_deals(limit=10)
    alerts = detect_stalled_deals(deals)

    # At least 1 stalled deal is guaranteed in mock data (indices 1, 4)
    assert len(alerts) >= 1

    for alert in alerts:
        assert alert["alert_type"] == "Stalled Deal"
        assert alert["days_stalled"] > 30
        assert "reasoning" in alert
        assert "recommended_actions" in alert
        assert isinstance(alert["recommended_actions"], list)

    # Verify descending financial impact ordering
    impacts = [a["financial_impact"] for a in alerts]
    assert impacts == sorted(impacts, reverse=True)


def test_detect_churn_risks():
    """Churn risk alerts must flag accounts below the health threshold."""
    deals = fetch_crm_deals(limit=10)
    alerts = detect_churn_risks(deals)

    # At least 1 churn risk is guaranteed in mock data (indices 2, 4)
    assert len(alerts) >= 1

    for alert in alerts:
        assert alert["alert_type"] == "Churn Risk"
        assert alert["account_health_score"] < 50
        assert alert["financial_impact"] > alert["current_deal_value"]  # LTV multiplier applied
        assert "reasoning" in alert

    impacts = [a["financial_impact"] for a in alerts]
    assert impacts == sorted(impacts, reverse=True)


def test_detect_overdue_invoices():
    """Overdue invoice alerts must flag unpaid/partially paid invoices."""
    invoices = fetch_invoices(limit=10)
    alerts = detect_overdue_invoices(invoices)

    assert len(alerts) >= 1

    for alert in alerts:
        assert alert["alert_type"] == "Overdue Invoice"
        assert alert["status"] in ("Unpaid", "Partially Paid")
        assert alert["days_overdue"] > 0
        assert "reasoning" in alert

    amounts = [a["financial_impact"] for a in alerts]
    assert amounts == sorted(amounts, reverse=True)


def test_prioritize_revenue_leakages():
    """Unified alert list must be globally sorted by financial impact."""
    deals = fetch_crm_deals(limit=10)
    invoices = fetch_invoices(limit=10)

    stalled = detect_stalled_deals(deals)
    churn = detect_churn_risks(deals)
    overdue = detect_overdue_invoices(invoices)

    ranked = prioritize_revenue_leakages(stalled, churn, overdue)

    assert len(ranked) == len(stalled) + len(churn) + len(overdue)
    assert ranked[0]["priority_rank"] == 1
    assert "timestamp" in ranked[0]

    impacts = [a["financial_impact"] for a in ranked]
    assert impacts == sorted(impacts, reverse=True)


def test_calculate_total_revenue_at_risk():
    """Revenue summary must correctly total financial impact per category."""
    deals = fetch_crm_deals(limit=10)
    invoices = fetch_invoices(limit=10)

    ranked = prioritize_revenue_leakages(
        detect_stalled_deals(deals),
        detect_churn_risks(deals),
        detect_overdue_invoices(invoices),
    )
    summary = calculate_total_revenue_at_risk(ranked)

    assert "grand_total_at_risk" in summary
    assert "breakdown" in summary
    assert summary["grand_total_at_risk"] > 0
    assert summary["total_alerts"] == len(ranked)

    # Verify breakdown sums match grand total
    breakdown_sum = sum(v["total_impact"] for v in summary["breakdown"].values())
    assert breakdown_sum == summary["grand_total_at_risk"]


# ─────────────────────────────────────────────────────────────
# HITL GATE TESTS
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hitl_auto_approves_low_risk():
    """LOW risk actions with auto_approve_low_risk=True should auto-approve."""
    gate = HITLGate(auto_approve_low_risk=True)
    approved = await gate.request_approval("generate_report", {"report_type": "weekly"})
    assert approved is True

    log = gate.get_audit_log()
    assert len(log) == 1
    assert log[0]["approved"] is True
    assert log[0]["approver"] == "auto"


@pytest.mark.asyncio
async def test_hitl_callback_approval():
    """Callback mode must pass the decision from the external callback."""
    gate = HITLGate(auto_approve_low_risk=True)

    async def mock_approve(action_name, context):
        return True  # Simulate UI clicking "Approve"

    async def mock_reject(action_name, context):
        return False  # Simulate UI clicking "Reject"

    approved = await gate.request_approval(
        "send_recovery_email",
        {"Customer": "Test Co.", "Email": "test@co.com"},
        approval_callback=mock_approve,
    )
    assert approved is True

    rejected = await gate.request_approval(
        "escalate_to_collections",
        {"Customer": "Test Co.", "Invoice ID": "INV-999"},
        approval_callback=mock_reject,
    )
    assert rejected is False

    log = gate.get_audit_log()
    assert len(log) == 2
    assert log[0]["approved"] is True
    assert log[1]["approved"] is False


def test_hitl_unknown_action_raises():
    """Requesting approval for an unregistered action must raise ValueError."""
    gate = HITLGate()
    with pytest.raises(ValueError, match="Unknown action"):
        asyncio.run(gate.request_approval("unregistered_action", {}))


def test_action_risk_registry_completeness():
    """All registered actions must have required metadata keys."""
    required_keys = {"risk_level", "description", "requires_approval"}
    for action, meta in ACTION_RISK_REGISTRY.items():
        assert required_keys.issubset(meta.keys()), f"Action '{action}' missing keys."
        assert meta["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


# ─────────────────────────────────────────────────────────────
# AGENT INITIALIZATION TEST
# ─────────────────────────────────────────────────────────────

def test_agent_initialization():
    """Verify upgraded Phase 2 agent is correctly configured."""
    assert revenue_recovery_agent.name == "revenue_recovery_agent"
    assert revenue_recovery_agent.model == GEMINI_MODEL
    # Expect at least 8 tools in Phase 2
    assert len(revenue_recovery_agent.tools) >= 8


# ─────────────────────────────────────────────────────────────
# END-TO-END INTEGRATION TEST (requires GEMINI_API_KEY)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_end_to_end_revenue_scan():
    """Full agent execution test — skipped if API key not configured or rate-limited."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or "YOUR_GEMINI_API_KEY" in api_key:
        pytest.skip("Skipping E2E test: GEMINI_API_KEY not configured.")

    try:
        response = await run_revenue_agent(
            query="Run a full revenue recovery scan and give me the top 3 issues by financial impact.",
            session_id="test_e2e_phase2",
        )
        assert isinstance(response, str)
        assert len(response) > 50
        print(f"\n[E2E Response]:\n{response}")

    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "quota" in err_str.lower():
            pytest.skip(f"Skipping E2E test: Gemini free-tier rate limit hit (429). Run again in 1 min.")
        raise
