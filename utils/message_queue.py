import redis
import json
import logging

# Setup logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Redis connection
r = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

QUEUE_NAME = "intent:incoming_queue"

def enqueue_message(session_id: str, user_message: str):
    payload = {
        "session_id": session_id,
        "user_message": user_message
    }
    r.rpush(QUEUE_NAME, json.dumps(payload))
    logger.info(f"🔃 Enqueued message for session {session_id}")

def dequeue_message():
    message = r.lpop(QUEUE_NAME)
    if message:
        logger.info(f"🔽 Dequeued message: {message}")
        return json.loads(message)
    return None

def queue_length():
    return r.llen(QUEUE_NAME)
