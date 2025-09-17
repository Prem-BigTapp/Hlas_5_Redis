# agents/car_agent.py
import logging
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage

from app.session_manager import (
    get_session,
    set_collected_info,
    set_stage,
    update_conversation_context,
)
from utils.llm_services import llm

logger = logging.getLogger(__name__)

class CarInfo(BaseModel):
    """Fields to collect for a car policy purchase flow."""
    vehicle_make: Optional[str] = Field(None, description="Car make (e.g., Toyota)")
    vehicle_model: Optional[str] = Field(None, description="Car model (e.g., Altis)")
    registration_year: Optional[str] = Field(None, description="First registration year (YYYY)")
    usage_type: Optional[str] = Field(None, description="private|commercial")
    driver_age: Optional[int] = Field(None, description="Main driver age in years")
    ncd: Optional[str] = Field(None, description="No-Claim Discount, if known (e.g., 30%)")
    addons: Optional[List[str]] = Field(default=None, description="Add-ons like zero_depreciation, roadside_assistance")
    response: str = Field(..., description="Natural-language reply to the user")

def run_car_agent(user_message: str, chat_history: list, session_id: str) -> str:
    """
    Converse + collect car info using structured output, persist fields,
    and auto-advance to recommendation (with purchase guidance) when complete.
    """
    # Ensure context shows we’re in the CAR flow
    update_conversation_context(session_id, current_agent="car", primary_product="CAR")  # [prev: no CAR context updates]

    session = get_session(session_id)
    collected_info = (session.get("collected_info") or {}).get("car_info", {})

    required_info = ["vehicle_make", "vehicle_model", "registration_year", "driver_age"]

    # Structured extraction + conversational reply (parity with Travel/Maid)
    chain = llm.with_structured_output(CarInfo, method="function_calling")  # [prev: no structured output extraction for CAR]

    today = datetime.now().strftime("%Y-%m-%d")
    sys_instructions = (
        "You are a friendly CAR insurance assistant.\n"
        "Collect ONLY the missing required fields in this order: "
        + ", ".join(required_info) + ".\n"
        "Optional: usage_type (private|commercial), ncd (e.g., 30%), addons (e.g., zero_depreciation).\n"
        f"Validate registration_year is YYYY and not after today ({today}); driver_age 18–90.\n"
        "Be concise; if fields are missing, ask ONE targeted follow-up."
    )

    prompt = [
        SystemMessage(content=sys_instructions),
        HumanMessage(content=user_message),
    ]
    result: CarInfo = chain.invoke(prompt)

    # Merge extracted fields into session (same as Travel/Maid)
    for k, v in result.model_dump().items():
        if k != "response" and v:
            collected_info[k] = v

    set_collected_info(session_id, "car_info", collected_info)  # [prev: no set_collected_info for CAR]
    logger.info(f"[CAR] Collected so far for {session_id}: {collected_info}")

    # Auto-advance when all required fields are present
    if all(key in collected_info and collected_info[key] for key in required_info):
        from .recommendation_agent import get_recommendation
        from .rec_retriever_agent import get_recommendation_message

        set_stage(session_id, "recommendation")
        rec = get_recommendation(session_id, "CAR")
        plan_tier = rec.get("plan", "Authorised Workshop")
        update_conversation_context(session_id, recommended_plan=plan_tier)

        benefits_msg = get_recommendation_message("CAR", plan_tier)

        guidance = (
            "\n\n**What’s next?**\n"
            "• Ask about other tiers or add-ons (e.g., *TPFT vs All Workshop*, *Zero Depreciation*, *Roadside Assistance*)\n"
            "• Ask any coverage question (e.g., *windscreen claims*, *PA limits*, *young driver excess*)\n"
            "• Say *proceed to payment* or *buy now* to continue\n"
        )
        return benefits_msg + guidance  # [prev: no purchase guidance for CAR]

    # Still collecting fields → reply with the LLM's conversational prompt
    return result.response
