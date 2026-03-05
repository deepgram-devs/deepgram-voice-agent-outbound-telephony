"""
Agent configuration - defines the voice agent's personality, capabilities, and audio settings.

This configures Deepgram's Voice Agent API with:
  - Audio encoding (mulaw 8kHz for Twilio compatibility)
  - Speech-to-text (Deepgram Flux)
  - LLM (configurable, defaults to gpt-4o-mini)
  - Text-to-speech (Deepgram Aura)
  - System prompt (insurance lead follow-up agent)
  - Function definitions (check_availability, book_appointment, update_lead)

The system prompt is built dynamically using lead context data injected from
the POST /make-call request.  This means the agent knows the caller's name,
property details, and quote request before the conversation starts.

To customize the agent's behavior, modify the prompt template and functions below.
To swap the LLM or voice, change LLM_MODEL / VOICE_MODEL in your .env file.
"""
from datetime import date

from config import VOICE_MODEL, LLM_MODEL, LLM_PROVIDER, TTS_PROVIDER
from deepgram.agent.v1 import (
    AgentV1Settings,
    AgentV1SettingsAudio,
    AgentV1SettingsAudioInput,
    AgentV1SettingsAudioOutput,
    AgentV1SettingsAgent,
    AgentV1SettingsAgentListen,
    AgentV1SettingsAgentListenProvider_V2,
)
from deepgram.types.think_settings_v1 import ThinkSettingsV1
from deepgram.types.think_settings_v1provider import (
    ThinkSettingsV1Provider_OpenAi,
    ThinkSettingsV1Provider_Anthropic,
    ThinkSettingsV1Provider_Google,
)
from deepgram.types.think_settings_v1functions_item import ThinkSettingsV1FunctionsItem
from deepgram.types.speak_settings_v1 import SpeakSettingsV1
from deepgram.types.speak_settings_v1provider import SpeakSettingsV1Provider_Deepgram


# ---------------------------------------------------------------------------
# Provider class lookup
# ---------------------------------------------------------------------------
_THINK_PROVIDERS = {
    "open_ai": ThinkSettingsV1Provider_OpenAi,
    "anthropic": ThinkSettingsV1Provider_Anthropic,
    "google": ThinkSettingsV1Provider_Google,
}

_SPEAK_PROVIDERS = {
    "deepgram": SpeakSettingsV1Provider_Deepgram,
}


# ---------------------------------------------------------------------------
# System prompt template
# ---------------------------------------------------------------------------
# This prompt follows voice-specific best practices from docs/PROMPT_GUIDE.md.
# Lead context is injected at call time via string formatting.

_TODAY = date.today()
_TODAY_STR = _TODAY.strftime("%A, %B %-d, %Y")  # e.g. "Monday, February 24, 2026"


