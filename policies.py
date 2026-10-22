"""Campus-specific booking policies.

Booking rules live here, completely separate from the core booking logic.
To add a new rule set for a new campus:

    1. Subclass BookingPolicy (override the class attributes / extra_rules).
    2. Decorate it with @register_policy.
    3. Create the campus with that policy's key (CampusService.add_campus).

No other file needs to change.
"""
from utils import fmt_minutes

POLICIES = {}  # dictionary: policy key -> policy instance


def register_policy(cls):
    POLICIES[cls.key] = cls()
    return cls


class BookingPolicy:
    key = "standard"
    label = "Standard Policy"
    max_duration = 120          # minutes per session
    open_minute = 8 * 60        # earliest start
    close_minute = 17 * 60      # latest finish
    max_advance_days = 30       # how far ahead bookings may be made
    priority_note = ""          # human-readable description of priority rules

    def summary(self):
        text = (f"{self.label}: max {self.max_duration} min per session | "
                f"hours {fmt_minutes(self.open_minute)}-{fmt_minutes(self.close_minute)} | "
                f"up to {self.max_advance_days} days ahead")
        if self.priority_note:
            text += f" | Priority: {self.priority_note}"
        return text

    def validate(self, resource, booking_date, start_min, duration):
        """Return a list of rule violations (empty list = booking allowed)."""
        errors = []
        if duration > self.max_duration:
            errors.append(f"Maximum booking duration is {self.max_duration} minutes "
                          f"(you asked for {duration}).")
        if start_min < self.open_minute or start_min + duration > self.close_minute:
            errors.append(f"Bookings must fall within {fmt_minutes(self.open_minute)} and "
                          f"{fmt_minutes(self.close_minute)} on this campus.")
        from datetime import date
        if (booking_date - date.today()).days > self.max_advance_days:
            errors.append(f"Bookings can only be made up to {self.max_advance_days} days in advance.")
        errors.extend(self.extra_rules(resource, booking_date, start_min, duration))
        return errors

    def extra_rules(self, resource, booking_date, start_min, duration):
        return []


@register_policy
class JohannesburgPolicy(BookingPolicy):
    key = "jhb"
    label = "Johannesburg Extended-Hours Policy"
    max_duration = 120
    open_minute = 7 * 60
    close_minute = 20 * 60
    max_advance_days = 30


@register_policy
class DurbanPolicy(BookingPolicy):
    key = "dbn"
    label = "Durban Long-Session Policy"
    max_duration = 180
    open_minute = 8 * 60
    close_minute = 17 * 60
    max_advance_days = 21
    priority_note = "Computer labs are reserved for assessments on Fridays"

    def extra_rules(self, resource, booking_date, start_min, duration):
        if resource["resource_type"] == "Computer Lab" and booking_date.weekday() == 4:
            return ["Computer labs cannot be booked on Fridays (reserved for assessments)."]
        return []


@register_policy
class CapeTownPolicy(BookingPolicy):
    key = "cpt"
    label = "Cape Town Short-Session Policy"
    max_duration = 90
    open_minute = 8 * 60
    close_minute = 18 * 60
    max_advance_days = 14
    priority_note = "Seminar rooms are reserved for faculty events after 15:00"

    def extra_rules(self, resource, booking_date, start_min, duration):
        if resource["resource_type"] == "Seminar Room" and start_min + duration > 15 * 60:
            return ["Seminar rooms must finish by 15:00 (priority access for faculty events)."]
        return []


def get_policy(key):
    return POLICIES.get(key, POLICIES.get("jhb"))
