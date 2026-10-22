"""SQLite persistence layer. All data survives between program runs."""
import hashlib
import os
import random
import secrets
import sqlite3
from datetime import date, timedelta

from policies import get_policy

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "richfield_booking.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS campuses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    policy_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('lecturer','campus_admin','system_operator')),
    campus_id INTEGER REFERENCES campuses(id)
);
CREATE TABLE IF NOT EXISTS resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    campus_id INTEGER NOT NULL REFERENCES campuses(id),
    available INTEGER NOT NULL DEFAULT 1,
    removed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL REFERENCES resources(id),
    lecturer_id INTEGER NOT NULL REFERENCES users(id),
    campus_id INTEGER NOT NULL REFERENCES campuses(id),
    booking_date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rejected_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campus_id INTEGER NOT NULL REFERENCES campuses(id),
    lecturer_id INTEGER REFERENCES users(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bookings_resource_date ON bookings(resource_id, booking_date);
CREATE INDEX IF NOT EXISTS idx_bookings_lecturer ON bookings(lecturer_id);
"""

SEED_CAMPUSES = [
    ("Richfield Johannesburg", "Johannesburg", "jhb", "JHB"),
    ("Richfield Durban", "Durban", "dbn", "DBN"),
    ("Richfield Cape Town", "Cape Town", "cpt", "CPT"),
]
SEED_RESOURCES = [
    ("LAB-01", "Computer Lab A", "Computer Lab"),
    ("LAB-02", "Computer Lab B", "Computer Lab"),
    ("PRJ-01", "Multimedia Projector 1", "Projector"),
    ("PRJ-02", "Multimedia Projector 2", "Projector"),
    ("SEM-01", "Seminar Room 1", "Seminar Room"),
    ("PRE-01", "Presentation Kit 1", "Presentation Equipment"),
]


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120_000)
    return salt, digest.hex()


class Database:
    def __init__(self, path=DB_FILE):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        if not self.one("SELECT id FROM campuses LIMIT 1"):
            self._seed()

    # ---- thin helpers -------------------------------------------------------
    def query(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def execute(self, sql, params=()):
        with self.conn:
            return self.conn.execute(sql, params).lastrowid

    def close(self):
        self.conn.close()

    # ---- first-run seed data --------------------------------------------------
    def _add_user(self, username, full_name, password, role, campus_id):
        salt, pw_hash = hash_password(password)
        return self.execute(
            "INSERT INTO users (username, full_name, password_hash, salt, role, campus_id) "
            "VALUES (?,?,?,?,?,?)", (username, full_name, pw_hash, salt, role, campus_id))

    def _seed(self):
        campus_ids = {}
        for name, city, policy_key, prefix in SEED_CAMPUSES:
            cid = self.execute("INSERT INTO campuses (name, city, policy_key) VALUES (?,?,?)",
                               (name, city, policy_key))
            campus_ids[prefix] = cid
            for code, rname, rtype in SEED_RESOURCES:
                self.execute(
                    "INSERT INTO resources (resource_code, name, resource_type, campus_id) "
                    "VALUES (?,?,?,?)", (f"{prefix}-{code}", rname, rtype, cid))
            self._add_user(f"admin_{prefix.lower()}", f"{city} Administrator",
                           "Admin@123", "campus_admin", cid)
        self._add_user("operator", "Head Office Operator", "Operator@123", "system_operator", None)
        lecturer_id = self._add_user("lecturer", "Demo Lecturer", "Lecturer@123",
                                     "lecturer", campus_ids["JHB"])
        self._seed_demo_bookings(lecturer_id)
        self._seed_demo_rejections(lecturer_id, campus_ids)

    def _seed_demo_bookings(self, lecturer_id):
        """Sample history so the reports have something to show on first run."""
        rng = random.Random(7)
        resources = self.query("SELECT r.*, c.policy_key FROM resources r "
                               "JOIN campuses c ON c.id = r.campus_id")
        taken = []  # (resource_id, date, start, end)
        today = date.today()
        for _ in range(70):
            res = rng.choice(resources)
            policy = get_policy(res["policy_key"])
            offset = rng.choice([d for d in range(-25, 5) if d != 0])
            day = today + timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            duration = min(rng.choice([30, 45, 60, 90, 120, 150]), policy.max_duration)
            latest_start = policy.close_minute - duration
            if latest_start < policy.open_minute:
                continue
            start = rng.randrange(policy.open_minute, latest_start + 1, 30)
            if policy.validate(res, day, start, duration):
                continue
            end = start + duration
            if any(t[0] == res["id"] and t[1] == day and start < t[3] and end > t[2] for t in taken):
                continue
            taken.append((res["id"], day, start, end))
            status = "cancelled" if offset > 0 and rng.random() < 0.2 else "active"
            self.execute(
                "INSERT INTO bookings (resource_id, lecturer_id, campus_id, booking_date, "
                "start_time, duration_min, status) VALUES (?,?,?,?,?,?,?)",
                (res["id"], lecturer_id, res["campus_id"], day.isoformat(),
                 f"{start // 60:02d}:{start % 60:02d}", duration, status))

    def _seed_demo_rejections(self, lecturer_id, campus_ids):
        """Sample policy rejections so the cross-campus comparison shows rule effects."""
        samples = {
            "JHB": ["Maximum booking duration is 120 minutes (you asked for 150)."] * 2,
            "DBN": ["Computer labs cannot be booked on Fridays (reserved for assessments)."] * 4
                   + ["Bookings must fall within 08:00 and 17:00 on this campus."],
            "CPT": ["Maximum booking duration is 90 minutes (you asked for 120)."] * 3
                   + ["Seminar rooms must finish by 15:00 (priority access for faculty events)."] * 2,
        }
        for prefix, reasons in samples.items():
            for reason in reasons:
                self.execute("INSERT INTO rejected_attempts (campus_id, lecturer_id, reason) "
                             "VALUES (?,?,?)", (campus_ids[prefix], lecturer_id, reason))
