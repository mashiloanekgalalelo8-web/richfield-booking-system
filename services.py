"""Core business logic: authentication, campuses, resources, bookings and reports."""
import sqlite3
from collections import Counter
from datetime import datetime, date, time

from database import hash_password
from models import Campus, user_from_row
from policies import POLICIES
from utils import AppError, parse_date, parse_time, parse_duration, fmt_minutes

RESOURCE_TYPES = ["Computer Lab", "Projector", "Seminar Room", "Presentation Equipment"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# --------------------------------------------------------------------------- auth
class AuthService:
    def __init__(self, db):
        self.db = db

    def register_lecturer(self, full_name, username, password, confirm, campus_id):
        full_name, username = full_name.strip(), username.strip()
        if not full_name or not username or not password:
            raise AppError("Please complete all fields.")
        if len(username) < 3:
            raise AppError("Username must be at least 3 characters.")
        if len(password) < 6:
            raise AppError("Password must be at least 6 characters.")
        if password != confirm:
            raise AppError("Passwords do not match.")
        if campus_id is None:
            raise AppError("Please choose your home campus.")
        if self.db.one("SELECT id FROM users WHERE username = ?", (username,)):
            raise AppError("That username is already taken.")
        salt, pw_hash = hash_password(password)
        self.db.execute(
            "INSERT INTO users (username, full_name, password_hash, salt, role, campus_id) "
            "VALUES (?,?,?,?,'lecturer',?)", (username, full_name, pw_hash, salt, campus_id))

    def login(self, username, password):
        row = self.db.one("SELECT * FROM users WHERE username = ?", (username.strip(),))
        if row:
            _, attempt = hash_password(password, row["salt"])
            if attempt == row["password_hash"]:
                return user_from_row(row)
        raise AppError("Invalid username or password.")


# ------------------------------------------------------------------------ campuses
class CampusService:
    def __init__(self, db):
        self.db = db

    def all(self):
        """Dictionary of campus id -> Campus object."""
        return {r["id"]: Campus(r["id"], r["name"], r["city"], r["policy_key"])
                for r in self.db.query("SELECT * FROM campuses ORDER BY id")}

    def add_campus(self, name, city, policy_key):
        """Expansion hook: add a new campus using any registered policy."""
        if policy_key not in POLICIES:
            raise AppError(f"Unknown booking policy '{policy_key}'.")
        return self.db.execute("INSERT INTO campuses (name, city, policy_key) VALUES (?,?,?)",
                               (name.strip(), city.strip(), policy_key))


# ------------------------------------------------------------------------ resources
class ResourceService:
    def __init__(self, db):
        self.db = db

    def codes(self):
        """Set of every resource code ever registered (used to reject duplicates)."""
        return {r["resource_code"] for r in self.db.query("SELECT resource_code FROM resources")}

    def list_for_campus(self, campus_id):
        return self.db.query("SELECT r.*, c.name AS campus_name FROM resources r "
                             "JOIN campuses c ON c.id = r.campus_id "
                             "WHERE r.campus_id = ? AND r.removed = 0 ORDER BY r.resource_code",
                             (campus_id,))

    def list_all(self):
        return self.db.query("SELECT r.*, c.name AS campus_name FROM resources r "
                             "JOIN campuses c ON c.id = r.campus_id "
                             "WHERE r.removed = 0 ORDER BY c.id, r.resource_code")

    def add(self, campus_id, code, name, rtype):
        code, name = code.strip().upper(), name.strip()
        if not code or not name:
            raise AppError("Resource code and name are required.")
        if rtype not in RESOURCE_TYPES:
            raise AppError("Please choose a valid resource type.")
        if code in self.codes():
            raise AppError(f"Duplicate resource: the code '{code}' is already registered.")
        try:
            return self.db.execute(
                "INSERT INTO resources (resource_code, name, resource_type, campus_id) "
                "VALUES (?,?,?,?)", (code, name, rtype, campus_id))
        except sqlite3.IntegrityError:
            raise AppError(f"Duplicate resource: the code '{code}' is already registered.")

    def update(self, resource_id, name, rtype, available):
        if not name.strip():
            raise AppError("Resource name is required.")
        if rtype not in RESOURCE_TYPES:
            raise AppError("Please choose a valid resource type.")
        self.db.execute("UPDATE resources SET name=?, resource_type=?, available=? WHERE id=?",
                        (name.strip(), rtype, 1 if available else 0, resource_id))

    def remove(self, resource_id):
        today = date.today().isoformat()
        future = self.db.one("SELECT COUNT(*) AS n FROM bookings WHERE resource_id=? AND "
                             "status='active' AND booking_date >= ?", (resource_id, today))
        if future["n"]:
            raise AppError("This resource still has current/future bookings. "
                           "Cancel them first, or mark the resource as out of service.")
        self.db.execute("UPDATE resources SET removed = 1 WHERE id = ?", (resource_id,))


# ------------------------------------------------------------------------- bookings
BOOKING_SELECT = """
SELECT b.id, b.booking_date, b.start_time, b.duration_min, b.status, b.created_at,
       b.resource_id, b.lecturer_id, b.campus_id,
       r.resource_code, r.name AS resource_name, r.resource_type,
       c.name AS campus_name, u.full_name AS lecturer_name
FROM bookings b
JOIN resources r ON r.id = b.resource_id
JOIN campuses  c ON c.id = b.campus_id
JOIN users     u ON u.id = b.lecturer_id
"""


class BookingService:
    def __init__(self, db):
        self.db = db

    # -- helpers
    @staticmethod
    def _decorate(row, now):
        start = parse_time(row["start_time"])
        d = date.fromisoformat(row["booking_date"])
        start_dt = datetime.combine(d, time(start // 60, start % 60))
        end_dt = datetime.fromtimestamp(start_dt.timestamp() + row["duration_min"] * 60)
        row["end_time"] = fmt_minutes((start + row["duration_min"]) % 1440)
        if row["status"] == "cancelled":
            row["state"] = "Cancelled"
        elif end_dt <= now:
            row["state"] = "Completed"
        elif start_dt <= now:
            row["state"] = "In progress"
        else:
            row["state"] = "Upcoming"
        return row

    def _fetch(self, where="", params=()):
        now = datetime.now()
        sql = BOOKING_SELECT + (f" WHERE {where}" if where else "") + \
              " ORDER BY b.booking_date DESC, b.start_time DESC"
        return [self._decorate(r, now) for r in self.db.query(sql, params)]

    def for_lecturer(self, lecturer_id):
        return self._fetch("b.lecturer_id = ?", (lecturer_id,))

    def for_campus(self, campus_id):
        return self._fetch("b.campus_id = ?", (campus_id,))

    def for_all(self):
        return self._fetch()

    # -- availability
    def slot_free(self, resource_id, booking_date, start_min, duration):
        rows = self.db.query("SELECT start_time, duration_min FROM bookings WHERE resource_id=? "
                             "AND booking_date=? AND status='active'",
                             (resource_id, booking_date.isoformat()))
        end_min = start_min + duration
        for r in rows:
            s = parse_time(r["start_time"])
            if start_min < s + r["duration_min"] and end_min > s:
                return False
        return True

    def resource_status(self, resource, now=None):
        """Real-time status label for a resource row."""
        now = now or datetime.now()
        if not resource["available"]:
            return "Out of service"
        now_min = now.hour * 60 + now.minute
        for r in self.db.query("SELECT start_time, duration_min FROM bookings WHERE resource_id=? "
                               "AND booking_date=? AND status='active'",
                               (resource["id"], now.date().isoformat())):
            s = parse_time(r["start_time"])
            if s <= now_min < s + r["duration_min"]:
                return "In use now"
        return "Available"

    def slot_status(self, resource, booking_date, start_min, duration):
        if not resource["available"]:
            return "Out of service"
        return "Free" if self.slot_free(resource["id"], booking_date, start_min, duration) else "Booked"

    # -- create / cancel
    def create(self, user, campus, resource_id, date_text, start_text, duration_text):
        if not user.has_permission("book"):
            raise AppError("Your role is not allowed to create bookings.")
        resource = self.db.one("SELECT * FROM resources WHERE id=? AND removed=0", (resource_id,))
        if not resource:
            raise AppError("Please select a resource from the list first.")
        if resource["campus_id"] != campus.id:
            raise AppError("That resource does not belong to the active campus.")
        if not resource["available"]:
            raise AppError("This resource is currently out of service and cannot be booked.")
        booking_date = parse_date(date_text)
        start_min = parse_time(start_text)
        duration = parse_duration(duration_text)
        start_dt = datetime.combine(booking_date, time(start_min // 60, start_min % 60))
        if start_dt <= datetime.now():
            raise AppError("Bookings must start in the future.")
        errors = campus.policy.validate(resource, booking_date, start_min, duration)
        if errors:
            self.db.execute("INSERT INTO rejected_attempts (campus_id, lecturer_id, reason) "
                            "VALUES (?,?,?)", (campus.id, user.id, errors[0]))
            raise AppError("Rejected by the campus booking policy:\n\n- " + "\n- ".join(errors))
        if not self.slot_free(resource["id"], booking_date, start_min, duration):
            raise AppError("This resource is already booked for an overlapping time. "
                           "Please choose a different time.")
        return self.db.execute(
            "INSERT INTO bookings (resource_id, lecturer_id, campus_id, booking_date, start_time, "
            "duration_min) VALUES (?,?,?,?,?,?)",
            (resource["id"], user.id, campus.id, booking_date.isoformat(),
             fmt_minutes(start_min), duration))

    def cancel(self, user, booking_id):
        if not user.has_permission("cancel_own_booking"):
            raise AppError("Your role is not allowed to cancel bookings.")
        rows = self._fetch("b.id = ?", (booking_id,))
        if not rows:
            raise AppError("Booking not found.")
        booking = rows[0]
        if booking["lecturer_id"] != user.id:
            raise AppError("You can only cancel your own bookings.")
        if booking["state"] != "Upcoming":
            raise AppError("Only future bookings can be cancelled "
                           f"(this one is {booking['state'].lower()}).")
        self.db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))


# --------------------------------------------------------------------------- reports
class ReportService:
    def __init__(self, db, campuses):
        self.db = db
        self.campuses = campuses

    def campus_report(self, campus_id):
        rows = self.db.query(
            "SELECT b.booking_date, b.duration_min, r.name AS rname, r.resource_code "
            "FROM bookings b JOIN resources r ON r.id = b.resource_id "
            "WHERE b.campus_id = ? AND b.status = 'active'", (campus_id,))
        total = len(rows)
        minutes = sum(r["duration_min"] for r in rows)
        per_resource = Counter(f"{r['resource_code']}  {r['rname']}" for r in rows)
        per_weekday = Counter(date.fromisoformat(r["booking_date"]).weekday() for r in rows)
        cancelled = self.db.one("SELECT COUNT(*) AS n FROM bookings WHERE campus_id=? "
                                "AND status='cancelled'", (campus_id,))["n"]
        rejected = self.db.one("SELECT COUNT(*) AS n FROM rejected_attempts WHERE campus_id=?",
                               (campus_id,))["n"]
        resources = self.db.one("SELECT COUNT(*) AS n FROM resources WHERE campus_id=? "
                                "AND removed=0", (campus_id,))["n"]
        return {
            "total": total,
            "average_duration": round(minutes / total, 1) if total else 0,
            "total_minutes": minutes,
            "cancelled": cancelled,
            "rejected": rejected,
            "resources": resources,
            "top_resources": per_resource.most_common(),
            "by_weekday": [(WEEKDAYS[i], per_weekday.get(i, 0)) for i in range(5)],
        }

    def comparison(self):
        """One entry per campus, combining usage figures with its booking policy."""
        result = []
        for campus in self.campuses.all().values():
            rep = self.campus_report(campus.id)
            p = campus.policy
            result.append({
                "campus": campus,
                "policy": p,
                "report": rep,
                "per_resource": round(rep["total"] / rep["resources"], 1) if rep["resources"] else 0,
                "top": rep["top_resources"][0][0] if rep["top_resources"] else "-",
            })
        return result
