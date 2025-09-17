import re
import logging
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from langchain_core.messages import SystemMessage, HumanMessage
from app.config import llm

logger = logging.getLogger(__name__)

class Product(str, Enum):
    TRAVEL = "TRAVEL"
    MAID = "MAID"
    CAR = "CAR"  # [prev: no CAR]
    FAMILY = 'FAMILY' # [prev: no FAMILY]
    UNKNOWN = "UNKNOWN"

class Intent(str, Enum):
    GREETING = "greeting"
    PRODUCT_INQUIRY = "product_inquiry"
    INFORMATIONAL = "informational"
    PAYMENT_INQUIRY = "payment_inquiry"
    POLICY_CLAIM_STATUS = "policy_claim_status"
    CLARIFICATION_NEEDED = "clarification_needed"
    INVALID_INPUT = "invalid_input"
    OTHER = "other"  # [prev: LLM could output 'unwanted']

class PrimaryIntentResult(BaseModel):
    intent: Intent = Field(..., description="One of the allowed intents")  # [prev: free text]
    product: Product = Field(default=Product.UNKNOWN, description="Detected product")
    confidence: float = Field(..., ge=0.0, le=1.0)
    requires_clarification: bool = False

def validate_user_input(text: str) -> dict:
    if not text or not text.strip():
        return {"is_valid": False, "issue_type": "empty", "message": "I didn’t catch that — could you type a message?"}
    if len(text) > 2000:
        return {"is_valid": False, "issue_type": "too_long", "message": "That’s a bit long; could you shorten it?"}
    if re.fullmatch(r"[\W_]+", text or ""):
        return {"is_valid": False, "issue_type": "symbols_only", "message": "Could you phrase that in words?"}
    return {"is_valid": True}

_ALLOWED_INTENTS = {i.value for i in Intent}  # {'greeting', 'product_inquiry', ...}

def _deterministic_normalize(user_message: str, result: PrimaryIntentResult) -> PrimaryIntentResult:
    """Map any odd labels and fill product via keywords — belt & suspenders."""
    text = (user_message or "").lower()

    # 1) Intent guard
    intent_val = getattr(result, "intent", None)
    if isinstance(intent_val, Intent):
        intent_str = intent_val.value
    else:
        intent_str = str(intent_val or "")
    if intent_str not in _ALLOWED_INTENTS:
        if any(w in text for w in ["buy", "purchase", "i want", "quote", "get a plan"]):
            result.intent = Intent.PRODUCT_INQUIRY
        elif any(w in text for w in ["what", "cover", "benefit", "policy", "include", "exclusion"]):
            result.intent = Intent.INFORMATIONAL
        else:
            result.intent = Intent.OTHER  # [prev: could remain 'unwanted']

    # 2) Product guard
    if result.product == Product.UNKNOWN:
        if any(w in text for w in ["car", "auto", "motor", "vehicle", "tpft", "workshop"]):
            result.product = Product.CAR
        elif any(w in text for w in ["maid", "helper", "domestic"]):
            result.product = Product.MAID
        elif any(w in text for w in ["travel", "trip", "vacation", "holiday"]):
            result.product = Product.TRAVEL

    # 3) Confidence floor so router doesn’t discard good matches
    if result.confidence is None or result.confidence < 0.5:
        result.confidence = 0.75  # [prev: could be <0.5]

    return result

def get_primary_intent(user_message: str, chat_history: list) -> PrimaryIntentResult:
    """
    LLM-powered primary intent + product detection,
    with strict enums and deterministic normalization.
    """
    system = SystemMessage(content="""
You classify the user's message into an INTENT and (if possible) a PRODUCT.

ALLOWED INTENTS:
greeting, product_inquiry, informational, payment_inquiry,
policy_claim_status, clarification_needed, invalid_input, other

ALLOWED PRODUCTS:
TRAVEL (trip/holiday cover), MAID (helper coverage),
CAR (motor/auto; workshops: All Workshop/Authorised Workshop/TPFT; NCD; zero dep)

Rules:
- If user asks to buy/quote -> intent=product_inquiry.
- If user asks a general policy/benefits question -> intent=informational.
- If product is uncertain -> product=UNKNOWN.
Return ONLY JSON conforming to the tool schema.
""")  # [prev: products/message might exclude CAR]
    human = HumanMessage(content=f"Recent history: {chat_history[-6:] if len(chat_history)>6 else chat_history}\nUser: {user_message}")

    chain = llm.with_structured_output(PrimaryIntentResult, method="function_calling")
    result = chain.invoke([system, human])

    result = _deterministic_normalize(user_message, result)  # [prev: no normalization]
    logger.info(f"PRIMARY INTENT AGENT RESULT: product={result.product} intent='{result.intent.value}' confidence={result.confidence} requires_clarification={result.requires_clarification}")  # [prev: could log 'unwanted']
    logger.info(f"Intent classification - Product: {result.product}, Intent: {result.intent}")
    return result
