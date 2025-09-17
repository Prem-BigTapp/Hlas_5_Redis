import os
import logging
import requests

logger = logging.getLogger(__name__)

def send_whatsapp_message(recipient_number: str, message_body: str):
    """
    Sends a WhatsApp message to a specified recipient using the Meta API.
    """
    access_token = os.environ.get("META_ACCESS_TOKEN")
    phone_number_id = os.environ.get("META_PHONE_NUMBER_ID")

    if not phone_number_id or not access_token:
        logger.error("Environment variables META_PHONE_NUMBER_ID and/or META_ACCESS_TOKEN are not set.")
        return

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_number,
        "type": "text",
        "text": {
            "body": message_body
        }
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Message sent successfully to {recipient_number}. Response: {response.json()}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send message to {recipient_number}: {e}")
        if e.response is not None:
            logger.error(f"Response status code: {e.response.status_code}")
            logger.error(f"Response content: {e.response.text}")