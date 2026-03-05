# Voice Agent Prompt Guide

Best practices for writing prompts for voice agents using the Deepgram Voice Agent API, with guidance specific to outbound calling scenarios.

## Core Principle: Voice-First Design

You're writing a script for a voice actor, not a chat interface. Every word the agent generates is synthesized into speech and played over a phone call. If it sounds awkward when read aloud, it will sound awkward when spoken.

## Critical Formatting Rules

Your prompt must explicitly forbid patterns that don't work in voice:

```
VOICE FORMATTING RULES:
You are a VOICE agent. Your responses are spoken aloud via text-to-speech.
- Use only plain conversational language
- NO markdown, emojis, brackets, or special formatting
- Keep responses brief: 1-2 sentences per turn
- Never announce function calls
- Spell out numbers naturally (say "January third" not "1/3")
```

### Why This Matters

```
Bad:  "That's great! 😊"
TTS:  "That's great exclamation point smiley face"

Bad:  "**Important**: Check your email"
TTS:  "star star Important star star colon Check your email"

Bad:  "[Looking up your appointment...]"
TTS:  "bracket Looking up your appointment dot dot dot bracket"

Good: "That's wonderful!"
TTS:  "That's wonderful!"
```

Without explicit instructions, LLMs default to chat-style formatting. The voice formatting rules block must be prominent in your prompt.

## Voice-Friendly Patterns

### Numbers and Identifiers

```
Bad:  "Your appointment is on 3/3/2026"
Good: "Your appointment is on January seventh"

Bad:  "That'll be $150.00"
Good: "That'll be one hundred and fifty dollars"

Bad:  "Call 555-1234"
Good: "Call five five five, one two three four"
```

### Lists

```
Bad:  "You have three options: 1) Basic, 2) Premium, 3) Enterprise"
Good: "You have three options. The first is Basic, then Premium,
       and finally Enterprise."
```

### Confirmations

```
Bad:  "Appointment Details:
       - Date: Jan 7
       - Time: 10:00 AM
       - Provider: Dr. Chen"

Good: "I have a checkup with Doctor Chen on January seventh at ten AM."
```

## Prompt Structure

A voice agent prompt should cover these sections:

### 1. Identity and Personality

```
You are a friendly and professional AI voice assistant calling on behalf
of Prestige Home Insurance. You are making an outbound call to follow up
on a homeowners insurance quote request.
```

For outbound, this must include who you're calling on behalf of and why. The person on the other end needs context immediately.

### 2. Voice Formatting Rules

Always include these explicitly. See above.

### 3. Compliance Rules (Outbound-Specific)

Outbound calls require explicit disclosure and boundaries:

```
CRITICAL COMPLIANCE RULES:
1. You MUST disclose that you are an AI voice assistant at the start of the call
2. You MUST state that you are NOT a licensed insurance agent
3. You MUST NOT quote prices or make coverage recommendations
4. If the person wants to end the call, let them go immediately
```

These rules should be prominent in the prompt and reinforced by the greeting.

### 4. Lead Context

For outbound calls, inject the lead data directly into the prompt so the agent has context before the conversation starts:

```
LEAD CONTEXT (from their online quote request):
- Name: Alex Mitchell
- Property: single family at 742 Evergreen Terrace, Springfield, IL
- Insurance status: currently insured but looking to switch providers
- Desired coverage start: April 15, 2026
```

This is the key architectural pattern for outbound: the system prompt is built dynamically at call time using `_build_system_prompt(lead_context)`. Each call gets a unique prompt tailored to that specific lead.

### 5. Call Flow

Define the stages of the conversation in order:

```
CALL FLOW:
1. OPENING: Confirm identity, disclose automation, explain purpose, ask permission
2. VERIFY: Confirm key details from their submission
3. GATHER: Ask qualifying questions one at a time
4. SCHEDULE: Check availability and book a consultation
5. WRAP UP: Confirm, thank them, end the call
```

Numbering the stages helps the agent stay on track without being overly rigid.

### 6. Function Call Rules

```
FUNCTION CALL RULES:
- check_availability: Read-only lookup, no confirmation needed
- book_appointment: Call AFTER the person selects a time slot
- update_lead: Call at the END of every call with full summary
- end_call: Say goodbye FIRST, then call the function
```

### 7. Conversation Style

```
CONVERSATION STYLE:
- Be professional, straightforward, and efficient
- This should take 2-4 minutes total
- Ask questions naturally, not like a checklist
- If they go off-topic, gently redirect
- Never pressure them
- Keep acknowledgments minimal - no filler praise
```

