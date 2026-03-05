"""
VoiceAgentSession - manages a single Deepgram Voice Agent connection for one phone call.

This is the core of the audio pipeline.  It bridges two WebSocket connections:

  Twilio WebSocket  <->  VoiceAgentSession  <->  Deepgram Voice Agent API

Audio flow:
  1. Twilio sends mulaw audio as base64 JSON -> we decode -> send raw bytes to Deepgram
  2. Deepgram sends raw mulaw bytes back   -> we encode to base64 -> send JSON to Twilio

For outbound calls, the session also handles:
  - AMD (answering machine detection) - buffers audio until AMD result arrives,
    then branches to live conversation or voicemail delivery
  - Lead context injection - the agent's system prompt is built dynamically
    with lead data from the POST /make-call request
  - Barge-in (sending Twilio "clear" events when the user starts speaking)
  - Function call dispatch (routing to backend/lead_service.py)
  - Transcript logging
  - Lifecycle management (connect, run, cleanup)

AMD approach (buffer-until-result):
  When a call connects, we don't know yet if it's a human or voicemail.
  Twilio's async AMD runs in the background and POSTs the result to /amd-result.
  Meanwhile, we buffer incoming Twilio audio. When the AMD result arrives:
    - Human: connect to Deepgram Voice Agent API, flush buffered audio, start conversation
    - Voicemail: deliver pre-recorded message via Deepgram Aura-2 TTS
  The ~2-4 second pause before the agent speaks is natural for outbound calls
  where the person says "Hello?" and waits.
"""
import asyncio
import base64
import json
import logging

from starlette.websockets import WebSocket

from deepgram import AsyncDeepgramClient
from deepgram.core.pydantic_utilities import parse_obj_as
from deepgram.agent.v1 import (
    AgentV1SettingsApplied,
    AgentV1FunctionCallRequest,
    AgentV1ConversationText,
    AgentV1UserStartedSpeaking,
    AgentV1AgentAudioDone,
    AgentV1Error,
    AgentV1Warning,
    AgentV1SendFunctionCallResponse,
)
from deepgram.agent.v1.socket_client import V1SocketClientResponse

from voice_agent.agent_config import get_agent_config
from voice_agent.silence_monitor import SilenceMonitor

logger = logging.getLogger(__name__)