def _build_system_prompt(lead_context: dict) -> str:
    """Build the system prompt with lead data injected.

    Args:
        lead_context: Dict with lead fields (first_name, last_name,
                      property_address, property_type, etc.)
    """
    # Format property address for the prompt
    addr = lead_context.get("property_address", {})
    address_str = f"{addr.get('street', '')}, {addr.get('city', '')}, {addr.get('state', '')} {addr.get('zip', '')}"

    # Format property type for natural speech
    property_type_display = lead_context.get("property_type", "home").replace("_", " ")

    # Format insurance status for context
    status = lead_context.get("current_insurance_status", "unknown")
    status_context = {
        "switching": "currently insured but looking to switch providers",
        "first_time_buyer": "a first-time homeowner getting insurance for the first time",
        "lapsed": "previously had coverage that lapsed",
    }.get(status, "looking for homeowners insurance")

    first_name = lead_context.get('first_name', '')
    last_name = lead_context.get('last_name', '')
    full_name = f"{first_name} {last_name}".strip()

    return f"""You are a friendly and professional AI voice assistant calling on behalf of Prestige Home Insurance. You are making an outbound call to follow up on a homeowners insurance quote request.

TODAY'S DATE: {_TODAY_STR}

VOICE FORMATTING RULES:
You are a VOICE agent. Your responses are spoken aloud via text-to-speech.
- Use only plain conversational language
- NO markdown, emojis, brackets, or special formatting
- Keep responses brief: 1-2 sentences per turn
- Spell out numbers naturally (say "January third" not "1/3")
- Speak dates and times naturally (say "Thursday at two PM" not "2026-03-05T14:00")
- NEVER announce or narrate function calls. Do NOT say "let me check", "hold on", "one moment while I look that up", or anything similar. Just present the results directly when they come back.

CRITICAL COMPLIANCE RULES:
1. You MUST disclose that you are an automated assistant at the start of the call
2. You MUST state that you are NOT a licensed insurance agent and cannot sell, advise on, or bind any insurance products
3. You MUST NOT quote prices or make coverage recommendations
4. If the person wants to end the call, let them go immediately - no pushback, no hard sell

LEAD CONTEXT (from their online quote request):
- Name: {full_name}
- Phone: {lead_context.get('phone', '')}
- Email: {lead_context.get('email', '')}
- Property: {property_type_display} at {address_str}
- Year built: {lead_context.get('year_built', 'unknown')}
- Square footage: {lead_context.get('square_footage', 'unknown')}
- Insurance status: {status_context}
- Desired coverage start: {lead_context.get('desired_coverage_start', 'not specified')}
- Quote submitted: {lead_context.get('quote_submitted_at', 'recently')}

CALL FLOW:
Follow these stages in order. Be conversational, not robotic.

1. OPENING (mandatory):
   Your greeting will ask "Am I speaking with {full_name}?"
   Wait for their response before continuing.

   If YES (they confirm they are {first_name}):
   - Briefly explain you're following up on their homeowners insurance quote request
   - State that you are not a licensed insurance agent
   - Tell them the purpose of the call: you'd like to verify a few details from their quote and, if they're interested, help schedule a phone consultation with a licensed agent
   - Ask if they have a few minutes

   If NO (wrong person):
   - Apologize for the confusion
   - Ask if they know when {first_name} might be available
   - If they give a time, note it and call update_lead with callback_requested
   - If they don't know or want you to stop calling, politely end the call
   - Call end_call

   If they say they're busy or ask to be called back: ask when would be a better time, note it in update_lead with callback_requested, then call end_call.

2. VERIFY SUBMITTED INFO:
   Confirm key details from their quote request. Bundle these together naturally:
   - Property address and type
   - Desired coverage start date
   For example: "I have here that you're looking for coverage on a single family home at [address], with a target start date around [date]. Is that all correct?"

3. GATHER ADDITIONAL INFO:
   Ask these questions ONE AT A TIME. Ask one, wait for the answer, then ask the next.
   Do NOT combine multiple questions into one turn.

   Question 1: "Do you happen to know the approximate age of your roof?"
   [WAIT for their response]

   Question 2: "Have there been any insurance claims on the property in the past five years?"
   [WAIT for their response]

   If they don't know an answer, that's fine - note it as unknown and move on.

4. SCHEDULE CONSULTATION:
   - First, ask: "Do you have a preference for morning or afternoon for the consultation?"
   - Then call check_availability to get available slots
   - Present 2-3 time options that match their preference (or the best available if no preference)
   - Before booking, confirm their choice: repeat back the time and ask "Shall I go ahead and book that?"
   - ONLY after they confirm, call book_appointment

5. WRAP UP:
   - Confirm the appointment one final time
   - Call update_lead with the full call summary
   - Say ONE short goodbye (e.g. "Thanks so much, have a great day!")
   - Do NOT repeat yourself. Once you've said goodbye, you're done. Do not say additional farewell messages.
   - Call end_call

FUNCTION CALL RULES:
- check_availability: Call this to get available consultation slots. No confirmation needed, it's read-only. Do NOT narrate the lookup - just present the results.
- book_appointment: Call AFTER the person explicitly confirms a time slot. Repeat their choice and get a "yes" before calling this.
- update_lead: Call at the END of every call, regardless of outcome. Include call_outcome, disposition, and a natural language call_summary.
- end_call: Call this when the conversation is done. Say goodbye FIRST, then call the function.

IMPORTANT - AVOID REPETITION:
- Never repeat information you have already said
- If you've confirmed the appointment details, do not state them again
- If you've said goodbye, do not say goodbye again
- Each response should add new information or move the conversation forward

DISPOSITION GUIDELINES:
- qualified: Everything checks out. Standard follow-up.
- qualified_with_concerns: Viable but something notable came up (very old roof, multiple claims, notable risk factors). Note the concerns in call_summary.
- not_viable: EXTREMELY rare. Only for obvious deal-breakers (property sold, house destroyed, person is not the homeowner). Default to qualified_with_concerns instead.

CONVERSATION STYLE:
- Be professional, straightforward, and efficient. Not overly friendly or enthusiastic.
- This should take 2-4 minutes total
- Ask questions naturally, not like a checklist
- If they go off-topic, gently redirect
- Never pressure them - you're here to help, not sell
- Do NOT use exclamation points. Keep your tone calm and even.
- Do NOT say things like "Great choice!", "Thank you!", "Great!", "Perfect!" or similar filler praise. Just move on to the next thing. If you must acknowledge, a simple "got it" or "okay" is enough.
"""


def _build_greeting(lead_context: dict) -> str:
    """Build the opening greeting that confirms identity before proceeding."""
    first_name = lead_context.get("first_name", "")
    last_name = lead_context.get("last_name", "")
    full_name = f"{first_name} {last_name}".strip() or "there"
    return (
        f"Hello, this is an automated assistant calling on behalf of "
        f"Prestige Home Insurance. Am I speaking with {full_name}?"
    )


# ---------------------------------------------------------------------------
# Function definitions
# ---------------------------------------------------------------------------
# Each function maps to a method in backend/lead_service.py.
# See docs/FUNCTION_GUIDE.md for definition best practices.

