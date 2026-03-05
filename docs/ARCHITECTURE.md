# Architecture

This document describes the architecture of the outbound telephony voice agent, from the high-level system design down to individual component responsibilities.

## System Overview

The system places outbound phone calls and connects them to an AI voice agent. Four services work together:

1. **Twilio** - Telephony provider. Dials phone numbers, converts audio to a digital stream, and sends it to our server over WebSocket. Also provides answering machine detection (AMD).
2. **Application Server** - This codebase. Initiates calls, bridges Twilio audio to Deepgram, handles function calls, manages AMD branching, and delivers voicemail.
3. **Deepgram Voice Agent API** - AI pipeline for live conversations. Transcribes speech, runs an LLM, and synthesizes a voice response. All three steps happen in a single managed WebSocket connection.
4. **Deepgram Standalone TTS (Aura-2)** - Used only for voicemail delivery. Generates audio from text without the full Voice Agent API pipeline.

```
                 ┌─────────────────────┐
                 │  External Trigger   │
                 │  (CRM, CLI, API)    │
                 └──────────┬──────────┘
                            │ POST /make-call
                            ▼
┌──────────┐     ┌─────────────────────┐     ┌──────────────────────┐
│          │     │  Application Server │     │  Deepgram Voice      │
│  Phone   │◄───►│      (Starlette)    │────►│  Agent API           │
│  (Call)  │     │                     │◄────│  (STT → LLM → TTS)   │
│          │     │                     │     │                      │
└──────────┘     └──────────┬──────────┘     └──────────────────────┘
   via Twilio               │
                 ┌──────────▼──────────┐     ┌──────────────────────┐
                 │   Backend Service   │     │  Deepgram Aura-2 TTS │
                 │ (Lead/CRM API)      │     │  (voicemail only)    │
                 └─────────────────────┘     └──────────────────────┘
```

## Call Flow

### 1. Call Initiation

When an external system triggers an outbound call:

```
External System ─► POST /make-call──► Application Server
                                        │
                                   Validates endpoint secret
                                   Builds lead context
                                   Stores in _pending_leads[call_sid]
                                        │
                                   Calls Twilio REST API:
                                     client.calls.create(
                                       to="+15551234567",
                                       twiml=<Connect><Stream url="wss://server/twilio"/>,
                                       machine_detection="DetectMessageEnd",
                                       async_amd=True,
                                       async_amd_status_callback=".../amd-result"
                                     )
                                        │
                  Twilio ◄──────────────┘
                    │
                    │ Dials the phone number
                    │ When answered: opens WebSocket to wss://server/twilio
                    │ Starts streaming audio immediately
                    │ Runs AMD in background
                    ▼
             Application Server
```

### 2. Session Setup with AMD Branching

When the Twilio WebSocket connects:

```
1. Server accepts WebSocket connection
2. Server waits for Twilio "start" event → extracts callSid, streamSid
3. Server retrieves lead context from _pending_leads[callSid]
4. Server creates a VoiceAgentSession with lead context
5. Session starts a single audio loop that runs for the entire call
   (begins in "buffering" mode, storing incoming audio in memory)
6. Session waits for AMD result (up to 20 seconds)
7. AMD result arrives via POST /amd-result:
   - "human" or "unknown" → Human path (connect to Deepgram Voice Agent API)
   - "machine_end_*" → Voicemail path (deliver TTS message)
```

If AMD detects a machine *after* the voice agent has already started (late AMD result — the timeout is generous because some voicemail greetings are long), the session tears down the Deepgram connection and switches to voicemail delivery mid-call.

### 3. Human Path: Voice Agent Conversation

If AMD detects a human (or unknown):

```
1. Session opens a WebSocket to Deepgram Voice Agent API
2. Session sends agent configuration (prompt with lead data, functions, audio settings)
3. Session waits for SettingsApplied acknowledgment from Deepgram
4. Session starts the silence monitor (see below)
5. Session discards buffered audio (greeting re-establishes the conversation)
6. Audio loop switches from "buffering" to "forwarding" mode
7. Agent speaks its greeting (personalized with lead name)
8. Full conversation proceeds with function calls
```

