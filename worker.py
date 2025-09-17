import os
import sys
import logging
import json
from dotenv import load_dotenv

# --- IMPORTANT: Setup Sys.Path BEFORE other app imports ---
# This adds the root directory (HLAS_BOT-main/) to the Python path
sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))# ---------------------------------------------------------

# Load .env variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("hlas_worker")

# --- App Imports ---
# These imports will work because of the sys.path.append above
# --- App Imports ---
# These imports will work because of the sys.path.append above
from utils.redis_client import get_redis_client, CHAT_QUEUE_NAME
from utils.whatsapp_utils import send_whatsapp_message
from agents.intelligent_orchestrator import orchestrate_chat
from agents.fallback_system import get_fallback_response
def main_worker_loop():
    """
    The main processing loop for the worker.
    It blocks and waits for a job from the Redis queue.
    """
    logger.info("Starting HLAS Chat Worker...")
    try:
        redis_client = get_redis_client()
        logger.info(f"Worker connected to Redis, listening on queue: {CHAT_QUEUE_NAME}")
    except Exception as e:
        logger.error(f"FATAL: Worker could not connect to Redis. Exiting. {e}")
        return

    while True:
        try:
            # BLPOP is a blocking pop. It will wait here until a job is available.
            # (Queue name, timeout in seconds - 0 means wait forever)
            queue_name, job_json = redis_client.blpop(CHAT_QUEUE_NAME, 0)
            
            logger.info(f"New job received from queue: {queue_name}")
            
            # Parse the job
            job_data = json.loads(job_json)
            message = job_data["message"]
            user_phone = job_data["user_phone"]
            session_id = job_data["session_id"]
            
            logger.info(f"Processing job for session: {session_id}")

            # --- This is the logic that used to be in the handler ---
            try:
                # 1. Process through orchestrator
                response = orchestrate_chat(message, session_id)
                
                # 2. Validate response
                if not response:
                    response = get_fallback_response("general_error", session_id)
                
                # 3. Truncate if needed (WhatsApp limit)
                if len(response) > 4096:
                    response = response[:4090] + "..."
                
                logger.info(f"Response generated for {session_id}. Sending...")

            except Exception as e:
                logger.error(f"Error in orchestrate_chat for {session_id}: {e}")
                response = get_fallback_response("general_error", session_id)
            
            # 4. Send the response
            send_whatsapp_message(user_phone, response)
            # -------------------------------------------------------

        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode job from queue: {e}. Job data: {job_json}")
        except KeyboardInterrupt:
            logger.info("Worker shutting down...")
            break
        except Exception as e:
            logger.error(f"An unexpected error occurred in worker loop: {e}")
            # Try to notify the user if possible
            try:
                if 'user_phone' in job_data:
                    send_whatsapp_message(job_data['user_phone'], "I'm sorry, an unexpected error occurred while processing your request. Please try again.")
            except:
                pass

if __name__ == "__main__":
    main_worker_loop()