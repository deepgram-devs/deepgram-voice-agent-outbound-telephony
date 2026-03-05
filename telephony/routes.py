"""
Telephony routes - handles outbound call initiation, Twilio audio stream, and AMD callbacks.

Three endpoints:

  POST /make-call
    Initiates an outbound call. Accepts a phone number and optional lead context.
    Validates endpoint secret via Authorization: Bearer header.

  WS /twilio
    Receives the audio stream from Twilio after the call connects.
    Creates a VoiceAgentSession and bridges audio to/from Deepgram.

  POST /amd-result
    Receives Twilio's answering machine detection result.
    Signals the active session to branch between human and voicemail paths.
"""
import asyncio
import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.websockets import WebSocket

from config import ENDPOINT_SECRET
from voice_agent.session import VoiceAgentSession

logger = logging.getLogger(__name__)

# Active sessions, keyed by call_sid.  Used by AMD callback to signal sessions.
active_sessions: dict[str, VoiceAgentSession] = {}

# Pending lead contexts, keyed by call_sid.  Set when placing the call,
# consumed when the Twilio WebSocket connects.
_pending_leads: dict[str, dict] = {}


def _check_endpoint_secret(request: Request) -> bool:
    """Validate the endpoint secret from the Authorization header.

    If ENDPOINT_SECRET is not configured, all requests pass (local dev mode).
    """
    if not ENDPOINT_SECRET:
        return True
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:] == ENDPOINT_SECRET
    return False


async def make_call(request: Request) -> Response:
    """Initiate an outbound call.

    Request body:
      {
        "to": "+15551234567",           # Required - phone number to call
        "lead": { ... }                 # Optional - lead context data
      }

    If "lead" is omitted, uses the default mock lead template.

    Headers:
      Authorization: Bearer <ENDPOINT_SECRET>   # Required when ENDPOINT_SECRET is configured
    """
    # Validate endpoint secret
    if not _check_endpoint_secret(request):
        return JSONResponse(
            {"error": "Unauthorized. Provide a valid endpoint secret via Authorization: Bearer <secret>"},
            status_code=401,
        )

    # Parse request body
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    phone_number = body.get("to")
    if not phone_number:
        return JSONResponse(
            {"error": "Missing required field: 'to' (phone number in E.164 format)"},
            status_code=400,
        )

    # Build lead context
    lead_data = body.get("lead")
    if lead_data:
        from backend.lead_service import build_lead_from_dict
        lead = build_lead_from_dict(phone_number, lead_data)
    else:
        from backend.lead_service import build_default_lead
        lead = build_default_lead(phone_number)

    # Convert lead to dict for session context
    lead_context = {
        "lead_id": lead.lead_id,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "email": lead.email,
        "property_address": {
            "street": lead.property_address.street,
            "city": lead.property_address.city,
            "state": lead.property_address.state,
            "zip": lead.property_address.zip,
        },
        "property_type": lead.property_type,
        "year_built": lead.year_built,
        "square_footage": lead.square_footage,
        "current_insurance_status": lead.current_insurance_status,
        "desired_coverage_start": lead.desired_coverage_start,
        "quote_submitted_at": lead.quote_submitted_at,
        "source": lead.source,
    }

    logger.info(f"[TELEPHONY] Initiating call to {phone_number} (lead: {lead.lead_id})")

    # Place the call via Twilio
    try:
        from telephony.call_manager import place_call
        call_sid = await asyncio.to_thread(place_call, phone_number)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"[TELEPHONY] Failed to place call: {e}")
        return JSONResponse(
            {"error": f"Failed to place call: {e}"},
            status_code=500,
        )

    # Store lead context for when the Twilio WebSocket connects
    _pending_leads[call_sid] = lead_context

    return JSONResponse({
        "call_sid": call_sid,
        "status": "initiated",
        "lead_id": lead.lead_id,
    })


async def twilio_websocket(websocket: WebSocket):
    """Handle a Twilio audio stream for an outbound call.

    Protocol:
      1. Twilio opens the WebSocket and sends a "connected" event
      2. Twilio sends a "start" event with callSid and streamSid
      3. Twilio sends "media" events with base64-encoded mulaw audio
      4. We send "media" events back with agent audio
      5. Twilio sends a "stop" event when the call ends

    This handler creates a VoiceAgentSession and delegates all audio
    processing to it.  The session handles AMD branching, the Deepgram
    connection, audio bridging, function calls, and cleanup.
    """
    await websocket.accept()
    logger.info("[TELEPHONY] WebSocket connected")

    call_sid = None
    stream_sid = None
    session = None

    try:
        # Wait for the Twilio "start" event to get call metadata.
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)

            if data.get("event") == "start":
                call_sid = data["start"].get("callSid", "unknown")
                stream_sid = data["start"].get("streamSid", "unknown")
                logger.info(f"[TELEPHONY] Call started - callSid={call_sid}")
                break
            elif data.get("event") == "connected":
                continue

        # Retrieve the lead context stored when the call was placed
        lead_context = _pending_leads.pop(call_sid, {})
        if not lead_context:
            logger.warning(f"[TELEPHONY] No lead context found for call {call_sid} - using defaults")
            from backend.lead_service import build_default_lead
            lead = build_default_lead("+10000000000")
            lead_context = {
                "lead_id": lead.lead_id,
                "first_name": lead.first_name,
                "last_name": lead.last_name,
                "phone": lead.phone,
                "email": lead.email,
                "property_address": {
                    "street": lead.property_address.street,
                    "city": lead.property_address.city,
                    "state": lead.property_address.state,
                    "zip": lead.property_address.zip,
                },
                "property_type": lead.property_type,
                "year_built": lead.year_built,
                "square_footage": lead.square_footage,
                "current_insurance_status": lead.current_insurance_status,
                "desired_coverage_start": lead.desired_coverage_start,
                "quote_submitted_at": lead.quote_submitted_at,
                "source": lead.source,
            }

        # Create and start the voice agent session.
        session = VoiceAgentSession(websocket, call_sid, stream_sid, lead_context)
        active_sessions[call_sid] = session

        await session.start()
        await session.run()

    except Exception as e:
        logger.error(f"[TELEPHONY] Error in call {call_sid}: {e}")
    finally:
        if session:
            await session.cleanup()
        if call_sid and call_sid in active_sessions:
            del active_sessions[call_sid]
        logger.info(f"[TELEPHONY] Call {call_sid} ended")


async def amd_result(request: Request) -> Response:
    """Receive Twilio's answering machine detection result.

    Twilio POSTs here with form data including:
      - CallSid: The call this result is for
      - AnsweredBy: "human", "machine_end_beep", "machine_end_silence",
                    "machine_end_other", "unknown", or "fax"

    We look up the active session by CallSid and signal it with the result.
    """
    form_data = await request.form()
    call_sid = form_data.get("CallSid", "")
    answered_by = form_data.get("AnsweredBy", "unknown")

    logger.info(f"[TELEPHONY] AMD result for {call_sid}: {answered_by}")

    session = active_sessions.get(call_sid)
    if session:
        session.signal_amd_result(answered_by)
    else:
        logger.warning(f"[TELEPHONY] No active session for AMD result (call {call_sid})")

    return Response(status_code=204)