### 4. Voicemail Path: TTS Delivery

If AMD detects a voicemail (either before or after the voice agent starts):

```
1. Audio loop switches to "discarding" mode (keeps reading but ignores audio)
2. If the voice agent was already running: Deepgram connection is closed,
   silence monitor is stopped
3. Session calls deliver_voicemail() from voicemail.py
4. voicemail.py builds a personalized message using lead context
5. voicemail.py uses Deepgram Aura-2 TTS (client.speak.v1.audio.generate)
   to generate mulaw audio chunks
6. Each chunk is base64-encoded and sent to Twilio over WebSocket
7. After delivery, update_lead is called with "no_answer_voicemail_left"
8. Session hangs up the call via Twilio REST API
```

The audio loop stays alive in "discarding" mode throughout voicemail delivery. This is important because the ASGI framework closes the WebSocket when the handler returns — if the audio loop ended, the Twilio connection would close before voicemail delivery finishes.

### 5. Audio Pipeline (Human Path)

During a live conversation, two streams run simultaneously:

```
Twilio → Server → Deepgram (caller's voice)
─────────────────────────────────────────────
1. Twilio sends JSON: {"event":"media","media":{"payload":"<base64 mulaw>"}}
2. Server decodes base64 → raw mulaw bytes
3. Server sends raw bytes to Deepgram WebSocket

Deepgram → Server → Twilio (agent's voice)
─────────────────────────────────────────────
1. Deepgram sends raw mulaw bytes
2. Server encodes to base64
3. Server sends JSON: {"event":"media","streamSid":"...","media":{"payload":"<base64>"}}
4. Twilio plays audio to caller
```

### 6. Function Calls

When the agent decides to use a tool:

```
1. Deepgram sends FunctionCallRequest event
   (function name, arguments, call ID)
2. Server dispatches to function_handlers.py
3. function_handlers.py calls the backend lead service
4. Server sends FunctionCallResponse back to Deepgram
   (call ID, result as JSON string)
5. Deepgram's LLM incorporates the result into its next response
6. Deepgram sends the spoken response as audio
```

### 7. Barge-In

When the caller starts speaking while the agent is talking:

```
1. Deepgram detects user speech → sends UserStartedSpeaking event
2. Server sends {"event":"clear","streamSid":"..."} to Twilio
3. Twilio immediately stops playing agent audio
4. Deepgram processes the caller's speech normally
```

### 8. Silence Detection

If the caller stops responding, the silence monitor prompts them:

```
1. Agent finishes speaking → silence timer starts
2. 60 seconds of silence → agent says "Are you still there?"
3. 30 more seconds → agent says goodbye
4. 5 more seconds → session ends the call
```

The first timeout is intentionally long (60s) because `AgentAudioDone` fires when Deepgram finishes *sending* audio, not when Twilio finishes *playing* it. A shorter timeout would fire while the agent is still audibly speaking.

The monitor uses Deepgram's `InjectAgentMessage` to make the agent speak each prompt. If the caller speaks at any point, the monitor resets. The timer restarts after each `AgentAudioDone` event (when the agent finishes speaking a prompt or a normal response).

### 9. Call End

```
1. Agent calls end_call function → server waits 3 seconds for goodbye audio
2. Server updates the Twilio call status to "completed" via REST API
3. Twilio closes the WebSocket
4. VoiceAgentSession.cleanup() runs:
   - Cancels async tasks (audio forwarding, Deepgram listener)
   - Closes Deepgram WebSocket connection
   - Removes session from active sessions dict
```

Or if the caller hangs up first:

```
1. Twilio sends "stop" event and closes WebSocket
2. Server detects WebSocket closure
3. Same cleanup process
```

## Component Details

### `main.py` - Entry Point

Creates the Starlette application with four routes and starts uvicorn. Configures logging.