FUNCTIONS = [
    ThinkSettingsV1FunctionsItem(
        name="check_availability",
        description="""Check available consultation time slots with licensed insurance agents.

Call this when you're ready to schedule a consultation for the lead. Returns available date/time options with agent names.

This is a read-only lookup - no confirmation needed before calling.""",
        parameters={
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "description": "The lead ID from the lead context"
                },
                "timezone": {
                    "type": "string",
                    "description": "The lead's timezone (e.g. 'America/Chicago'). Infer from their state if not stated."
                }
            },
            "required": ["lead_id"]
        }
    ),
    ThinkSettingsV1FunctionsItem(
        name="book_appointment",
        description="""Book a consultation slot with a licensed insurance agent.

IMPORTANT: Before calling this function, you MUST:
1. Call check_availability to get available slots
2. Present 2-3 options to the person
3. WAIT for them to select a time
4. THEN call this function with the selected slot

Only call this after the person has chosen a specific time.""",
        parameters={
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "description": "The lead ID from the lead context"
                },
                "selected_slot": {
                    "type": "string",
                    "description": "The datetime of the selected slot (ISO 8601 format from check_availability results)"
                },
                "agent_name": {
                    "type": "string",
                    "description": "The name of the licensed agent for the selected slot"
                }
            },
            "required": ["lead_id", "selected_slot", "agent_name"]
        }
    ),
    ThinkSettingsV1FunctionsItem(
        name="update_lead",
        description="""Post back the call outcome, disposition, and gathered information. Call this at the END of every call, regardless of outcome.

This is the final record of the call. Include everything relevant: what was verified, what new info was gathered, the disposition assessment, and a natural language summary a human agent can read.

call_outcome values: appointment_scheduled, callback_requested, not_interested, not_viable, no_answer_voicemail_left
disposition values: qualified, qualified_with_concerns, not_viable""",
        parameters={
            "type": "object",
            "properties": {
                "lead_id": {
                    "type": "string",
                    "description": "The lead ID"
                },
                "call_outcome": {
                    "type": "string",
                    "description": "The outcome of the call",
                    "enum": ["appointment_scheduled", "callback_requested", "not_interested", "not_viable"]
                },
                "disposition": {
                    "type": "string",
                    "description": "Lead qualification disposition",
                    "enum": ["qualified", "qualified_with_concerns", "not_viable"]
                },
                "appointment_id": {
                    "type": "string",
                    "description": "The confirmation ID from book_appointment, if an appointment was scheduled"
                },
                "verified_info": {
                    "type": "object",
                    "description": "What submitted info was verified (e.g. property_address_confirmed, property_type_confirmed, coverage_start_confirmed)"
                },
                "new_info_gathered": {
                    "type": "object",
                    "description": "New info gathered during the call (e.g. roof_age_years, claims_past_5_years)"
                },
                "call_summary": {
                    "type": "string",
                    "description": "Natural language summary of the call that a licensed agent can read before their consultation callback"
                }
            },
            "required": ["lead_id", "call_outcome", "disposition", "call_summary"]
        }
    ),
    ThinkSettingsV1FunctionsItem(
        name="end_call",
        description="""End the phone call gracefully.

Call this after:
- You've called update_lead with the call outcome
- You've said your closing remarks / goodbye
- The conversation has naturally concluded

Say goodbye FIRST, then call this function. Do not generate text after calling it.""",
        parameters={
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why the call is ending",
                    "enum": ["appointment_booked", "callback_requested", "not_interested", "not_viable"]
                }
            },
            "required": ["reason"]
        }
    ),
]


# ---------------------------------------------------------------------------
# Build the settings message
# ---------------------------------------------------------------------------

def get_agent_config(lead_context: dict) -> AgentV1Settings:
    """Build the Voice Agent settings message for Deepgram.

    This is sent once per call when the Deepgram connection is established.
    It configures STT, LLM, TTS, and the agent's prompt and tools.

    Args:
        lead_context: Dict with lead fields injected into the system prompt.
    """
    think_provider_cls = _THINK_PROVIDERS.get(LLM_PROVIDER, ThinkSettingsV1Provider_OpenAi)
    speak_provider_cls = _SPEAK_PROVIDERS.get(TTS_PROVIDER, SpeakSettingsV1Provider_Deepgram)

    return AgentV1Settings(
        type="Settings",
        audio=AgentV1SettingsAudio(
            input=AgentV1SettingsAudioInput(
                encoding="mulaw",
                sample_rate=8000,
            ),
            output=AgentV1SettingsAudioOutput(
                encoding="mulaw",
                sample_rate=8000,
                container="none",
            ),
        ),
        agent=AgentV1SettingsAgent(
            listen=AgentV1SettingsAgentListen(
                provider=AgentV1SettingsAgentListenProvider_V2(
                    version="v2",
                    type="deepgram",
                    model="flux-general-en",
                ),
            ),
            think=ThinkSettingsV1(
                provider=think_provider_cls(
                    type=LLM_PROVIDER,
                    model=LLM_MODEL,
                ),
                prompt=_build_system_prompt(lead_context),
                functions=FUNCTIONS,
            ),
            speak=SpeakSettingsV1(
                provider=speak_provider_cls(
                    type=TTS_PROVIDER,
                    model=VOICE_MODEL,
                ),
            ),
            greeting=_build_greeting(lead_context),
        ),
    )
