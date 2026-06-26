import random
from datetime import datetime, timedelta

def fetch_crm_deals(limit: int = 10) -> list[dict]:
    """
    Fetches real-time CRM sales pipeline data.
    
    Args:
        limit: Maximum number of deals to fetch.
        
    Returns:
        A list of dictionaries representing sales deals, stages, and health scores.
    """
    # Deterministic generation for consistency in testing, but dynamic contents
    random.seed(42)
    
    companies = [
        "Acme Corp", "Beta Solutions", "Gamma Enterprises", "Delta Logistics",
        "Epsilon Health", "Zeta Finance", "Eta Retail", "Theta Tech",
        "Iota Energy", "Kappa Media"
    ]
    
    stages = ["Discovery", "Qualification", "Proposal", "Negotiation", "Closed Won", "Closed Lost"]
    
    deals = []
    for i in range(min(limit, len(companies))):
        company = companies[i]
        value = random.randint(15000, 250000)
        stage = random.choice(stages)
        # Generate some stalled deals
        if i in [1, 4]:
            last_activity_days = random.randint(35, 60) # > 30 days threshold
        else:
            last_activity_days = random.randint(2, 20)
            
        # Generate some low health scores (churn risks)
        if i in [2, 4]:
            health_score = random.randint(20, 45) # < 50 threshold
        else:
            health_score = random.randint(65, 95)
            
        deals.append({
            "deal_id": f"DEAL-{1000 + i}",
            "customer_name": company,
            "deal_value": value,
            "stage": stage,
            "last_activity_days_ago": last_activity_days,
            "account_health_score": health_score,
            "owner": f"Sales Rep {i % 3 + 1}"
        })
    return deals


def fetch_invoices(limit: int = 10) -> list[dict]:
    """
    Fetches active accounts receivable invoice status logs.
    
    Args:
        limit: Maximum number of invoices to fetch.
        
    Returns:
        A list of dictionaries containing invoices and payment status.
    """
    random.seed(100)
    
    companies = [
        "Acme Corp", "Beta Solutions", "Gamma Enterprises", "Delta Logistics",
        "Epsilon Health", "Zeta Finance", "Eta Retail", "Theta Tech",
        "Iota Energy", "Kappa Media"
    ]
    
    invoices = []
    for i in range(min(limit, len(companies))):
        company = companies[i]
        amount = random.randint(5000, 80000)
        
        # Generate some unpaid/overdue invoices
        if i in [0, 3, 5]:
            status = "Unpaid"
            days_overdue = random.randint(5, 45)
        elif i in [2]:
            status = "Partially Paid"
            days_overdue = random.randint(15, 30)
        else:
            status = "Paid"
            days_overdue = 0
            
        invoices.append({
            "invoice_id": f"INV-{5000 + i}",
            "customer_name": company,
            "amount": amount,
            "status": status,
            "days_overdue": days_overdue,
            "due_date": (datetime.now() - timedelta(days=days_overdue)).strftime("%Y-%m-%d") if days_overdue > 0 else (datetime.now() + timedelta(days=random.randint(5, 30))).strftime("%Y-%m-%d")
        })
    return invoices


def real_time_market_risk_analysis(query: str) -> str:
    """
    Performs basic market research or competitor analysis using a mock internet search.
    Provides external signals that might affect customer churn or deal viability.
    
    Args:
        query: Market segment or competitor search query.
        
    Returns:
        A text summary of recent market risks, competitor offerings, or macroeconomic signals.
    """
    # Simple simulated internet research results for common query terms
    query_lower = query.lower()
    
    if "beta" in query_lower or "solutions" in query_lower:
        return (
            "[Search Signal] Beta Solutions recently faced a system outage affecting 15% of users. "
            "Competitors are aggressively targeting their customer base with migration discounts."
        )
    elif "acme" in query_lower or "corp" in query_lower:
        return (
            "[Search Signal] Acme Corp announced budget cuts of 10% across IT and procurement divisions "
            "for the upcoming fiscal quarter. This may delay new deal approvals."
        )
    elif "competitor" in query_lower or "market" in query_lower:
        return (
            "[Search Signal] General industry trends show a shift towards pay-as-you-go pricing models. "
            "Fixed enterprise contracts are seeing increased pressure to renegotiate."
        )
    else:
        return (
            f"[Search Signal] Search results for '{query}': No high-priority macroeconomic headwinds found, "
            "but general procurement cycles in this vertical have lengthened by 15-20%."
        )
