"""
Voicemail delivery via Deepgram Aura-2 TTS.

When answering machine detection determines the call went to voicemail,
this module generates a personalized voicemail message using Deepgram's
text-to-speech API and streams it to Twilio over the existing WebSocket.

Key design choices:
  - Uses Deepgram Aura-2 TTS (not the Voice Agent API) since there's
    no need for a full conversational agent on a voicemail
  - Uses the same voice model as the live agent for consistency
  - Streams audio chunks as they arrive for minimal latency
  - Calls update_lead directly (not through the voice agent) since there's
    no Voice Agent API connection in the voicemail path
"""
import base64
import logging

from deepgram import DeepgramClient
from starlette.websockets import WebSocket

from config import VOICE_MODEL

logger = logging.getLogger(__name__)


def _build_voicemail_text(lead_context: dict) -> str:
    """Build a personalized voicemail message from lead context.

    Keeps it under 20 seconds when spoken - brief and professional.
    """
    first_name = lead_context.get("first_name", "there")
    return (
        f"Hi {first_name}, this is an automated assistant calling on behalf of "
        f"Prestige Home Insurance. We're following up on the homeowners insurance "
        f"quote you recently requested through our website. We'd love to connect "
        f"you with one of our licensed agents to go over your options. We'll try "
        f"you again soon, or you can visit our website at your convenience. "
        f"Thanks, and have a great day."
    )


async def deliver_voicemail(
    twilio_ws: WebSocket,
    stream_sid: str,
    lead_context: dict,
) -> float:
    """Generate voicemail audio via Deepgram TTS and stream to Twilio.

    Args:
        twilio_ws: The Twilio WebSocket connection
        stream_sid: The Twilio stream SID for media messages
        lead_context: Lead data dict for personalizing the voicemail

    Returns:
        Estimated playback duration in seconds (mulaw 8kHz = 8000 bytes/sec).
    """
    voicemail_text = _build_voicemail_text(lead_context)
    logger.info(f"[VOICEMAIL] Delivering voicemail: {voicemail_text[:60]}...")

    try:
        # Use synchronous Deepgram client for TTS generation.
        # speak.v1.audio.generate() returns an Iterator[bytes] of audio chunks.
        client = DeepgramClient()

        # Stream audio chunks to Twilio as they arrive
        chunk_count = 0
        total_bytes = 0
        for chunk in client.speak.v1.audio.generate(
            text=voicemail_text,
            model=VOICE_MODEL,
            encoding="mulaw",
            sample_rate=8000,
            container="none",
        ):
            if chunk:
                audio_b64 = base64.b64encode(chunk).decode("utf-8")
                await twilio_ws.send_json({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": audio_b64},
                })
                chunk_count += 1
                total_bytes += len(chunk)

        # mulaw 8kHz mono = 8000 bytes per second of audio
        playback_duration = total_bytes / 8000
        logger.info(f"[VOICEMAIL] Delivered {chunk_count} audio chunks ({playback_duration:.1f}s of audio)")

    except Exception as e:
        logger.error(f"[VOICEMAIL] Error delivering voicemail: {e}")
        raise

    # Log the voicemail delivery as a lead update
    # (do this before returning so the CRM update happens regardless)
    try:
        from backend.lead_service import lead_service
        lead_id = lead_context.get("lead_id", "unknown")
        first_name = lead_context.get("first_name", "Unknown")
        last_name = lead_context.get("last_name", "")

        await lead_service.update_lead(
            lead_id=lead_id,
            call_outcome="no_answer_voicemail_left",
            disposition="qualified",
            call_summary=(
                f"Call to {first_name} {last_name} went to voicemail. "
                f"Left automated message referencing their homeowners insurance "
                f"quote request for Prestige Home Insurance."
            ),
        )
    except Exception as e:
        logger.error(f"[VOICEMAIL] Error updating lead after voicemail: {e}")

    return playback_duration
