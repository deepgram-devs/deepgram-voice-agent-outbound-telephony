"""
Outbound call manager - initiates calls via the Twilio REST API.

This module wraps the Twilio API for placing outbound calls.  When a call
is placed, Twilio dials the recipient and streams audio back to the server
via WebSocket.

The call flow:
  1. Server calls Twilio REST API with inline TwiML
  2. TwiML tells Twilio to open a WebSocket back to our /twilio endpoint
  3. Twilio dials the recipient's phone number
  4. When they pick up (or voicemail answers), audio flows over WebSocket
  5. Twilio's AMD runs in the background and POSTs result to /amd-result

The server must be publicly accessible (via Fly.io, ngrok, etc.) because
Twilio needs to open a WebSocket connection back to us.
"""
import logging

from twilio.rest import Client

from config import (
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_PHONE_NUMBER,
    SERVER_EXTERNAL_URL,
)

logger = logging.getLogger(__name__)


def place_call(to: str) -> str:
    """Place an outbound call via Twilio.

    Args:
        to: The phone number to call (E.164 format, e.g. +15551234567)

    Returns:
        The Twilio call SID (e.g. "CA...")

    Raises:
        ValueError: If required Twilio configuration is missing
        Exception: If the Twilio API call fails
    """
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER]):
        raise ValueError(
            "Missing Twilio configuration. Set TWILIO_ACCOUNT_SID, "
            "TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER in your .env file."
        )

    if not SERVER_EXTERNAL_URL:
        raise ValueError(
            "Missing SERVER_EXTERNAL_URL. Twilio needs a public URL to "
            "stream audio back. Set it in your .env file or use the setup wizard."
        )

    # Strip protocol prefix - TwiML needs a bare hostname for wss://
    host = SERVER_EXTERNAL_URL.replace("https://", "").replace("http://", "").rstrip("/")

    # Build the AMD callback URL
    amd_callback_url = f"{SERVER_EXTERNAL_URL.rstrip('/')}/amd-result"

    # Build inline TwiML that tells Twilio to stream audio to our WebSocket
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="wss://{host}/twilio" />
    </Connect>
</Response>"""

    logger.info(f"[CALL_MANAGER] Placing call to {to}")
    logger.info(f"[CALL_MANAGER] Audio stream -> wss://{host}/twilio")
    logger.info(f"[CALL_MANAGER] AMD callback -> {amd_callback_url}")

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    call = client.calls.create(
        to=to,
        from_=TWILIO_PHONE_NUMBER,
        twiml=twiml,
        # Answering Machine Detection (async)
        machine_detection="DetectMessageEnd",
        async_amd=True,
        async_amd_status_callback=amd_callback_url,
        async_amd_status_callback_method="POST",
    )

    logger.info(f"[CALL_MANAGER] Call initiated - SID: {call.sid}")
    return call.sid
