"""
Mock lead service - in-memory lead management and consultation scheduling.

This simulates a real CRM/scheduling backend with an async interface that
looks like HTTP API calls.  To swap this for a real backend, replace the
method bodies with actual HTTP requests - the voice agent layer doesn't
need to change.

The three main operations match the function calls in agent_config.py:
  - check_availability: Return available consultation slots
  - book_appointment: Book a consultation with a licensed agent
  - update_lead: Post back call outcome and gathered information
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from backend.models import Lead, PropertyAddress, ConsultationSlot, Appointment

logger = logging.getLogger(__name__)

# Licensed agents who handle consultations
LICENSED_AGENTS = [
    "James Rivera",
    "Monica Chen",
    "David Park",
]


def build_default_lead(phone: str) -> Lead:
    """Build the default mock lead template with the given phone number.

    This is the "Alex Mitchell" lead from the scenario spec.  Developers
    can simply provide their own phone number and get a realistic demo call.
    """
    return Lead(
        lead_id=f"lead_{uuid.uuid4().hex[:6]}",
        first_name="Alex",
        last_name="Mitchell",
        phone=phone,
        email="alex.mitchell@email.com",
        property_address=PropertyAddress(
            street="742 Evergreen Terrace",
            city="Springfield",
            state="IL",
            zip="62704",
        ),
        property_type="single_family",
        year_built=1992,
        square_footage=2100,
        current_insurance_status="switching",
        desired_coverage_start="2026-04-15",
        quote_submitted_at="2026-02-28T14:32:00Z",
        source="website_quote_form",
    )


def build_lead_from_dict(phone: str, data: dict) -> Lead:
    """Build a Lead from API request data, using defaults for missing fields."""
    address_data = data.get("property_address", {})
    return Lead(
        lead_id=f"lead_{uuid.uuid4().hex[:6]}",
        first_name=data.get("first_name", "Alex"),
        last_name=data.get("last_name", "Mitchell"),
        phone=phone,
        email=data.get("email", "customer@email.com"),
        property_address=PropertyAddress(
            street=address_data.get("street", "742 Evergreen Terrace"),
            city=address_data.get("city", "Springfield"),
            state=address_data.get("state", "IL"),
            zip=address_data.get("zip", "62704"),
        ),
        property_type=data.get("property_type", "single_family"),
        year_built=data.get("year_built", 1992),
        square_footage=data.get("square_footage", 2100),
        current_insurance_status=data.get("current_insurance_status", "switching"),
        desired_coverage_start=data.get("desired_coverage_start", "2026-04-15"),
        quote_submitted_at=data.get("quote_submitted_at", datetime.now().isoformat()),
        source=data.get("source", "website_quote_form"),
    )


class LeadService:
    """In-memory lead management and consultation scheduling backend."""

    def __init__(self):
        self.appointments: dict[str, Appointment] = {}

    def _generate_consultation_slots(self) -> list[ConsultationSlot]:
        """Generate realistic consultation slots for the next few business days.

        Returns 4-5 slots spread across 2-3 days with different agents,
        which is a realistic number for a voice conversation.
        """
        slots = []
        today = datetime.now().date()
        current = today + timedelta(days=1)
        days_found = 0

        # Find the next 3 business days
        business_days = []
        while days_found < 3:
            if current.weekday() < 5:  # Monday-Friday
                business_days.append(current)
                days_found += 1
            current += timedelta(days=1)

        # Create slots: morning and afternoon on each day
        slot_times = [
            (10, 0),   # 10:00 AM
            (14, 0),   # 2:00 PM
        ]

        agent_idx = 0
        for day in business_days:
            for hour, minute in slot_times:
                dt = datetime(day.year, day.month, day.day, hour, minute)
                # Use a fixed timezone offset (Central Time, -06:00)
                iso_str = dt.strftime("%Y-%m-%dT%H:%M:%S") + "-06:00"
                slots.append(ConsultationSlot(
                    datetime=iso_str,
                    agent_name=LICENSED_AGENTS[agent_idx % len(LICENSED_AGENTS)],
                ))
                agent_idx += 1

        # Return 3-4 slots (a reasonable number to offer over the phone)
        return slots[:4]

    # ------------------------------------------------------------------
    # Public API - these are the methods the voice agent calls
    # ------------------------------------------------------------------

    async def check_availability(self, lead_id: str, timezone: Optional[str] = None) -> dict:
        """Get available consultation time slots with licensed agents.

        In a real system, this would query a scheduling/CRM API.
        Here we generate realistic mock slots.
        """
        slots = self._generate_consultation_slots()

        logger.info(f"[BACKEND] check_availability for {lead_id}: {len(slots)} slots available")

        return {
            "available_slots": [
                {
                    "datetime": s.datetime,
                    "agent_name": s.agent_name,
                    "display": s.display(),
                }
                for s in slots
            ],
        }

    async def book_appointment(
        self, lead_id: str, selected_slot: str, agent_name: str
    ) -> dict:
        """Book a consultation slot with a licensed agent.

        In a real system, this would create a calendar event and potentially
        trigger a confirmation SMS/email.  Here we log the booking.
        """
        confirmation_id = f"appt_{uuid.uuid4().hex[:6]}"

        slot = ConsultationSlot(datetime=selected_slot, agent_name=agent_name)
        appointment = Appointment(
            confirmation_id=confirmation_id,
            lead_id=lead_id,
            slot=slot,
        )
        self.appointments[confirmation_id] = appointment

        logger.info(f"[BACKEND] Booked: {appointment.display()}")

        return {
            "confirmation_id": confirmation_id,
            "status": "confirmed",
            "datetime": selected_slot,
            "agent_name": agent_name,
        }

    async def update_lead(
        self,
        lead_id: str,
        call_outcome: str,
        disposition: str,
        appointment_id: Optional[str] = None,
        verified_info: Optional[dict] = None,
        new_info_gathered: Optional[dict] = None,
        call_summary: Optional[str] = None,
    ) -> dict:
        """Post back the call outcome and gathered information.

        This is the most architecturally interesting function for developers.
        It demonstrates how voice agent output gets structured and piped back
        into business systems.

        In a real deployment, this payload would go to a CRM, a webhook, a
        database, or an internal API.  Here we log it prominently to the
        console so developers can see the "data comes back out" loop.
        """
        result = {
            "lead_id": lead_id,
            "call_outcome": call_outcome,
            "disposition": disposition,
            "timestamp": datetime.now().isoformat(),
        }
        if appointment_id:
            result["appointment_id"] = appointment_id
        if verified_info:
            result["verified_info"] = verified_info
        if new_info_gathered:
            result["new_info_gathered"] = new_info_gathered
        if call_summary:
            result["call_summary"] = call_summary

        # Log prominently - this is the key output developers want to see
        logger.info("")
        logger.info("=" * 70)
        logger.info("  LEAD UPDATE - Call outcome posted back to CRM")
        logger.info("=" * 70)
        logger.info(json.dumps(result, indent=2))
        logger.info("=" * 70)
        logger.info("")

        return {"status": "updated", "lead_id": lead_id}


# Singleton - created once at import time, shared across all calls.
# In production, you'd replace this with an HTTP client to your real CRM.
lead_service = LeadService()
