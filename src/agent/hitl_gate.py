"""
Phase 2: Human-in-the-Loop (HITL) Authorization Gate
Intercepts high-impact agent actions and requires explicit human approval
before they are executed. This acts as a safety rail for consequential
operations like sending customer outreach, escalating to collections, or
placing accounts on credit hold.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# ACTION RISK REGISTRY
# Maps action names to their risk level and description.
# ─────────────────────────────────────────────────────────────
ACTION_RISK_REGISTRY: dict[str, dict] = {
    "send_recovery_email": {
        "risk_level": "MEDIUM",
        "description": "Send an automated recovery outreach email to a customer.",
        "requires_approval": True,
    },
    "escalate_to_collections": {
        "risk_level": "HIGH",
        "description": "Escalate an overdue invoice account to the collections team.",
        "requires_approval": True,
    },
    "place_credit_hold": {
        "risk_level": "CRITICAL",
        "description": "Place a customer account on credit hold, blocking future orders.",
        "requires_approval": True,
    },
    "schedule_executive_call": {
        "risk_level": "MEDIUM",
        "description": "Schedule an executive business review call with a customer.",
        "requires_approval": True,
    },
    "apply_retention_discount": {
        "risk_level": "HIGH",
        "description": "Apply an automatic retention discount to prevent churn.",
        "requires_approval": True,
    },
    "generate_report": {
        "risk_level": "LOW",
        "description": "Generate and export a revenue recovery report.",
        "requires_approval": False,  # Low-risk: auto-approved
    },
}


# ─────────────────────────────────────────────────────────────
# HITL GATE CLASS
# ─────────────────────────────────────────────────────────────

class HITLGate:
    """
    Human-in-the-Loop authorization gate for high-impact agent actions.
    
    Provides two approval modes:
    - CLI mode: blocks and prompts the terminal operator for input.
    - Callback mode: delegates approval to an external async callback (for APIs/UIs).
    """

    def __init__(self, auto_approve_low_risk: bool = True):
        """
        Args:
            auto_approve_low_risk: If True, LOW risk actions are auto-approved
                                   without human intervention.
        """
        self.auto_approve_low_risk = auto_approve_low_risk
        self._approval_log: list[dict] = []

    def _log_decision(
        self,
        action_name: str,
        context: dict,
        approved: bool,
        approver: str = "system"
    ) -> None:
        """Records every HITL decision for audit trail."""
        self._approval_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action_name,
            "context": context,
            "approved": approved,
            "approver": approver,
        })

    def get_audit_log(self) -> list[dict]:
        """Returns the full HITL decision audit log."""
        return self._approval_log

    async def request_approval(
        self,
        action_name: str,
        context: dict,
        approval_callback: Callable | None = None
    ) -> bool:
        """
        Requests human approval for a named action before executing it.

        Args:
            action_name: The registered action name (must exist in ACTION_RISK_REGISTRY).
            context: Dict of contextual data about the action (e.g., customer name, amount).
            approval_callback: Optional async function for non-CLI approval (API/UI mode).
                               Must accept (action_name, context) and return bool.

        Returns:
            True if approved, False if rejected.

        Raises:
            ValueError: If action_name is not in ACTION_RISK_REGISTRY.
        """
        if action_name not in ACTION_RISK_REGISTRY:
            raise ValueError(
                f"Unknown action '{action_name}'. "
                f"Register it in ACTION_RISK_REGISTRY first."
            )

        action_meta = ACTION_RISK_REGISTRY[action_name]
        risk_level = action_meta["risk_level"]
        requires_approval = action_meta["requires_approval"]

        # Auto-approve low-risk actions if configured
        if not requires_approval or (self.auto_approve_low_risk and risk_level == "LOW"):
            logger.info(f"[HITL] Auto-approved LOW-risk action: {action_name}")
            self._log_decision(action_name, context, approved=True, approver="auto")
            return True

        # Use external callback if provided (API/UI integration mode)
        if approval_callback is not None:
            approved = await approval_callback(action_name, context)
            approver = "callback"
            self._log_decision(action_name, context, approved=approved, approver=approver)
            return approved

        # ── CLI INTERACTIVE MODE ──────────────────────────────────
        self._print_approval_prompt(action_name, action_meta, context, risk_level)
        
        # Block on user input
        loop = asyncio.get_event_loop()
        raw_input = await loop.run_in_executor(
            None,
            lambda: input("Decision [approve/reject]: ").strip().lower()
        )

        approved = raw_input in ("approve", "a", "yes", "y")
        approver = "human_cli"
        self._log_decision(action_name, context, approved=approved, approver=approver)

        if approved:
            print(f"\n✅  [HITL] Action '{action_name}' APPROVED by operator.\n")
        else:
            print(f"\n❌  [HITL] Action '{action_name}' REJECTED by operator.\n")

        return approved

    def _print_approval_prompt(
        self, action_name: str, meta: dict, context: dict, risk_level: str
    ) -> None:
        """Formats a clear, human-readable approval request in the CLI."""
        risk_colors = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🟠", "CRITICAL": "🔴"}
        icon = risk_colors.get(risk_level, "⚪")

        print("\n" + "=" * 60)
        print(f"  {icon}  HUMAN AUTHORIZATION REQUIRED")
        print("=" * 60)
        print(f"  Action   : {action_name}")
        print(f"  Risk     : {icon} {risk_level}")
        print(f"  Details  : {meta['description']}")
        print("-" * 60)
        print("  Context:")
        for k, v in context.items():
            print(f"    {k:20s}: {v}")
        print("=" * 60)


# ─────────────────────────────────────────────────────────────
# HITL-GATED ACTION TOOLS (callable by the ADK agent)
# ─────────────────────────────────────────────────────────────

# Shared gate instance (can be injected as dependency)
_hitl_gate = HITLGate(auto_approve_low_risk=True)


async def send_recovery_email(
    customer_name: str,
    contact_email: str,
    issue_type: str,
    financial_impact: int
) -> dict:
    """
    Sends an automated recovery outreach email to a customer after HITL approval.
    Covers stalled deal re-engagement, churn prevention, and invoice payment reminders.

    Args:
        customer_name: Name of the customer account.
        contact_email: Customer's contact email address.
        issue_type: Type of recovery issue (e.g., 'Stalled Deal', 'Churn Risk', 'Overdue Invoice').
        financial_impact: Dollar value of revenue at risk.

    Returns:
        Status dict indicating approval result and action taken.
    """
    context = {
        "Customer": customer_name,
        "Email": contact_email,
        "Issue Type": issue_type,
        "Revenue at Risk": f"${financial_impact:,}",
    }
    approved = await _hitl_gate.request_approval("send_recovery_email", context)
    if not approved:
        return {"status": "REJECTED", "action": "send_recovery_email", "customer": customer_name}

    # Simulate email dispatch
    print(f"[ACTION] 📧 Recovery email sent to {contact_email} for account '{customer_name}'.")
    return {
        "status": "EXECUTED",
        "action": "send_recovery_email",
        "customer": customer_name,
        "email": contact_email,
        "issue_type": issue_type,
    }


async def escalate_to_collections(
    customer_name: str,
    invoice_id: str,
    amount_overdue: int,
    days_overdue: int
) -> dict:
    """
    Escalates a severely overdue invoice to the collections team after HITL approval.

    Args:
        customer_name: Name of the customer account.
        invoice_id: The specific invoice ID to escalate.
        amount_overdue: Total outstanding balance.
        days_overdue: Number of days past the invoice due date.

    Returns:
        Status dict indicating approval result and action taken.
    """
    context = {
        "Customer": customer_name,
        "Invoice ID": invoice_id,
        "Amount Overdue": f"${amount_overdue:,}",
        "Days Overdue": str(days_overdue),
    }
    approved = await _hitl_gate.request_approval("escalate_to_collections", context)
    if not approved:
        return {"status": "REJECTED", "action": "escalate_to_collections", "customer": customer_name}

    print(f"[ACTION] 🚨 Invoice {invoice_id} for '{customer_name}' escalated to Collections team.")
    return {
        "status": "EXECUTED",
        "action": "escalate_to_collections",
        "customer": customer_name,
        "invoice_id": invoice_id,
        "amount": amount_overdue,
    }


async def apply_retention_discount(
    customer_name: str,
    deal_id: str,
    discount_percent: float,
    deal_value: int
) -> dict:
    """
    Applies a retention discount to a churn-risk or stalled deal after HITL approval.

    Args:
        customer_name: Name of the at-risk customer account.
        deal_id: The deal identifier to apply the discount to.
        discount_percent: Percentage discount to offer (e.g., 10.0 for 10%).
        deal_value: Original deal value to compute the discount impact.

    Returns:
        Status dict indicating approval result and action taken.
    """
    discount_value = int(deal_value * discount_percent / 100)
    context = {
        "Customer": customer_name,
        "Deal ID": deal_id,
        "Discount": f"{discount_percent}%",
        "Discount Value": f"${discount_value:,}",
        "Original Deal Value": f"${deal_value:,}",
    }
    approved = await _hitl_gate.request_approval("apply_retention_discount", context)
    if not approved:
        return {"status": "REJECTED", "action": "apply_retention_discount", "customer": customer_name}

    print(f"[ACTION] 💰 {discount_percent}% retention discount applied on deal {deal_id} for '{customer_name}'.")
    return {
        "status": "EXECUTED",
        "action": "apply_retention_discount",
        "customer": customer_name,
        "deal_id": deal_id,
        "discount_percent": discount_percent,
        "discount_value": discount_value,
    }


def get_hitl_audit_log() -> list[dict]:
    """
    Retrieves the full HITL decision audit trail for compliance reporting.

    Returns:
        List of all HITL decisions made in the current session with timestamps.
    """
    return _hitl_gate.get_audit_log()
