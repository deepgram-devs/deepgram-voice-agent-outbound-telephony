"""
Silence monitor — detects prolonged silence and prompts the user.

Uses Deepgram's InjectAgentMessage to check if the caller is still there.
The monitor runs a single timer that advances through attempts. Two events
from the session drive it:

  notify_agent_audio_done()    — Agent finished speaking. Start the timer.
  notify_user_started_speaking() — User is engaging. Reset attempts.

Note: AgentAudioDone fires when Deepgram finishes *sending* audio, not when
Twilio finishes *playing* it to the caller. The first attempt uses a long
timeout (60s) to avoid firing while the agent is still audibly speaking.

Usage:
    monitor = SilenceMonitor(connection, call_sid, on_timeout=end_call_cb)
    monitor.notify_agent_audio_done()       # Call on AgentAudioDone events
    monitor.notify_user_started_speaking()   # Call on UserStartedSpeaking events
    monitor.stop()                           # Cancel monitoring
"""

import asyncio
import logging
from typing import Callable, Awaitable

from deepgram.agent.v1 import AgentV1InjectAgentMessage

logger = logging.getLogger(__name__)

SILENCE_ATTEMPTS = [
    {"wait": 60, "message": "Are you still there?"},
    {"wait": 30, "message": "It seems like you may have stepped away. We'll try again another time. Goodbye."},
]
FINAL_WAIT = 5


class SilenceMonitor:
    """Monitors for prolonged silence and prompts the user."""

    def __init__(self, connection, call_sid: str, on_timeout: Callable[[], Awaitable]):
        """
        Args:
            connection: The Deepgram Voice Agent async connection
            call_sid: For logging
            on_timeout: Async callback invoked when all attempts are exhausted
        """
        self._connection = connection
        self._call_sid = call_sid
        self._on_timeout = on_timeout

        self._attempt_index = 0
        self._timer_task: asyncio.Task | None = None
        self._stopped = False

    def notify_agent_audio_done(self):
        """Agent finished speaking — start (or restart) the silence timer.

        Called on every AgentAudioDone event, whether from a normal agent
        response or from an injected silence prompt.
        """
        if self._stopped:
            return
        self._start_timer()

    def notify_user_started_speaking(self):
        """User started speaking — reset attempts and restart timer.

        Called on UserStartedSpeaking events. We use this instead of
        ConversationText because ConversationText arrives only after full
        transcription, meaning the timer could fire while the user is
        mid-sentence.
        """
        if self._stopped:
            return
        self._attempt_index = 0
        self._start_timer()

    def stop(self):
        """Stop monitoring. Safe to call multiple times."""
        self._stopped = True
        self._cancel_timer()

    def _start_timer(self):
        """Cancel any existing timer and start a new one for the current attempt."""
        self._cancel_timer()

        if self._attempt_index < len(SILENCE_ATTEMPTS):
            wait = SILENCE_ATTEMPTS[self._attempt_index]["wait"]
        elif self._attempt_index == len(SILENCE_ATTEMPTS):
            # All attempt messages delivered — final wait before hangup
            wait = FINAL_WAIT
        else:
            return

        self._timer_task = asyncio.create_task(self._timer(wait))

    def _cancel_timer(self):
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None

    async def _timer(self, seconds: float):
        """Wait, then fire the current attempt action."""
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return

        if self._stopped:
            return

        if self._attempt_index < len(SILENCE_ATTEMPTS):
            # Inject the prompt — agent will speak it
            attempt = SILENCE_ATTEMPTS[self._attempt_index]
            logger.info(
                f"[SILENCE:{self._call_sid}] "
                f"Attempt {self._attempt_index + 1}/{len(SILENCE_ATTEMPTS)}: {attempt['message']}"
            )
            try:
                await self._connection.send_inject_agent_message(
                    AgentV1InjectAgentMessage(message=attempt["message"])
                )
            except Exception as e:
                logger.error(f"[SILENCE:{self._call_sid}] Failed to inject message: {e}")
                return
            self._attempt_index += 1
            # Don't start the next timer here — wait for AgentAudioDone

        elif self._attempt_index == len(SILENCE_ATTEMPTS):
            # Final wait exhausted — end the call
            logger.info(f"[SILENCE:{self._call_sid}] No response after all attempts — ending call")
            try:
                await self._on_timeout()
            except Exception as e:
                logger.error(f"[SILENCE:{self._call_sid}] Error in timeout callback: {e}")
