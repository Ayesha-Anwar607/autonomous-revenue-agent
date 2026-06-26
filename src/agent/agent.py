import asyncio
from google.genai import types
from google.adk import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService

from config.config import (
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

# Format the system instruction prompt with configurations
instructions = REVENUE_RECOVERY_SYSTEM_PROMPT.format(
    stalled_days=STALLED_DEAL_DAYS_THRESHOLD,
    health_threshold=CHURN_RISK_HEALTH_SCORE_THRESHOLD
)

# Initialize the ADK Agent
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
        calculate_total_revenue_at_risk
    ]
)

# In-memory session service to persist session state
session_service = InMemorySessionService()

async def run_revenue_agent(
    query: str,
    session_id: str = "session_1",
    user_id: str = "user_1"
) -> str:
    """
    Programmatically runs the Revenue Recovery Agent using the ADK Runner.
    
    Args:
        query: Natural language instruction/question for the agent.
        session_id: Session identifier for persistence.
        user_id: User identifier.
        
    Returns:
        The final text response from the agent.
    """
    # Verify/create session
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

    # Initialize the Runner
    runner = Runner(
        agent=revenue_recovery_agent,
        app_name="revenue_recovery",
        session_service=session_service
    )
    
    # Construct the input content
    content = types.Content(
        role="user",
        parts=[types.Part(text=query)]
    )
    
    final_text = ""
    
    # Execute the agent and listen to events
    events = runner.run_async(
        session_id=session.id,
        user_id=user_id,
        new_message=content
    )
    
    async for event in events:
        if event.is_final_response() and event.content:
            for part in event.content.parts:
                if part.text:
                    final_text += part.text
                    
    return final_text

async def interactive_loop():
    print("==================================================")
    print("🤖 Enterprise Revenue Recovery Agent")
    print("Type 'quit' or 'exit' to stop.")
    print("==================================================")
    
    session_id = "local_session_1"
    user_id = "local_admin"
    
    while True:
        try:
            query = input("\nUser: ")
            if query.lower() in ['quit', 'exit', 'q']:
                print("Goodbye!")
                break
            if not query.strip():
                continue
                
            print("\nAgent is thinking... (this may take a moment)")
            response = await run_revenue_agent(query=query, session_id=session_id, user_id=user_id)
            print(f"\nAgent: {response}")
            
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"\n[!] Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(interactive_loop())
    except KeyboardInterrupt:
        pass