Routes:
- `POST /make-call` - Initiate an outbound call
- `WS /twilio` - Handle audio stream
- `POST /amd-result` - Receive AMD callback
- `GET /` - Dashboard (health check)

### `config.py` - Configuration

Loads environment variables from `.env` via python-dotenv. Validates that `DEEPGRAM_API_KEY` is set. All other variables have defaults or are optional.

Key variables:
- `SERVER_EXTERNAL_URL` - Required for Twilio to stream audio back. Set automatically by the setup wizard or manually for tunnel-based workflows.
- `ENDPOINT_SECRET` - Authenticates `POST /make-call` requests. Auto-generated by the setup wizard.
- `TWILIO_*` - Account credentials and phone number for placing calls.

### `telephony/routes.py` - API Endpoints

Three endpoints:

**`POST /make-call`** - Initiates an outbound call. Validates the endpoint secret, parses the request body (phone number + optional lead context), builds a Lead object, places the call via `call_manager`, and stores the lead context in `_pending_leads` for the WebSocket handler to pick up.

**`WS /twilio`** - Handles the audio stream. Accepts the WebSocket, waits for the Twilio "start" event, retrieves lead context from `_pending_leads` by call SID, creates a `VoiceAgentSession`, and delegates everything to it.

**`POST /amd-result`** - Receives Twilio's AMD callback with `AnsweredBy` and `CallSid`. Looks up the active session and signals it with the result.

The `_pending_leads` dict bridges lead context between the HTTP endpoint (where the call is placed) and the WebSocket handler (where audio arrives). This is necessary because Twilio's `calls.create()` returns a call SID, and the WebSocket connects separately with that same SID.

### `telephony/call_manager.py` - Outbound Call Manager

Wraps the Twilio REST API for placing outbound calls. Builds inline TwiML with `<Connect><Stream>` pointing to the server's WebSocket endpoint. Configures AMD parameters:

- `machine_detection="DetectMessageEnd"` - Wait for the voicemail greeting to finish (beep)
- `async_amd=True` - Don't block the call; detect in background
- `async_amd_status_callback` - POST result to `/amd-result`

### `voice_agent/session.py` - VoiceAgentSession

The core of the system. One instance per active call. Manages:

- **AMD branching**: Buffer audio until AMD result, then branch to human or voicemail path. Handles late AMD results (machine detected after voice agent is already running) by switching to voicemail mid-call.
- **Deepgram connection lifecycle**: Connect, configure, listen, cleanup
- **Audio loop**: A single long-lived task reads from the Twilio WebSocket for the entire call. Its behavior is controlled by `_audio_mode`: `"buffering"` during AMD wait, `"forwarding"` during live conversation, `"discarding"` during voicemail delivery.
- **Event dispatch**: Routes Deepgram events to appropriate handlers
- **Function calls**: Dispatches to `function_handlers.py`, sends results back
- **Barge-in**: Sends Twilio "clear" events on user speech detection
- **Silence detection**: Creates a `SilenceMonitor` that prompts unresponsive callers and eventually ends the call
- **Call termination**: Uses Twilio REST API to hang up after `end_call`

Key implementation details:

