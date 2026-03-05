# Outbound Telephony Voice Agent

A reference implementation for building an outbound telephony voice agent using [Deepgram's Voice Agent API](https://developers.deepgram.com/docs/voice-agent) and [Twilio](https://www.twilio.com/). Uses [Deepgram Flux](https://developers.deepgram.com/docs/models-overview) for speech-to-text with native turn-taking optimized for real-time voice agent conversations. Includes endpoint authentication and answering machine detection (AMD) with voicemail delivery.

The server initiates outbound calls via a REST API. When the call connects, an AI voice agent follows up on a homeowners insurance quote request: it verifies lead information, gathers additional details, and schedules a consultation with a licensed agent. If voicemail is detected, it delivers a personalized message instead.

## Architecture

```
              ┌─────────────────────────────────────────┐
              │          Application Server             │
              │             (Starlette)                 │
              │                                         │
              │   POST /make-call                       │  ← External trigger (e.g. CRM, CLI)
              │     │                                   │
              │     ▼                                   │
              │   Twilio REST API ─── place call ──┐    │
              │                                    │    │
              │                              ┌─────┘    │
              │                              ▼          │
              │                         ┌─────────┐     │
              │                         │ Twilio  │     │
              │                         │  dials  │     │
              │                         │  phone  │     │
              │                         └────┬────┘     │
              │                              └─────┐    │
              │   WS /twilio  ◄──── audio stream ──┘    │
              │     │                                   │
              │     ▼                                   │
              │   POST /amd-result  ◄── AMD callback    │
              │     │                                   │
              │     ▼                                   │
              │   ┌───────────────────────────────┐     │
              │   │      VoiceAgentSession        │     │
              │   │                               │     │
              │   │  Human?  ──► Deepgram Voice   │     │
              │   │              Agent API        │     │
              │   │              (STT→LLM→TTS)    │     │
              │   │                               │     │
              │   │  Voicemail? ──► Deepgram TTS  │     │
              │   │                               │     │
              │   └───────────┬───────────────────┘     │
              │               │                         │
              │   ┌───────────▼───────────────────┐     │
              │   │     Backend Service           │     │
              │   │     (Mock Lead/CRM API)       │     │
              │   │                               │     │
              │   │  - Check consultation slots   │     │
              │   │  - Book appointments          │     │
              │   │  - Post back call outcomes    │     │
              │   └───────────────────────────────┘     │
              └─────────────────────────────────────────┘
```

### Call Initiation Flow

1. An external system (CRM, CLI script, webhook) sends `POST /make-call` with a phone number and lead context
2. The server calls the Twilio REST API with inline TwiML containing `<Connect><Stream>` pointing back to its own WebSocket endpoint
3. Twilio dials the recipient's phone and opens a WebSocket back to the server to stream audio
4. Twilio's async AMD runs in the background and POSTs the result to `/amd-result`

### Audio Flow (Human Path)

1. Recipient speaks into their phone
2. Twilio captures the audio and streams it as base64-encoded mulaw over WebSocket
3. The application server decodes the base64 and sends raw mulaw bytes to Deepgram's Voice Agent API
4. Deepgram handles the full pipeline: speech-to-text, LLM reasoning, text-to-speech
5. Deepgram sends back raw mulaw audio bytes
6. The application server encodes to base64 and sends as JSON to Twilio
7. Twilio plays the audio to the recipient

### AMD + Voicemail Flow

When the call connects, the server doesn't know if it's a human or voicemail. It buffers incoming audio while Twilio's AMD runs (~2-4 seconds). When the result arrives:

- **Human**: Connect to Deepgram Voice Agent API, flush buffered audio, start conversation
- **Voicemail**: Deliver a personalized message via Deepgram Aura-2 TTS, then hang up

### Key Technical Concepts

- **Outbound call initiation**: The server actively places calls via the Twilio REST API with inline TwiML. This is the inverse of inbound, where the server passively receives calls. The server still needs a public URL because Twilio opens a WebSocket back to stream audio.

- **Single WebSocket bridge**: The core of the system is `VoiceAgentSession`, which bridges two WebSocket connections (one to Twilio, one to Deepgram). It translates between Twilio's JSON-based protocol and Deepgram's binary audio protocol.

- **Lead context injection**: The agent's system prompt is built dynamically with lead data from the `POST /make-call` request. The agent knows the caller's name, property details, and quote request before the conversation starts.

- **Barge-in**: When the Deepgram Voice Agent detects that the user started speaking, the server sends a Twilio "clear" event to immediately stop playing agent audio.

- **Function calls**: The Deepgram Voice Agent API supports tool use. The agent can check consultation availability, book appointments, and post structured call outcomes back to a CRM-like backend.

- **Structured call outcomes**: The `update_lead` function captures the full call outcome — disposition, verified info, new info gathered, and a natural language summary — and logs it to the console. In production, this payload would go to a CRM, webhook, or database.

- **Answering machine detection**: Twilio's async AMD detects whether a human or voicemail answered. The session buffers audio until the result arrives, then branches accordingly. If AMD detects a machine after the voice agent has already started (late detection), the session tears down the Deepgram connection and switches to voicemail delivery mid-call.

- **Silence detection**: A silence monitor tracks whether the caller is responding. If the caller goes silent for 60 seconds, the agent prompts them ("Are you still there?") and eventually ends the call. Uses Deepgram's `InjectAgentMessage` to make the agent speak each prompt naturally.

For a deeper look at the call flow, session lifecycle, and component details, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick Start

The fastest path to a working outbound voice agent is the setup wizard, which configures Twilio and deploys to [Fly.io](https://fly.io).

### Prerequisites

- Python 3.12+
- A [Deepgram account](https://console.deepgram.com/) and [API key](https://developers.deepgram.com/docs/create-additional-api-keys#create-an-api-key-using-the-deepgram-console)
  - $200 free credits, no credit card required
- A [Twilio account](https://www.twilio.com/try-twilio)
  - New accounts come with trial credits.
- A [Fly.io account](https://fly.io/app/sign-up) and [flyctl](https://fly.io/docs/flyctl/install/) installed and authenticated (`flyctl auth login`).
  - Fly.io's [free allowance](https://fly.io/docs/about/free-trial/) is more than enough for this reference implementation with the default suspend-on-idle configuration.

Note: Twilio trial accounts play a short disclaimer before connecting callers.

### 1. Clone and Install

```bash
git clone https://github.com/deepgram-devs/deepgram-voice-agent-outbound-telephony.git
cd deepgram-voice-agent-outbound-telephony

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and add your Deepgram API key:

```
DEEPGRAM_API_KEY=your_key_here
```

### 3. Run the Setup Wizard

```bash
python setup.py
```

The wizard will:
1. Prompt for your [Twilio account credentials](https://help.twilio.com/articles/14726256820123-What-is-a-Twilio-Account-SID-and-where-can-I-find-it-) (from [console.twilio.com](https://www.twilio.com/console))
2. Let you pick an existing phone number or purchase a new one
3. Generate an endpoint secret for securing the `/make-call` endpoint
4. Deploy your voice agent to Fly.io

### 4. Place a Test Call

```bash
python make_call.py --to "+15551234567"
```

Your phone rings. The voice agent runs through the insurance lead follow-up conversation. Check the server logs for the full conversation transcript and structured call outcome.

```bash
# Other setup wizard modes:
python setup.py --twilio-only       # Skip Fly.io, provide your own URL
python setup.py --status            # Show current config
python setup.py --teardown          # Clean up deployment
python setup.py --redeploy          # Redeploy to pick up code changes or .env changes
```

### Viewing Logs

After deployment, view your application logs with:

```bash
flyctl logs --app <your-app-name>
```

The app name is shown in the setup wizard output and in `python setup.py --status`.

### Cold Starts

The default configuration uses Fly.io's suspend mode. Your server suspends when idle and wakes in 1-3 seconds on incoming requests.

If you want instant response times for demos or production use, set `min_machines_running = 1` in `fly.toml` and redeploy to keep one VM always warm:

```toml
# fly.toml
min_machines_running = 1
```

See [Fly.io pricing](https://fly.io/docs/about/pricing/) for details.

### Twilio Regulatory Requirements

Depending on your region, Twilio may require address verification before you can purchase a phone number. The setup wizard will surface any errors from Twilio. Follow the instructions in the [Twilio console](https://www.twilio.com/console) if prompted.

## Alternative: Tunnel + Twilio

If you prefer to run the server locally instead of deploying to Fly.io, you can use a tunnel to expose your locally running voice agent server via a public URL.

### 1. Start a Tunnel

```bash
# ngrok
ngrok http 8080

# or zrok
zrok share public localhost:8080
```

Copy the public URL (e.g., `https://xxxx.ngrok.io`).

### 2. Update Configuration

Add to your `.env`:

```
SERVER_EXTERNAL_URL=https://xxxx.ngrok.io
TWILIO_ACCOUNT_SID=ACxxxxx
TWILIO_AUTH_TOKEN=xxxxx
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
```

### 3. Start the Server

```bash
python main.py
```

### 4. Place a Call

```bash
python make_call.py --to "+15551234567"
```

Or use the setup wizard with `--twilio-only` to handle Twilio configuration:

```bash
python setup.py --twilio-only
```

See [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md) for the full local development guide.

## Using `make_call.py`

The `make_call.py` CLI script is the primary way to trigger outbound calls during development.

```bash
# Simplest - uses default mock lead (Alex Mitchell)
python make_call.py --to "+15551234567"

# With custom lead name
python make_call.py --to "+15551234567" --lead-name "John Smith"

# With full custom lead from a JSON file
python make_call.py --to "+15551234567" --lead-file custom_lead.json

# Point at a different server (e.g., deployed on Fly.io)
python make_call.py --to "+15551234567" --server "https://my-app.fly.dev"
```

The script reads `SERVER_EXTERNAL_URL` and `ENDPOINT_SECRET` from `.env` automatically.

## Example Conversation

```
Agent: "Hello, this is an automated assistant calling on behalf of Prestige
        Home Insurance. Am I speaking with Alex Mitchell?"

Alex:  "Yes, that's me."

Agent: "I'm following up on your homeowners insurance quote request. I'm not
        a licensed insurance agent, but I'd like to verify a few details from
        your quote and see if you're interested in scheduling a phone
        consultation with a licensed agent. Do you have a few minutes?"

Alex:  "Sure, go ahead."

Agent: "I have here that you're looking for coverage on a single-family home
        at 742 Evergreen Terrace in Springfield, Illinois, with a target start
        date around April fifteenth. Is that all correct?"

Alex:  "Yeah, that's right."

Agent: "Do you happen to know the approximate age of your roof?"

Alex:  "I think it was replaced maybe twelve years ago."

Agent: "Got it. Have there been any insurance claims on the property in the
        past five years?"

Alex:  "No, none."

Agent: "Do you have a preference for morning or afternoon for the consultation?"

Alex:  "Afternoon works better."

  [Agent calls check_availability(lead_id="lead_29a4c1")]

Agent: "There's an opening on Thursday at two PM with James Rivera, or Friday
        at two PM with Monica Chen. Which works best for you?"

Alex:  "Thursday at two."

Agent: "Shall I go ahead and book that consultation for Thursday at two PM
        with James Rivera?"

Alex:  "Yep, that's fine."

  [Agent calls book_appointment(lead_id="lead_29a4c1",
                                selected_slot="2026-03-05T14:00:00-06:00",
                                agent_name="James Rivera")]

Agent: "Your appointment is booked for Thursday at two PM with James Rivera.
        Thanks for your time, have a great day."

  [Agent calls update_lead(lead_id="lead_29a4c1",
                           call_outcome="appointment_scheduled",
                           disposition="qualified",
                           call_summary="Spoke with Alex Mitchell regarding...")]

  [Agent calls end_call(reason="appointment_booked")]
```

After the call, the server logs show the structured call outcome:

```
======================================================================
  LEAD UPDATE - Call outcome posted back to CRM
======================================================================
{
  "lead_id": "lead_29a4c1",
  "call_outcome": "appointment_scheduled",
  "disposition": "qualified",
  "appointment_id": "appt_8x92k",
  "verified_info": {
    "property_address_confirmed": true,
    "property_type_confirmed": true,
    "coverage_start_confirmed": true
  },
  "new_info_gathered": {
    "roof_age_years": 12,
    "claims_past_5_years": 0
  },
  "call_summary": "Spoke with Alex Mitchell regarding their homeowners
    insurance quote request for 742 Evergreen Terrace, Springfield, IL.
    All submitted information confirmed accurate. Property has a 12-year-old
    roof, no claims in the past 5 years. Lead is in good standing. Scheduled
    consultation with James Rivera for March 5 at 2:00 PM CT."
}
======================================================================
```

## Project Structure

```
outbound-telephony-voice-agent/
├── main.py                          # Entry point, Starlette app + uvicorn
├── config.py                        # Environment variable management
├── setup.py                         # Setup wizard (Twilio + Fly.io)
├── make_call.py                     # CLI script to initiate outbound calls
├── requirements.txt                 
├── .env.example                     
├── Dockerfile                       
├── fly.toml                         
│
├── telephony/
│   ├── routes.py                    # POST /make-call, WS /twilio, POST /amd-result
│   └── call_manager.py              # Outbound call initiation via Twilio REST API
│
├── voice_agent/
│   ├── session.py                   # VoiceAgentSession, Deepgram connection + audio bridge
│   ├── agent_config.py              # Agent prompt, functions, audio/model settings
│   ├── function_handlers.py         # Routes function calls to backend service
│   ├── voicemail.py                 # Voicemail TTS generation via Deepgram Aura-2 TTS
│   └── silence_monitor.py           # Detects prolonged silence and prompts the caller
│
├── backend/
│   ├── models.py                    # Data models (Lead, ConsultationSlot, Appointment)
│   └── lead_service.py              # Mock lead/CRM API (in-memory)
│
└── docs/
    ├── ARCHITECTURE.md              # Detailed architecture and data flows
    ├── PROMPT_GUIDE.md              # Voice agent prompt best practices
    ├── FUNCTION_GUIDE.md            # Function definition best practices
    └── LOCAL_DEVELOPMENT.md         # Local dev setup guide
```

## Customization

### Change the Agent's Personality

Edit `_build_system_prompt()` in `voice_agent/agent_config.py`. The prompt is built dynamically with lead context data, so the agent knows who it's calling and why. See [docs/PROMPT_GUIDE.md](docs/PROMPT_GUIDE.md) for voice-specific prompt best practices.

### Add or Modify Functions

1. Define the function in `voice_agent/agent_config.py` (in the `FUNCTIONS` list)
2. Add the handler in `voice_agent/function_handlers.py`
3. Implement the backend logic in `backend/lead_service.py`

See [docs/FUNCTION_GUIDE.md](docs/FUNCTION_GUIDE.md) for function definition best practices.

### Swap the LLM or Voice

Set environment variables in `.env`:

```
LLM_MODEL=gpt-4o-mini          # Default: gpt-4o-mini
LLM_PROVIDER=open_ai           # Default: open_ai (also: anthropic, google)
VOICE_MODEL=aura-2-thalia-en   # Default: aura-2-thalia-en
TTS_PROVIDER=deepgram           # Default: deepgram
```

For Fly.io deployments, run `python setup.py --redeploy`to sync
any `.env` changes to the deployed app.

For available LLM models, see the [Voice Agent LLM docs](https://developers.deepgram.com/docs/voice-agent-llm-models).
For available TTS models, see the [Voice Agent TTS docs](https://developers.deepgram.com/docs/voice-agent-tts-models).

### Multilingual Support

This reference implementation is configured for English using Deepgram Flux (`flux-general-en`) for STT, which provides native turn-taking optimized for voice agents. For building voice agents in other languages, see the [Deepgram multilingual voice agent guide](https://developers.deepgram.com/docs/multilingual-voice-agent).

### Replace the Mock Backend

The `backend/` directory contains an in-memory mock. To connect to a real CRM or scheduling system:

1. Keep the same method signatures in `lead_service.py`
2. Replace the method bodies with HTTP calls to your real API
3. The voice agent layer doesn't need to change

The function dispatch in `voice_agent/function_handlers.py` uses lazy imports, making the boundary between voice agent and backend explicit.

### Customize the Voicemail Message

Edit `_build_voicemail_text()` in `voice_agent/voicemail.py`. The message is personalized with the lead's first name from the lead context.

## Compliance Note

This reference implementation is a **technical reference**, not a compliance reference. Outbound voice calling is often subject to regulations (e.g. TCPA or equivalent regulations in other jurisdictions).

This implementation includes a few compliance-aware design choices:
- AI disclosure at the start of every call
- Statement that the agent is not a licensed insurance agent
- The scenario assumes prior express consent (quote request submitted)

These are merely examples, they do not constitute actual compliance guidance.

Developers deploying outbound voice agents should consult appropriate legal and/or regulatory guidance to ensure compliance with applicable regulations.

## Additional Resources

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Detailed architecture, data flow diagrams, and component details
- [docs/PROMPT_GUIDE.md](docs/PROMPT_GUIDE.md) - Best practices for writing voice agent prompts
- [docs/FUNCTION_GUIDE.md](docs/FUNCTION_GUIDE.md) - Best practices for defining agent functions
- [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md) - Step-by-step local development setup
- [Deepgram Voice Agent API Docs](https://developers.deepgram.com/docs/voice-agent) - Official API documentation
