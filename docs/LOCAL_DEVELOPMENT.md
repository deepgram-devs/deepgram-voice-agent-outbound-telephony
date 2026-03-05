# Local Development Guide

How to develop and test the outbound voice agent locally using a tunnel.

## Overview

Unlike inbound voice agents, outbound agents can't be tested without Twilio — the server needs to actively place calls and receive audio streams back. Local development requires:

1. A tunnel (ngrok or zrok) to make your local server reachable by Twilio
2. Twilio credentials and a phone number
3. Your own phone to receive the call

## Prerequisites

- Python 3.12+
- A [Deepgram API key](https://console.deepgram.com/) (free tier available)
- A [Twilio account](https://www.twilio.com/try-twilio) with Account SID, Auth Token, and a phone number
- A tunnel tool: [ngrok](https://ngrok.com/) or [zrok](https://zrok.io/)
- A phone to call

## Setup

### 1. Create Virtual Environment

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start a Tunnel

```bash
# ngrok
ngrok http 8080

# or zrok
zrok share public localhost:8080
```

Copy the public HTTPS URL (e.g., `https://xxxx.ngrok.io`).

### 4. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` and set these values:

```
DEEPGRAM_API_KEY=your_key_here

SERVER_EXTERNAL_URL=https://xxxx.ngrok.io

TWILIO_ACCOUNT_SID=ACxxxxx
TWILIO_AUTH_TOKEN=xxxxx
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
```

The `SERVER_EXTERNAL_URL` must match your tunnel URL. Twilio uses this to open a WebSocket back to your server and to send AMD callbacks.

Optionally set an endpoint secret (the setup wizard generates one, but it's optional for local dev):

```
ENDPOINT_SECRET=your_optional_secret
```

If `ENDPOINT_SECRET` is not set, the `/make-call` endpoint accepts all requests.

### Alternative: Use the Setup Wizard

Instead of manually configuring `.env`, you can use the setup wizard in Twilio-only mode:

```bash
python setup.py --twilio-only
```

This prompts for your Twilio credentials, lets you select a phone number, generates an endpoint secret, and saves everything to `.env`.

## Running

### 1. Start the Server

```bash
python main.py
```

You should see:

```
12:00:00.000 INFO __main__ - Deepgram API key: configured
12:00:00.000 INFO __main__ - External URL: https://xxxx.ngrok.io
12:00:00.000 INFO __main__ - Make-call endpoint: https://xxxx.ngrok.io/make-call
```

### 2. Place a Test Call

In another terminal:

```bash
python make_call.py --to "+15551234567"
```

Replace `+15551234567` with your actual phone number. Your phone will ring within a few seconds.

### make_call.py Options

```bash
# Default mock lead (Alex Mitchell)
python make_call.py --to "+15551234567"

# Custom lead name
python make_call.py --to "+15551234567" --lead-name "John Smith"

# Full custom lead from JSON file
python make_call.py --to "+15551234567" --lead-file my_lead.json

# Override server URL
python make_call.py --to "+15551234567" --server "https://my-app.fly.dev"

# Override endpoint secret
python make_call.py --to "+15551234567" --secret "my_secret"
```

### 3. Have the Conversation

Pick up your phone and talk to the agent. The server terminal shows:
- Call connection and AMD detection
- Full conversation transcript (both sides)
- Function calls and results
- Structured call outcome from `update_lead`

### Ending a Call

The call ends when:
- The agent calls `end_call` (after the conversation concludes)
- You hang up
- You press `Ctrl+C` on the server (terminates everything)

## What You Can Test Locally

- Full agent conversation (disclosure, verify, gather, schedule, wrap up)
- All function calls (check_availability, book_appointment, update_lead, end_call)
- AMD detection (human vs. voicemail branching)
- Voicemail delivery (call a number that goes to voicemail)
- Different lead contexts (`--lead-name`, `--lead-file`)
- Prompt iteration — edit `voice_agent/agent_config.py`, restart the server, call again
- Barge-in behavior (interrupt the agent while it's speaking)
- Edge cases (say "not interested", "call back later", hang up mid-conversation)

## What Requires Deployment

- Production-grade latency (local tunnels add latency)
- Concurrent calls from different numbers
- Persistent availability (tunnels close when you close the terminal)
- Endpoint secret enforcement in a shared environment

For the fastest path to a deployed instance, use the setup wizard:

```bash
python setup.py  # Full setup: Twilio + Fly.io
```

## Testing Voicemail

To test voicemail delivery, call a number that goes to voicemail (e.g., your own phone with Do Not Disturb on). You should see:

```
[SESSION:CAxxx] AMD result: machine_end_beep
[SESSION:CAxxx] Voicemail detected - delivering message
[VOICEMAIL] Delivering voicemail: Hi Alex, this is an automated assistant...
[VOICEMAIL] Delivered 42 audio chunks
```

Followed by the lead update with `call_outcome: no_answer_voicemail_left`.

## Troubleshooting

### "Missing SERVER_EXTERNAL_URL"

The server needs a public URL for Twilio to connect back. Make sure your tunnel is running and `SERVER_EXTERNAL_URL` is set in `.env`:

```
SERVER_EXTERNAL_URL=https://xxxx.ngrok.io
```

### "Missing Twilio configuration"

Set all three Twilio variables in `.env`:

```
TWILIO_ACCOUNT_SID=ACxxxxx
TWILIO_AUTH_TOKEN=xxxxx
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
```

### "Could not connect to server"

Make sure the server is running:

```bash
python main.py
```

And that `make_call.py` is pointed at the right URL (reads `SERVER_EXTERNAL_URL` from `.env`).

### Phone doesn't ring

- Check the server logs for errors after calling `make_call.py`
- Verify your Twilio account has sufficient balance
- Verify the phone number is in E.164 format: `+15551234567`
- Trial accounts can only call verified numbers — add yours at [twilio.com/console](https://www.twilio.com/console)

### "Timeout waiting for settings to be applied"

This means the Deepgram connection was established but the agent configuration was rejected. Check:
- Your `DEEPGRAM_API_KEY` is valid
- The `LLM_MODEL` in `.env` is a model your Deepgram account has access to
- The `VOICE_MODEL` in `.env` is a valid Deepgram Aura voice

### AMD always returns "unknown"

Twilio's AMD works best with real phone calls. If testing with VoIP or unusual phone setups, AMD may not detect reliably. The agent treats "unknown" as human, which is the safe default.

### Tunnel URL changed

If your tunnel restarts and gets a new URL, update `SERVER_EXTERNAL_URL` in `.env` and restart the server.
