"""
Outbound Telephony Voice Agent - Entry Point

Starts a Starlette web server that handles:
  - POST /make-call   -> Initiate an outbound call
  - WS   /twilio      -> Twilio audio stream
  - POST /amd-result  -> Twilio answering machine detection callback

Usage:
  python main.py

To place a test call:
  python make_call.py --to "+15551234567"
"""
import logging

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route, WebSocketRoute
from starlette.responses import PlainTextResponse

from config import SERVER_HOST, SERVER_PORT, SERVER_EXTERNAL_URL, DEEPGRAM_API_KEY
from telephony.routes import make_call, twilio_websocket, amd_result

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def dashboard(request):
    return PlainTextResponse(
        "Outbound Telephony Voice Agent is running.\n"
        "Use `python make_call.py --to \"+15551234567\"` to place a test call."
    )


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = Starlette(
    routes=[
        Route("/make-call", make_call, methods=["POST"]),
        WebSocketRoute("/twilio", twilio_websocket),
        Route("/amd-result", amd_result, methods=["POST"]),
        Route("/", dashboard),
    ],
)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(f"Deepgram API key: {'configured' if DEEPGRAM_API_KEY else 'MISSING'}")
    if SERVER_EXTERNAL_URL:
        logger.info(f"External URL: {SERVER_EXTERNAL_URL}")
        logger.info(f"Make-call endpoint: {SERVER_EXTERNAL_URL}/make-call")
    else:
        logger.info("WARNING: No SERVER_EXTERNAL_URL set - Twilio cannot stream audio back")
        logger.info("Set SERVER_EXTERNAL_URL to your ngrok/zrok URL or Fly.io URL")

    uvicorn.run(
        app,
        host=SERVER_HOST,
        port=SERVER_PORT,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
