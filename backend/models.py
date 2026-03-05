"""
Data models for the lead management backend.

These are plain dataclasses - no ORM, no database.  They define the shape
of the data that flows between the voice agent and the lead service.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class PropertyAddress:
    """A property's mailing address."""
    street: str
    city: str
    state: str
    zip: str

    def display(self) -> str:
        return f"{self.street}, {self.city}, {self.state} {self.zip}"


@dataclass
class Lead:
    """A lead from the CRM - represents a consumer who submitted a quote request."""
    lead_id: str
    first_name: str
    last_name: str
    phone: str
    email: str
    property_address: PropertyAddress
    property_type: str              # single_family, condo, townhouse, mobile_home
    year_built: int
    square_footage: int
    current_insurance_status: str   # switching, first_time_buyer, lapsed
    desired_coverage_start: str     # YYYY-MM-DD
    quote_submitted_at: str         # ISO 8601 timestamp
    source: str = "website_quote_form"

    def display(self) -> str:
        """Human-readable summary for logging."""
        return (
            f"{self.first_name} {self.last_name} | "
            f"{self.property_address.display()} | "
            f"{self.property_type} | "
            f"Coverage start: {self.desired_coverage_start}"
        )


@dataclass
class ConsultationSlot:
    """An available time slot for a licensed agent consultation."""
    datetime: str       # ISO 8601 with timezone (e.g. 2026-03-05T10:00:00-06:00)
    agent_name: str

    def display(self) -> str:
        """Human-readable description for the agent to read aloud."""
        try:
            dt = datetime_module.fromisoformat(self.datetime)
            # Format like "Thursday March 5th at 10 AM"
            hour = dt.hour
            period = "AM" if hour < 12 else "PM"
            display_hour = hour if hour <= 12 else hour - 12
            if display_hour == 0:
                display_hour = 12
            return f"{dt.strftime('%A %B %-d')} at {display_hour} {period} with {self.agent_name}"
        except Exception:
            return f"{self.datetime} with {self.agent_name}"


# Alias to avoid shadowing the datetime field name
import datetime as datetime_module


@dataclass
class Appointment:
    """A booked consultation with a licensed agent."""
    confirmation_id: str
    lead_id: str
    slot: ConsultationSlot
    booked_at: datetime = field(default_factory=datetime.now)

    def display(self) -> str:
        return f"Consultation {self.confirmation_id}: {self.slot.display()}"