- Takes `lead_context` at creation time (unlike inbound, which has no per-call context)
- Uses `asyncio.Event` for AMD signaling between the `/amd-result` route and the session
- Buffers audio in a list during the AMD wait period, discards it when the voice agent starts (the greeting re-establishes the conversation)
- Uses manual `__aenter__()` / `__aexit__()` for the Deepgram connection because it outlives any single function scope
- The listen loop parses raw WebSocket frames and skips unrecognized message types (the Deepgram API may send types the SDK doesn't handle)
- Must wait for `SettingsApplied` from Deepgram before forwarding audio

### `voice_agent/silence_monitor.py` - Silence Monitor

Detects prolonged silence during a call and prompts the caller before ending the call. Uses Deepgram's `InjectAgentMessage` to make the agent speak each prompt.

Two events from the session drive the monitor:
- `notify_agent_audio_done()` — Agent finished speaking. Start (or restart) the silence timer.
- `notify_user_started_speaking()` — User is engaging. Reset attempts.

The timer advances through attempts defined in `SILENCE_ATTEMPTS`. The first attempt uses a long timeout (60s) to account for the gap between `AgentAudioDone` and actual audio playback on the phone. After the final attempt message is spoken and a `FINAL_WAIT` elapses with no response, the `on_timeout` callback fires to end the call.

### `voice_agent/agent_config.py` - Agent Configuration

Defines the agent's personality, capabilities, and technical settings:

- **System prompt**: Built dynamically via `_build_system_prompt(lead_context)`. Injects lead data (name, property address, insurance status, etc.) into the prompt. Includes compliance rules (AI disclosure, not a licensed agent, never quote prices).
- **Greeting**: Personalized opening with AI disclosure and permission-asking
- **Functions**: 4 tools (check_availability, book_appointment, update_lead, end_call)
- **Audio settings**: mulaw encoding at 8kHz (Twilio's native format)
- **Models**: Configurable LLM and TTS voice via environment variables

### `voice_agent/function_handlers.py` - Function Dispatch

Maps function names from agent config to backend service methods. Uses lazy imports to keep the boundary between voice agent and backend layers explicit.

### `voice_agent/voicemail.py` - Voicemail Delivery

Generates and streams voicemail audio when AMD detects an answering machine:

- Builds a personalized message using lead context (first name, company name, reference to quote)
- Uses Deepgram's Aura-2 TTS API (`client.speak.v1.audio.generate()`) - not the Voice Agent API
- Streams audio chunks to Twilio as they arrive (base64-encode, wrap in JSON, send over WebSocket)
- Uses the same voice model as the live agent for consistency
- Calls `update_lead` directly after delivery (no Voice Agent API connection in this path)

### `backend/models.py` - Data Models

Plain dataclasses: `PropertyAddress`, `Lead`, `ConsultationSlot`, `Appointment`. Each has a `display()` method that returns a human-readable string suitable for logging or the agent to read aloud.

### `backend/lead_service.py` - Mock Lead Service

An in-memory service that simulates a real CRM/scheduling backend. Key methods:

- `build_default_lead(phone)` - Creates the "Alex Mitchell" default mock lead
- `build_lead_from_dict(phone, data)` - Creates a lead from API request data with defaults for missing fields
- `check_availability(lead_id)` - Generates realistic consultation slots across the next 3 business days with 3 licensed agent names
- `book_appointment(lead_id, selected_slot, agent_name)` - Logs the booking
- `update_lead(lead_id, call_outcome, disposition, ...)` - Logs the full structured call outcome prominently to the console. This is the key output developers want to see.

The async method signatures are designed to look like HTTP API calls. To connect to a real CRM, replace the method bodies with actual HTTP requests.

### `make_call.py` - Developer CLI

A standalone script for triggering outbound calls. Uses stdlib `urllib.request` (no extra dependencies). Reads `SERVER_EXTERNAL_URL` and `ENDPOINT_SECRET` from `.env`. Supports custom lead names and JSON lead files. Handles errors gracefully.

## Audio Encoding

Twilio uses mulaw (u-law) encoding at 8kHz mono - a telephony standard. The Deepgram Voice Agent API is configured to match:

```python
audio=AgentV1SettingsAudio(
    input=AgentV1SettingsAudioInput(encoding="mulaw", sample_rate=8000),
    output=AgentV1SettingsAudioOutput(encoding="mulaw", sample_rate=8000, container="none"),
)
```

This means no transcoding is needed - audio passes through the server as-is. The only transformation is between Twilio's base64 JSON format and Deepgram's raw binary format.

The voicemail path uses the same encoding. Deepgram's Aura-2 TTS API is configured with `encoding="mulaw"`, `sample_rate=8000`, `container="none"` to produce raw mulaw audio compatible with Twilio.