## Outbound-Specific Patterns

### The Opening Is Everything

Outbound calls are inherently interruptive. The first 10 seconds determine whether the person stays on the line. A good opener must:

1. Confirm the person's identity ("Am I speaking with...?")
2. Identify who you are and who you represent
3. Reference why you're calling (the quote request they submitted)
4. Disclose that you're an automated assistant
5. Explain the purpose of the call (verify details, schedule consultation)
6. Ask permission to continue

```
Good: "Hello, this is an automated assistant calling on behalf of Prestige
       Home Insurance. Am I speaking with Alex Mitchell? [wait for confirmation]
       I'm following up on your homeowners insurance quote request. I'm not a
       licensed insurance agent, but I'd like to verify a few details from your
       quote and see if you're interested in scheduling a phone consultation
       with a licensed agent. Do you have a few minutes?"
```

The greeting is pre-built (not generated turn-by-turn) to ensure disclosure happens reliably.

### Handling "Who Is This?" and "How Did You Get My Number?"

People receiving outbound calls may be suspicious. The prompt should prepare the agent for this:

```
If they ask who you are or how you got their number:
- Remind them you're calling on behalf of [company name]
- Reference their quote request with specific details (property address, date)
- If they don't recall, politely offer to end the call
```

The lead context in the prompt gives the agent specific details to reference, which helps establish legitimacy.

### Handling "Not Interested" or "Call Back Later"

```
If they're not interested: Acknowledge politely, call update_lead with
  not_interested, call end_call. No pushback, no hard sell.

If they want a callback: Ask when would be better, note it in update_lead
  with callback_requested, then end the call.
```

### Verify Before You Gather

The verification stage ("I have here that you're looking for coverage on a single-family home at 742 Evergreen Terrace...") serves two purposes:

1. Confirms the data is accurate
2. Demonstrates to the person that this is a legitimate follow-up, not a cold call

Bundle verification points together naturally:

```
Good: "So I have here that you're looking for coverage on a single-family
       home at 742 Evergreen Terrace in Springfield, with a target start
       date around mid-April. Is all of that still accurate?"

Bad:  "Can you confirm your address?"
      [wait]
      "And your property type?"
      [wait]
      "And your coverage start date?"
```

## Key Patterns

### Confirm-Then-Act

For operations that change state (booking), the agent should:

1. Present the options from the function result
2. Let the person choose
3. Confirm their choice
4. Then call the function

```
Agent: "I have openings on Thursday at ten AM or Thursday at two PM.
        Which works better for you?"
Caller: "Two PM works."
[Agent calls book_appointment]
```

### Read-Only Lookups

For operations that don't change state (checking availability), the agent can call the function without asking permission:

```
Agent: "Let me check what's available for you."
[Agent calls check_availability - no confirmation needed]
Agent: "I have openings on Thursday at ten AM and two PM."
```

### One Question at a Time

Voice conversations are sequential. Asking multiple questions in one turn confuses callers:

```
Bad:  "What's the age of your roof, and have there been any claims,
       and do you have a pool?"
Good: "Do you happen to know the approximate age of your roof?"
      [wait]
      "And have there been any insurance claims on the property
       in the past five years?"
```

Exception: Verification can bundle 2-3 points together because you're reading back facts for confirmation, not asking open-ended questions.

### Don't Announce Function Calls

```
Bad:  "Let me check that for you... [pause while function runs]... I found..."
Good: "I have openings at ten AM and two PM."
```

The function call happens between turns. The caller doesn't need to know about it.

### Graceful Endings

```
Good: "Thanks for your time, have a great day."
[Agent calls end_call]
```

Say goodbye first, then call the `end_call` function. Don't generate text after calling it.

## Testing Your Prompt

1. Place a test call using `make_call.py`
2. Have a conversation - does the agent stay in character?
3. Try edge cases:
   - Say "who is this?" at the opening
   - Say "I'm not interested"
   - Ask to be called back later
   - Give vague answers to questions ("I'm not sure about my roof")
   - Interrupt the agent mid-sentence (barge-in)
4. Check the server logs - are responses 1-3 sentences?
5. Check the `update_lead` output - does the call summary accurately capture the conversation?
6. Listen for unnatural phrasing - if it sounds wrong spoken aloud, rewrite it

## See Also

- `voice_agent/agent_config.py` - The insurance lead follow-up prompt (working example)
- [FUNCTION_GUIDE.md](FUNCTION_GUIDE.md) - Function definition best practices
