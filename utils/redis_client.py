import os
import logging
import redis
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_redis_client = None

def get_redis_client():
    """
    Get a singleton Redis client instance.
    """
    global _redis_client
    if _redis_client is None:
        try:
            redis_url = os.getenv("REDIS_URL") # e.g., "redis://localhost:6379/0"
            if not redis_url:
                raise ValueError("REDIS_URL not set in .env file")
            
            # decode_responses=True makes Redis return strings, not bytes
            _redis_client = redis.from_url(redis_url, decode_responses=True)
            _redis_client.ping()
            logger.info("Successfully connected to Redis.")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    return _redis_client

# Define the queue name
CHAT_QUEUE_NAME = "hlas_chat_queue"