class VoiceAgentSession:
    """Manages one Deepgram Voice Agent session for the lifetime of a phone call."""

    def __init__(self, twilio_ws: WebSocket, call_sid: str, stream_sid: str, lead_context: dict):
        self.twilio_ws = twilio_ws
        self.call_sid = call_sid
        self.stream_sid = stream_sid
        self.lead_context = lead_context

        # Deepgram connection state
        self._client = None
        self._connection = None
        self._context_manager = None

        # AMD state
        self._amd_result = asyncio.Event()
        self._amd_answered_by = None  # Set by signal_amd_result()
        self._audio_buffer = []       # Buffer Twilio audio until AMD resolves

        # Coordination
        self._settings_applied = asyncio.Event()
        self._cleanup_done = False
        self._voicemail_done = asyncio.Event()

        # Twilio audio loop mode: "buffering" -> "forwarding" -> "discarding"
        #   buffering:   store audio in _audio_buffer (during AMD wait)
        #   forwarding:  send audio to Deepgram (live conversation)
        #   discarding:  keep reading but ignore audio (during voicemail delivery)
        self._audio_mode = "buffering"

        # Tasks
        self._listen_task = None
        self._audio_task = None

        # Silence detection
        self._silence_monitor = None

    # ------------------------------------------------------------------
    # AMD signaling
    # ------------------------------------------------------------------

    def signal_amd_result(self, answered_by: str):
        """Called by the /amd-result route when Twilio's AMD detection completes.

        Args:
            answered_by: Twilio's AnsweredBy value - "human", "machine_end_beep",
                         "machine_end_silence", "machine_end_other", "unknown", or "fax"
        """
        self._amd_answered_by = answered_by
        self._amd_result.set()
        logger.info(f"[SESSION:{self.call_sid}] AMD result: {answered_by}")

        # Late AMD result — voice agent is already running
        if self._connection is not None and answered_by.startswith("machine_"):
            logger.info(f"[SESSION:{self.call_sid}] Late AMD machine detection — switching to voicemail")
            asyncio.create_task(self._switch_to_voicemail())

    def _is_voicemail(self) -> bool:
        """Check if AMD detected a voicemail/answering machine."""
        if self._amd_answered_by is None:
            return False
        return self._amd_answered_by.startswith("machine_")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self):
        """Wait for AMD result, then either start the voice agent or deliver voicemail."""
        logger.info(f"[SESSION:{self.call_sid}] Waiting for AMD result...")

        # Start the Twilio audio loop — it begins in "buffering" mode and
        # runs for the entire lifetime of the Twilio WebSocket.  We never
        # cancel this task; we change self._audio_mode to control behavior.
        self._audio_task = asyncio.create_task(self._twilio_audio_loop())

        # Wait for AMD result (typically 2-4 seconds)
        try:
            await asyncio.wait_for(self._amd_result.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            # No AMD result received - treat as human (safe default)
            logger.warning(f"[SESSION:{self.call_sid}] AMD timeout - treating as human")
            self._amd_answered_by = "unknown"

        if self._is_voicemail():
            # Voicemail path - deliver TTS message, no Voice Agent API needed
            logger.info(f"[SESSION:{self.call_sid}] Voicemail detected - delivering message")
            await self._deliver_voicemail()
            return

        # Human path - connect to Deepgram Voice Agent API
        logger.info(f"[SESSION:{self.call_sid}] Human detected - starting voice agent")
        await self._connect_deepgram()

    async def _connect_deepgram(self):
        """Connect to Deepgram Voice Agent API and configure the agent."""
        logger.info(f"[SESSION:{self.call_sid}] Connecting to Deepgram Voice Agent API")

        # Create client and open WebSocket connection.
        self._client = AsyncDeepgramClient()
        self._context_manager = self._client.agent.v1.connect()
        self._connection = await self._context_manager.__aenter__()

        # Start our own receive loop instead of connection.start_listening().
        self._listen_task = asyncio.create_task(self._listen_loop())

        # Send agent configuration with lead context injected into the prompt.
        config = get_agent_config(self.lead_context)
        await self._connection.send_settings(config)

        # Wait for Deepgram to acknowledge the settings before forwarding audio.
        try:
            await asyncio.wait_for(self._settings_applied.wait(), timeout=5.0)
            logger.info(f"[SESSION:{self.call_sid}] Settings applied - ready for audio")
        except asyncio.TimeoutError:
            logger.error(f"[SESSION:{self.call_sid}] Timeout waiting for settings to be applied")
            raise

        # Start silence monitor — timer begins on the first AgentAudioDone
        # (which fires after the agent speaks its greeting)
        self._silence_monitor = SilenceMonitor(
            connection=self._connection,
            call_sid=self.call_sid,
            on_timeout=self._handle_silence_timeout,
        )

        # Discard buffered audio - it's just the person's "Hello?" and silence
        # during AMD detection.  Flushing it would trigger barge-in and cut off
        # the agent's greeting.  The greeting re-establishes the conversation.
        if self._audio_buffer:
            logger.info(f"[SESSION:{self.call_sid}] Discarding {len(self._audio_buffer)} buffered audio chunks (AMD wait)")
            self._audio_buffer.clear()

    async def _deliver_voicemail(self):
        """Deliver voicemail via Deepgram Aura-2 TTS."""
        self._audio_mode = "discarding"

        from voice_agent.voicemail import deliver_voicemail
        playback_duration = await deliver_voicemail(self.twilio_ws, self.stream_sid, self.lead_context)

        # Wait for Twilio to finish playing the audio before hanging up.
        # Audio is sent to Twilio faster than real-time, so we need to wait
        # for the estimated playback duration plus a small buffer.
        await self._end_call_after_delay(delay=playback_duration + 2)

    async def _switch_to_voicemail(self):
        """Tear down the voice agent session and deliver voicemail instead.

        Called when AMD detects a machine AFTER we've already connected to
        Deepgram and started the voice agent (late AMD result).

        The Twilio audio loop keeps running in "discarding" mode so the
        WebSocket stays open — ASGI/uvicorn closes the WebSocket when
        the handler returns, so we must keep reading from it.
        """
        # Switch audio loop to discard mode — stops forwarding to Deepgram
        # but keeps reading from Twilio so the WebSocket stays alive.
        self._audio_mode = "discarding"

        # Clear Twilio's audio buffer so the agent's greeting stops playing
        # immediately. Without this, voicemail audio queues behind the
        # greeting and our playback delay calculation would be wrong.
        await self.twilio_ws.send_json({"event": "clear", "streamSid": self.stream_sid})

        # Stop silence monitor
        if self._silence_monitor:
            self._silence_monitor.stop()

        # Cancel only the Deepgram listen task
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        # Close the Deepgram connection
        if self._context_manager:
            try:
                await self._context_manager.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"[SESSION:{self.call_sid}] Error closing Deepgram during switchover: {e}")
        self._context_manager = None
        self._connection = None
        self._client = None

        # Deliver voicemail via standalone TTS.
        # The Twilio WebSocket is still open — we can send audio to it.
        from voice_agent.voicemail import deliver_voicemail
        playback_duration = await deliver_voicemail(self.twilio_ws, self.stream_sid, self.lead_context)

        await self._end_call_after_delay(delay=playback_duration + 2)
        self._voicemail_done.set()

    async def run(self):
        """Forward audio from Twilio to Deepgram until the call ends.

        Call this after start().  It blocks until the Twilio WebSocket closes
        (caller hangs up) or an error occurs.

        If the call went to voicemail, start() already handled everything
        and this returns immediately.
        """
        if self._is_voicemail():
            return

        # Switch audio loop from buffering to forwarding mode
        self._audio_mode = "forwarding"

        # Wait for either the audio loop or the Deepgram listener to finish.
        # The audio loop ends when the Twilio WebSocket closes (caller hangs
        # up or we hang up via _end_call_after_delay).  The listen loop ends
        # when the Deepgram connection closes.
        tasks = [self._audio_task]
        if self._listen_task:
            tasks.append(self._listen_task)

        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If the Deepgram listener finished first (e.g. late AMD switchover
        # cancelled it), wait for voicemail delivery before cleaning up.
        if self._voicemail_done.is_set() or self._audio_mode == "discarding":
            await self._voicemail_done.wait()

        # Cancel whatever is still running.
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        logger.info(f"[SESSION:{self.call_sid}] Call ended")

    async def cleanup(self):
        """Release all resources.  Safe to call multiple times."""
        if self._cleanup_done:
            return
        self._cleanup_done = True

        logger.info(f"[SESSION:{self.call_sid}] Cleaning up")

        # Stop silence monitor
        if self._silence_monitor:
            self._silence_monitor.stop()

        # Cancel tasks
        for task in [self._audio_task, self._listen_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close the Deepgram connection
        if self._context_manager:
            try:
                await self._context_manager.__aexit__(None, None, None)
            except Exception as e:
                logger.debug(f"[SESSION:{self.call_sid}] Error during Deepgram cleanup: {e}")

        self._connection = None
        self._client = None
        logger.info(f"[SESSION:{self.call_sid}] Cleanup complete")

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------

    async def _listen_loop(self):
        """Read messages from Deepgram, skipping any the SDK can't parse.

        This replaces connection.start_listening() which crashes the entire
        loop when the API sends a message type the SDK doesn't recognize
        (e.g. "History").  We read raw frames, try to parse them, and
        skip unrecognized types instead of dying.
        """
        try:
            async for raw_message in self._connection._websocket:
                try:
                    if isinstance(raw_message, bytes):
                        parsed = raw_message
                    else:
                        json_data = json.loads(raw_message)
                        parsed = parse_obj_as(V1SocketClientResponse, json_data)
                except Exception:
                    # Unknown message type - log and continue.
                    msg_type = json_data.get("type", "unknown") if isinstance(raw_message, str) else "binary"
                    logger.debug(f"[SESSION:{self.call_sid}] Skipping unrecognized message type: {msg_type}")
                    continue

                # Dispatch the parsed message.
                if isinstance(parsed, AgentV1SettingsApplied):
                    self._settings_applied.set()
                else:
                    await self._handle_message(parsed)
        except Exception as e:
            logger.info(f"[SESSION:{self.call_sid}] Deepgram listen loop ended: {e}")
        finally:
            logger.info(f"[SESSION:{self.call_sid}] Deepgram connection closed")

    async def _handle_message(self, message):
        """Process a single message from the Deepgram Voice Agent."""
        try:
            # Binary audio -> forward to Twilio
            if isinstance(message, bytes):
                audio_b64 = base64.b64encode(message).decode("utf-8")
                await self.twilio_ws.send_json({
                    "event": "media",
                    "streamSid": self.stream_sid,
                    "media": {"payload": audio_b64},
                })

            # Function call request -> dispatch to backend
            elif isinstance(message, AgentV1FunctionCallRequest):
                await self._handle_function_call(message)

            # Transcript text -> log it
            elif isinstance(message, AgentV1ConversationText):
                logger.info(f"[SESSION:{self.call_sid}] {message.role.upper()}: {message.content}")

            # User started speaking -> tell Twilio to stop playing agent audio
            elif isinstance(message, AgentV1UserStartedSpeaking):
                logger.info(f"[SESSION:{self.call_sid}] User started speaking")
                if self._silence_monitor:
                    self._silence_monitor.notify_user_started_speaking()
                await self.twilio_ws.send_json({
                    "event": "clear",
                    "streamSid": self.stream_sid,
                })

            # Agent finished speaking
            elif isinstance(message, AgentV1AgentAudioDone):
                logger.debug(f"[SESSION:{self.call_sid}] Agent finished speaking")
                if self._silence_monitor:
                    self._silence_monitor.notify_agent_audio_done()

            # Errors and warnings
            elif isinstance(message, AgentV1Error):
                logger.error(f"[SESSION:{self.call_sid}] Agent error: {message.description}")
            elif isinstance(message, AgentV1Warning):
                logger.warning(f"[SESSION:{self.call_sid}] Agent warning: {message.description}")

        except Exception as e:
            logger.error(f"[SESSION:{self.call_sid}] Error handling message: {e}")

    # ------------------------------------------------------------------
    # Function calls
    # ------------------------------------------------------------------

    async def _handle_function_call(self, event: AgentV1FunctionCallRequest):
        """Dispatch a function call from the agent to the backend service."""
        if not event.functions:
            return

        func = event.functions[0]
        function_name = func.name
        call_id = func.id
        args = json.loads(func.arguments) if func.arguments else {}

        logger.info(f"[SESSION:{self.call_sid}] Function call: {function_name}({args})")

        try:
            from voice_agent.function_handlers import dispatch_function

            result = await dispatch_function(function_name, args)
            logger.info(f"[SESSION:{self.call_sid}] Function result: {function_name} -> {json.dumps(result)}")
        except Exception as e:
            logger.error(f"[SESSION:{self.call_sid}] Function error: {function_name} -> {e}")
            result = {"error": str(e)}

        # Send the result back to Deepgram so the agent can incorporate it
        # into its next response.
        response = AgentV1SendFunctionCallResponse(
            type="FunctionCallResponse",
            name=function_name,
            content=json.dumps(result),
            id=call_id,
        )
        await self._connection.send_function_call_response(response)

        # If this was end_call, wait for the agent's goodbye audio to play
        # then hang up.
        if function_name == "end_call":
            asyncio.create_task(self._end_call_after_delay())

    # ------------------------------------------------------------------
    # Silence handling
    # ------------------------------------------------------------------

    async def _handle_silence_timeout(self):
        """Called by SilenceMonitor when all silence attempts are exhausted."""
        logger.info(f"[SESSION:{self.call_sid}] Silence timeout — ending call")
        await self._end_call_after_delay(delay=1)

    # ------------------------------------------------------------------
    # Call termination
    # ------------------------------------------------------------------

    async def _end_call_after_delay(self, delay: int = 3):
        """Wait for the agent's goodbye audio to finish, then hang up.

        Uses the Twilio REST API to set the call status to "completed".
        """
        await asyncio.sleep(delay)

        logger.info(f"[SESSION:{self.call_sid}] Hanging up call")

        from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            try:
                from twilio.rest import Client
                client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                await asyncio.to_thread(
                    client.calls(self.call_sid).update,
                    status="completed",
                )
                logger.info(f"[SESSION:{self.call_sid}] Twilio call completed")
            except Exception as e:
                logger.error(f"[SESSION:{self.call_sid}] Failed to complete Twilio call: {e}")

        try:
            await self.twilio_ws.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Audio forwarding
    # ------------------------------------------------------------------

    async def _twilio_audio_loop(self):
        """Read from the Twilio WebSocket for the entire lifetime of the call.

        This single long-lived loop handles all phases of the call by
        checking self._audio_mode to decide what to do with incoming audio:

          "buffering"   — Store audio in self._audio_buffer (during AMD wait)
          "forwarding"  — Send audio to Deepgram (live conversation)
          "discarding"  — Ignore audio (during voicemail delivery)

        We never cancel this task.  Cancelling would cause run() to return,
        which would cause the ASGI WebSocket handler to return, which would
        make uvicorn close the Twilio WebSocket — even if we still need it
        for voicemail delivery.
        """
        try:
            while True:
                message = await self.twilio_ws.receive_text()
                data = json.loads(message)

                if data.get("event") == "media":
                    if self._audio_mode == "buffering":
                        payload = data["media"]["payload"]
                        audio_bytes = base64.b64decode(payload)
                        self._audio_buffer.append(audio_bytes)
                    elif self._audio_mode == "forwarding":
                        payload = data["media"]["payload"]
                        audio_bytes = base64.b64decode(payload)
                        if self._connection:
                            await self._connection.send_media(audio_bytes)
                    # "discarding" — do nothing

                elif data.get("event") == "stop":
                    logger.info(f"[SESSION:{self.call_sid}] Twilio stream stopped")
                    break

                # Ignore other events (connected, start, mark, etc.)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            # WebSocket closed — caller hung up or we ended the call.
            logger.info(f"[SESSION:{self.call_sid}] Twilio WebSocket closed: {e}")
