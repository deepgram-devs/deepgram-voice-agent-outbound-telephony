"""
Function dispatch - routes agent function calls to the backend lead service.

Each function the agent can call (defined in agent_config.py) maps to a method
on the lead service.  This module is the bridge between the voice agent
layer and the backend layer.

To swap the mock backend for a real API, you only need to change the imports
and method calls here - the voice agent layer doesn't know or care whether
the backend is in-memory or a remote HTTP service.
"""
import logging

logger = logging.getLogger(__name__)


async def dispatch_function(name: str, args: dict) -> dict:
    """Dispatch a function call to the appropriate backend handler.

    Args:
        name: Function name (matches names in agent_config.FUNCTIONS)
        args: Parsed arguments from the LLM

    Returns:
        Result dict that gets sent back to the agent as context for its next response.
    """
    # Lazy import - keeps the backend dependency explicit and avoids
    # circular imports during startup.
    from backend.lead_service import lead_service

    if name == "check_availability":
        return await lead_service.check_availability(
            lead_id=args.get("lead_id", ""),
            timezone=args.get("timezone"),
        )

    elif name == "book_appointment":
        return await lead_service.book_appointment(
            lead_id=args["lead_id"],
            selected_slot=args["selected_slot"],
            agent_name=args["agent_name"],
        )

    elif name == "update_lead":
        return await lead_service.update_lead(
            lead_id=args.get("lead_id", ""),
            call_outcome=args.get("call_outcome", ""),
            disposition=args.get("disposition", ""),
            appointment_id=args.get("appointment_id"),
            verified_info=args.get("verified_info"),
            new_info_gathered=args.get("new_info_gathered"),
            call_summary=args.get("call_summary"),
        )

    elif name == "end_call":
        reason = args.get("reason", "appointment_booked")
        logger.info(f"Call ending: {reason}")
        return {"status": "call_ended", "reason": reason}

    else:
        logger.warning(f"Unknown function: {name}")
        return {"error": f"Unknown function: {name}"}
