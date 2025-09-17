import time
import logging
from utils.message_queue import dequeue_message
from agents.primary_intent_agent import classify_intent

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def process_message(session_id: str, user_message: str):
    logger.info(f"🔍 Processing message for session {session_id}: {user_message}")
    result = classify_intent(session_id, user_message)
    logger.info(f"✅ Result for session {session_id}: {result}")
    return result

if __name__ == "__main__":
    logger.info("📥 Queue worker started. Waiting for messages...")

    while True:
        item = dequeue_message()
        if item:
            session_id = item["session_id"]
            user_message = item["user_message"]
            try:
                process_message(session_id, user_message)
            except Exception as e:
                logger.error(f"❌ Error processing session {session_id}: {str(e)}")
        else:
            time.sleep(1)  # No message, wait a bit
