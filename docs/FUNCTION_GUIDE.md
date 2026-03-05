# Function Definition Guide

Best practices for defining functions (tools) for voice agents using the Deepgram Voice Agent API.

## Core Principle

Function descriptions teach the LLM when and how to call functions. Write them as instructions to a colleague, not as developer documentation.

## Anatomy of a Function Definition

```python
ThinkSettingsV1FunctionsItem(
    name="check_availability",
    description="What it does, when to use it, and any workflow requirements",
    parameters={
        "type": "object",
        "properties": {
            "lead_id": {
                "type": "string",
                "description": "What it is and where to find it"
            }
        },
        "required": ["lead_id"]
    }
)
```

The function definition is sent to Deepgram as part of the agent configuration. The LLM sees the name, description, and parameters when deciding whether to call a function.

## Writing Descriptions

Use a three-part formula: **What**, **When**, **How**.

```python
description="""Check available consultation time slots with licensed insurance agents.

Call this when you're ready to schedule a consultation for the lead.
Returns available date/time options with agent names.

This is a read-only lookup - no confirmation needed before calling."""
```

### What to Include

- What the function does (one sentence)
- When to call it (triggers)
- Any pre-conditions (confirm with the caller first?)
- What the result means (if not obvious)

### What to Omit

- Implementation details (the LLM doesn't care about your database)
- Error handling instructions (handle errors in your code, not the prompt)
- Response format details (the LLM adapts to whatever JSON you return)

## Confirm-Then-Act Pattern

For functions that change state, the description must instruct the LLM to get confirmation first:

```python
description="""Book a consultation slot with a licensed insurance agent.

IMPORTANT: Before calling this function, you MUST:
1. Call check_availability to get available slots
2. Present 2-3 options to the person
3. WAIT for them to select a time
4. THEN call this function with the selected slot

Only call this after the person has chosen a specific time."""
```

Without this, the LLM will call the function as soon as it has the parameters, skipping confirmation.

For read-only functions, explicitly say no confirmation is needed:

```python
description="""Check available consultation time slots.

This is a read-only lookup - no confirmation needed before calling."""
```

## Voice-Specific Parameter Descriptions

Voice agents hear speech, not text. Teach the LLM to parse natural language into structured parameters:

```python
"timezone": {
    "type": "string",
    "description": "The lead's timezone (e.g. 'America/Chicago'). "
                   "Infer from their state if not stated."
}
```

```python
"selected_slot": {
    "type": "string",
    "description": "The datetime of the selected slot "
                   "(ISO 8601 format from check_availability results)"
}
```

The LLM is generally good at converting spoken words to structured data, but explicit format instructions help with edge cases.

## Parameter Design

### Use `required` Carefully

Only require parameters that are truly necessary. Optional parameters with good descriptions give the LLM flexibility:

```python
"properties": {
    "lead_id": {
        "type": "string",
        "description": "The lead ID from the lead context"
    },
    "timezone": {
        "type": "string",
        "description": "The lead's timezone. Omit to use default."
    }
},
"required": ["lead_id"]  # timezone is optional
```

### Use Enums to Constrain Values

```python
"call_outcome": {
    "type": "string",
    "description": "The outcome of the call",
    "enum": ["appointment_scheduled", "callback_requested",
             "not_interested", "not_viable"]
}
```

Enums prevent the LLM from inventing values and make your function handler simpler.

## Coordinating with the System Prompt

Function descriptions and the system prompt should reinforce each other. If a function requires confirmation before calling, say so in both places:

**In the function description:**
```python
description="...WAIT for the person to select a time..."
```

**In the system prompt:**
```
For book_appointment:
- Call AFTER the person selects a time slot
- Include the lead_id, selected datetime, and agent name
```

Redundancy is intentional. The LLM sees both the prompt and the function descriptions, and reinforcing critical behaviors in both locations makes them more reliable.

## Designing Return Values

The result you return from a function call becomes context for the LLM's next response. Design results for the LLM to read, not for a frontend to render:

```python
# Good - the LLM can speak this naturally
return {
    "available_slots": [
        {
            "datetime": "2026-03-05T10:00:00-06:00",
            "agent_name": "James Rivera",
            "display": "Thursday March 5 at 10 AM with James Rivera"
        }
    ],
}

# Bad - raw data the LLM has to interpret
return {
    "slots": [{"ts": 1736240400, "a": "JR", "type": 1}]
}
```

Include a `display` or `description` field with human-readable text. The LLM will use it almost verbatim, which gives you more control over what gets said.

### Error Results

Return errors as structured data, not exceptions:

```python
return {
    "success": False,
    "error": "That slot is no longer available. Please check availability again."
}
```

The LLM will incorporate the error message into its response naturally: "I'm sorry, it looks like that slot is no longer available. Would you like me to check for other openings?"

## The `update_lead` Pattern: Data Comes Back Out

The `update_lead` function is the most architecturally interesting function in this reference implementation. It demonstrates how voice agent output gets structured and piped back into business systems:

```python
description="""Post back the call outcome, disposition, and gathered information.
Call this at the END of every call, regardless of outcome.

This is the final record of the call. Include everything relevant:
what was verified, what new info was gathered, the disposition
assessment, and a natural language summary a human agent can read."""
```

The result isn't used by the agent (the call is ending). The value is in the structured payload that gets logged — in production, this would go to a CRM, webhook, or database.

Key design choices:
- Called at the end of **every** call, not just successful ones
- Includes both structured data (`verified_info`, `new_info_gathered`) and natural language (`call_summary`)
- The `call_summary` field is particularly valuable: a licensed agent can read it before their callback to walk in with full context
- Disposition values are constrained by enums, but the summary provides freeform detail

## How Many Functions?

Keep it to 2-5 functions per agent. More functions mean:
- The LLM has more choices to evaluate, increasing latency
- Higher chance of the LLM calling the wrong function
- Longer configuration messages sent to Deepgram

If you need more than 5 functions, consider whether some can be combined or whether you need a multi-agent architecture.

## Example: Complete Function Set

This reference implementation uses 4 functions:

| Function | Purpose | Confirmation Required? |
|---|---|---|
| `check_availability` | Look up consultation time slots | No (read-only) |
| `book_appointment` | Book a specific consultation slot | Yes |
| `update_lead` | Post back call outcome and gathered info | No (end-of-call) |
| `end_call` | Hang up the phone | No (natural conclusion) |

The read-only / write distinction maps directly to whether confirmation is needed. `update_lead` is an exception — it changes state but doesn't need confirmation because it's called at the end of the call as a recording of what happened, not an action the caller approves.

## See Also

- `voice_agent/agent_config.py` - The function definitions used in this reference
- `voice_agent/function_handlers.py` - How function calls are dispatched to the backend
- [PROMPT_GUIDE.md](PROMPT_GUIDE.md) - System prompt best practices
