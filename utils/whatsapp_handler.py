"""
Enhanced WhatsApp Handler for Production-Grade HLAS Insurance Chatbot
=====================================================================

This module provides comprehensive WhatsApp message handling with robust
error recovery, validation, and production-grade features.

This version is modified to act as a "Producer" for a Redis task queue.
"""

import os
import re
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime
import asyncio
import json
from fastapi import Request, Response
import requests

# Note: orchestrate_chat is no longer imported here, it's used by the worker
from agents.fallback_system import get_fallback_response
from app.session_manager import get_session_stats, cleanup_old_sessions

# Import the new utils
from .redis_client import get_redis_client, CHAT_QUEUE_NAME
from .whatsapp_utils import send_whatsapp_message
logger = logging.getLogger(__name__)

class WhatsAppMessageHandler:
    """
    Enhanced WhatsApp message handler. Acts as a Producer for Redis.
    """
    
    def __init__(self):
        self.verify_token = os.environ.get("META_VERIFY_TOKEN")
        self.access_token = os.environ.get("META_ACCESS_TOKEN")
        self.phone_number_id = os.environ.get("META_PHONE_NUMBER_ID")
        self.max_message_length = 4096  # WhatsApp limit
        self.rate_limit_window = 60  # seconds
        self.rate_limit_max_messages = 10  # per window
        self.message_counts = {}  # Simple rate limiting storage
        
        try:
            self.redis_client = get_redis_client()
        except Exception as e:
            logger.error(f"FATAL: Could not connect to Redis on init. {e}")
            self.redis_client = None
        
    def verify_webhook(self, request: Request) -> Response:
        """
        Verifies the webhook subscription with Meta with enhanced validation.
        """
        try:
            # Extract query parameters
            mode = request.query_params.get("hub.mode")
            token = request.query_params.get("hub.verify_token")
            challenge = request.query_params.get("hub.challenge")
            
            logger.info(f"Webhook verification attempt - Mode: {mode}, Token present: {bool(token)}")
            
            # Validate required parameters
            if not all([mode, token, challenge]):
                logger.warning("Missing required webhook verification parameters")
                return Response(content="Missing parameters", status_code=400)
            
            # Check the mode and token
            if mode == "subscribe" and token == self.verify_token:
                logger.info("Webhook verification successful")
                return Response(content=challenge, status_code=200)
            else:
                logger.warning(f"Webhook verification failed - Invalid mode or token")
                return Response(content="Verification failed", status_code=403)
                
        except Exception as e:
            logger.error(f"Error in webhook verification: {str(e)}")
            return Response(content="Internal error", status_code=500)
    
    def extract_message_data(self, data: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
        """
        Extract message and user information from WhatsApp webhook data with validation.
        
        Returns:
            Tuple[message, user_phone_number, metadata]
        """
        try:
            # Check if this is a status update (e.g., 'sent', 'delivered', 'read')
            value = data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {})
            if 'statuses' in value:
                try:
                    status_info = value['statuses'][0]
                    status = status_info.get('status', 'unknown')
                    recipient_id = status_info.get('recipient_id', 'unknown')
                    logger.info(f"Received '{status}' status update for {recipient_id}. Ignoring.")
                except (IndexError, KeyError):
                    logger.info("Received a status update with unexpected format. Ignoring.")
                return None, None, {}

            # Multiple extraction patterns for different webhook formats
            extraction_patterns = [
                # Standard format
                lambda d: (
                    d['entry'][0]['changes'][0]['value']['messages'][0]['text']['body'],
                    d['entry'][0]['changes'][0]['value']['messages'][0]['from']
                ),
                # Alternative format 1
                lambda d: (
                    d['entry']['changes']['value']['messages']['text']['body'],
                    d['entry']['changes']['value']['messages']['from']
                ),
                # Alternative format 2
                lambda d: (
                    d['body']['text'],
                    d['from']
                )
            ]
            
            message = None
            user_phone = None
            metadata = {}
            
            for pattern in extraction_patterns:
                try:
                    message, user_phone = pattern(data)
                    if message and user_phone:
                        break
                except (KeyError, IndexError, TypeError):
                    continue
            
            if not message or not user_phone:
                # This is now only an error if it's not a status update
                logger.warning(f"Could not extract message data from webhook. Not a user message or status update: {data}")
                return None, None, {}
            
            # Extract additional metadata
            try:
                if 'entry' in data and isinstance(data['entry'], list):
                    entry = data['entry'][0]
                    if 'changes' in entry and isinstance(entry['changes'], list):
                        change = entry['changes'][0]
                        if 'value' in change and 'messages' in change['value']:
                            msg_data = change['value']['messages'][0]
                            metadata = {
                                'message_id': msg_data.get('id'),
                                'timestamp': msg_data.get('timestamp'),
                                'type': msg_data.get('type', 'text'),
                                'from_name': change['value'].get('contacts', [{}])[0].get('profile', {}).get('name', 'Unknown')
                            }
            except Exception as e:
                logger.warning(f"Could not extract metadata: {str(e)}")
            
            # Validate and clean message
            message = self.validate_and_clean_message(message)
            user_phone = self.validate_phone_number(user_phone)
            
            return message, user_phone, metadata
            
        except Exception as e:
            logger.error(f"Error extracting message data: {str(e)}")
            return None, None, {}
    
    def validate_and_clean_message(self, message: str) -> Optional[str]:
        """
        Validate and clean incoming message.
        """
        if not message:
            return None
        
        # Remove excessive whitespace
        message = re.sub(r'\s+', ' ', message.strip())
        
        # Check length
        if len(message) > self.max_message_length:
            logger.warning(f"Message too long: {len(message)} characters")
            message = message[:self.max_message_length] + "..."
        
        # Basic content filtering (can be enhanced)
        if len(message) < 1:
            return None
        
        return message
    
    def validate_phone_number(self, phone: str) -> Optional[str]:
        """
        Validate and normalize phone number.
        """
        if not phone:
            return None
        
        # Remove non-numeric characters except +
        clean_phone = re.sub(r'[^\d+]', '', phone)
        
        # Basic validation
        if len(clean_phone) < 8 or len(clean_phone) > 15:
            logger.warning(f"Invalid phone number format: {phone}")
            return None
        
        return clean_phone
    
    def check_rate_limit(self, user_phone: str) -> bool:
        """
        Simple rate limiting check.
        """
        try:
            current_time = datetime.now()
            
            if user_phone not in self.message_counts:
                self.message_counts[user_phone] = []
            
            # Clean old entries
            cutoff_time = current_time.timestamp() - self.rate_limit_window
            self.message_counts[user_phone] = [
                timestamp for timestamp in self.message_counts[user_phone]
                if timestamp > cutoff_time
            ]
            
            # Check if limit exceeded
            if len(self.message_counts[user_phone]) >= self.rate_limit_max_messages:
                logger.warning(f"Rate limit exceeded for {user_phone}")
                return False
            
            # Add current message
            self.message_counts[user_phone].append(current_time.timestamp())
            return True
            
        except Exception as e:
            logger.error(f"Error in rate limiting: {str(e)}")
            return True  # Allow on error
    
    async def process_webhook(self, request: Request) -> Response:
        """
        Main webhook processing function. It acknowledges the request immediately
        and then pushes the message to the Redis queue.
        """
        try:
            data = await request.json()
            logger.debug(f"Received webhook data: {data}")
            
            message, user_phone, metadata = self.extract_message_data(data)
            
            if message and user_phone:
                # 1. Check rate limit
                if not self.check_rate_limit(user_phone):
                    rate_limit_msg = "You're sending messages too quickly! 😅 Please wait a moment and try again."
                    # Send rate limit message directly
                    send_whatsapp_message(user_phone, rate_limit_msg)
                    return Response(status_code=200) # Still acknowledge Meta

                # 2. Prepare the job payload
                session_id = f"whatsapp_{user_phone}"
                job_payload = {
                    "message": message,
                    "user_phone": user_phone,
                    "session_id": session_id,
                    "metadata": metadata
                }
                
                # 3. Push to Redis queue
                if self.redis_client:
                    try:
                        job_json = json.dumps(job_payload)
                        self.redis_client.rpush(CHAT_QUEUE_NAME, job_json)
                        logger.info(f"Pushed job for {user_phone} to queue {CHAT_QUEUE_NAME}")
                    except Exception as e:
                        logger.error(f"Failed to push job to Redis: {e}")
                        # Fallback: send an error message directly
                        send_whatsapp_message(user_phone, "I'm having trouble with my queue system. Please try again in a moment.")
                else:
                    logger.error("Redis client not available. Cannot queue job.")
                    send_whatsapp_message(user_phone, "I'm experiencing a high volume of requests. Please try again later.")
            
            # Always return 200 to acknowledge receipt of the event
            return Response(status_code=200)
            
        except Exception as e:
            logger.error(f"Critical error in webhook processing: {str(e)}")
            # Still return 200 to avoid webhook disabling, but log the error
            return Response(status_code=200)
    
    def get_health_status(self) -> Dict[str, Any]:
        """
        Get health status for monitoring.
        """
        try:
            # Clean up old sessions periodically
            cleanup_old_sessions(max_age_hours=24)
            
            # Get session statistics
            session_stats = get_session_stats()
            
            # Get rate limiting stats
            active_users = len([
                phone for phone, timestamps in self.message_counts.items()
                if len(timestamps) > 0
            ])
            
            return {
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "sessions": session_stats,
                "active_rate_limited_users": active_users,
                "webhook_verification_token_configured": bool(self.verify_token)
            }
            
        except Exception as e:
            logger.error(f"Error getting health status: {str(e)}")
            return {
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e)
            }

# Global handler instance
whatsapp_handler = WhatsAppMessageHandler()

# Convenience functions for FastAPI routes
async def handle_whatsapp_verification(request: Request) -> Response:
    """Handle WhatsApp webhook verification."""
    return whatsapp_handler.verify_webhook(request)

async def handle_whatsapp_message(request: Request) -> Response:
    """Handle incoming WhatsApp messages."""
    return await whatsapp_handler.process_webhook(request)