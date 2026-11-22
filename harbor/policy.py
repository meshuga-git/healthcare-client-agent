"""Synthetic US demo security controls; not a clinical or compliance system."""

from __future__ import annotations
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable
from cryptography.fernet import Fernet

EMERGENCY_TEXT = (
    "If this is an emergency or you are in immediate danger, call 911 now. "
    "This scheduling demo cannot provide emergency care. Do not wait for a reply here."
)
CLINICAL_TEXT = (
    "I cannot provide medical advice, assess symptoms, recommend medication, or interpret "
    "results. I have added your question to the local demonstration queue; no real staff have "
    "been contacted. If there may be immediate danger, "
    "contact local emergency services now rather than waiting here."
)
IDENTITY_TEXT = "We could not verify those details. Please contact the practice for assistance."


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class SafetyResult:
    kind: str
    message: str
    rule: str


# Intentionally conservative lexical baseline. Not a validated clinical classifier.
EMERGENCY_PATTERNS = (
    r"\b(?:emergency|suicid\w*|overdos\w*|unconscious|unresponsive)\b",
    r"\b(?:kill|hurt|harm)\s+(?:myself|my self|yourself|someone|them|him|her)\b",
    r"\b(?:can'?t|cannot|not|difficulty|trouble)\s+(?:breathe|breathing)\b",
    r"\b(?:chest pain|severe bleeding|bleeding heavily|stop breathing|end my life)\b",
)
CLINICAL_PATTERNS = (
    r"\b(?:symptom\w*|medication\w*|dosage|dose|diagnos\w*|prescri\w*|treatment|pain|rash|fever|dizz\w*)\b",
    r"\b(?:test|lab|blood)\s+results?\b",
    r"\b(?:pregnan\w*|infection|antibiotic\w*|side effects?|medical advice)\b",
)


def safety_screen(
    text: str, emergency_text: str = EMERGENCY_TEXT, clinical_text: str = CLINICAL_TEXT
) -> SafetyResult | None:
    normalized = re.sub(r"[\u200b-\u200f\ufeff]", "", text.casefold()).replace("’", "'")
    if any(re.search(p, normalized) for p in EMERGENCY_PATTERNS):
        return SafetyResult("emergency", emergency_text, "H02")
    if any(re.search(p, normalized) for p in CLINICAL_PATTERNS):
        return SafetyResult("clinical", clinical_text, "H03")
    return None


class Privacy:
    def __init__(self, key: str | bytes):
        self.key = key.encode() if isinstance(key, str) else key
        self.fernet = Fernet(self.key)

    def encrypt(self, value) -> str:
        return self.fernet.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, value: str):
        return json.loads(self.fernet.decrypt(value.encode()))

    def fingerprint(self, value: str) -> str:
        return hmac.new(self.key, value.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def redact(text: str, known_values: tuple[str, ...] = ()) -> str:
        for value in sorted(known_values, key=len, reverse=True):
            if value:
                text = re.sub(re.escape(value), "[REDACTED]", text, flags=re.I)
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", text)
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "[DATE]", text)
        text = re.sub(r"(?<!\w)\+?\d[\d ()-]{7,}\d", "[PHONE]", text)
        return text


class IdentityStore:
    """DOB + phone demonstration factors, short-lived opaque location-scoped tokens.

    Not strong identity proofing: demo knowledge factors must be replaced by a reviewed
    possession-factor flow before use with actual patients. Never log supplied factors.
    """

    def __init__(
        self,
        path: str,
        privacy: Privacy,
        clock: Callable[[], float] = time.time,
        max_attempts: int = 5,
        lock_seconds: int = 900,
        token_seconds: int = 900,
    ):
        self.path, self.privacy, self.clock = path, privacy, clock
        self.max_attempts, self.lock_seconds, self.token_seconds = max_attempts, lock_seconds, token_seconds
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript("""
        CREATE TABLE IF NOT EXISTS identities(patient_id TEXT PRIMARY KEY, location_id TEXT NOT NULL,
          contact_hash TEXT NOT NULL, encrypted TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS identity_attempts(scope TEXT PRIMARY KEY, attempts INTEGER, locked_until REAL);
        CREATE TABLE IF NOT EXISTS identity_tokens(token_hash TEXT PRIMARY KEY, patient_id TEXT, location_id TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS identity_audit(id INTEGER PRIMARY KEY, ts REAL, action TEXT, subject_ref TEXT, fields_accessed TEXT);
        """)
        _otp_tables(self)
        import threading

        self._lock = threading.RLock()

    @staticmethod
    def normalize_phone(phone: str) -> str:
        return re.sub(r"\D", "", phone)

    def register(self, patient_id: str, location_id: str, dob: str, phone: str, **personal):
        from datetime import date

        date.fromisoformat(dob)
        phone = self.normalize_phone(phone)
        if len(phone) < 10:
            raise PolicyError("Invalid synthetic contact")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO identities VALUES (?,?,?,?)",
                (
                    patient_id,
                    location_id,
                    self.privacy.fingerprint(phone),
                    self.privacy.encrypt(dict(dob=dob, phone=phone, **personal)),
                ),
            )

    def _audit(self, action, subject, fields):
        self._conn.execute(
            "INSERT INTO identity_audit(ts,action,subject_ref,fields_accessed) VALUES (?,?,?,?)",
            (self.clock(), action, subject, json.dumps(fields)),
        )

    def verify(self, contact: str, dob: str, location_id: str, conversation_id: str) -> str | None:
        contact_hash = self.privacy.fingerprint(self.normalize_phone(contact))
        scopes = ["contact:" + contact_hash, "conversation:" + self.privacy.fingerprint(conversation_id)]
        with self._lock, self._conn:
            states = [
                self._conn.execute("SELECT attempts,locked_until FROM identity_attempts WHERE scope=?", (s,)).fetchone()
                for s in scopes
            ]
            if any(state and state[1] > self.clock() for state in states):
                self._audit("verification_locked", None, [])
                return None
            row = self._conn.execute(
                "SELECT patient_id,encrypted FROM identities WHERE contact_hash=? AND location_id=?",
                (contact_hash, location_id),
            ).fetchone()
            data = self.privacy.decrypt(row[1]) if row else {"dob": "invalid"}
            self._audit("verification_factor_access", row[0] if row else None, ["dob"] if row else [])
            valid = row is not None and hmac.compare_digest(data["dob"], dob)
            if not valid:
                for scope, state in zip(scopes, states):
                    count = (state[0] if state and state[1] == 0 else 0) + 1
                    until = self.clock() + self.lock_seconds if count >= self.max_attempts else 0
                    self._conn.execute("INSERT OR REPLACE INTO identity_attempts VALUES(?,?,?)", (scope, count, until))
                self._audit("verification_failed", None, [])
                if any((s[0] if s else 0) + 1 >= self.max_attempts for s in states):
                    self._audit("staff_alert_lockout", None, [])
                return None
            for scope in scopes:
                self._conn.execute("DELETE FROM identity_attempts WHERE scope=?", (scope,))
            token = secrets.token_urlsafe(32)
            self._conn.execute(
                "INSERT INTO identity_tokens VALUES(?,?,?,?)",
                (self.privacy.fingerprint(token), row[0], location_id, self.clock() + self.token_seconds),
            )
            self._audit("verification_success", row[0], [])
            return token

    def require(self, token: str, location_id: str) -> str:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT patient_id,location_id,expires FROM identity_tokens WHERE token_hash=?",
                (self.privacy.fingerprint(token),),
            ).fetchone()
            if not row or row[1] != location_id or row[2] <= self.clock():
                raise PolicyError(IDENTITY_TEXT)
            self._audit("authorized_patient_access", row[0], ["patient_ref"])
            return row[0]

    def personal_values(self, patient_id: str) -> tuple[str, ...]:
        """Internal-only redaction vocabulary; never expose via public API."""
        with self._lock, self._conn:
            row = self._conn.execute("SELECT encrypted FROM identities WHERE patient_id=?", (patient_id,)).fetchone()
            if not row:
                return ()
            fields = self.privacy.decrypt(row[0])
            self._audit("redaction_vocabulary_access", patient_id, list(fields))
            values = []
            for value in fields.values():
                if isinstance(value, str):
                    values.append(value)
                    if " " in value:
                        values.extend(part for part in value.split() if len(part) > 2)
            return tuple(values)

    def delete(self, patient_id: str):
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM identities WHERE patient_id=?", (patient_id,))
            self._conn.execute("DELETE FROM identity_tokens WHERE patient_id=?", (patient_id,))
            self._conn.execute("DELETE FROM otp_challenges WHERE patient_id=?", (patient_id,))
            self._conn.execute("DELETE FROM otp_outbox WHERE patient_id=?", (patient_id,))
            self._audit("patient_deleted", patient_id, [])

    def close(self):
        self._conn.close()


ROLE_ACTIONS = {
    "guest": {"summary"},
    "front_desk": {"summary", "schedule", "escalations", "conversations", "booking", "intake_completeness"},
    "provider": {"summary", "schedule", "escalations", "intake", "intake_completeness"},
    "admin": {
        "summary",
        "schedule",
        "escalations",
        "conversations",
        "booking",
        "intake",
        "intake_completeness",
        "audit",
        "settings",
        "delete",
    },
}


def authorize_staff(role: str, allowed_locations: list[str], location_id: str, action: str):
    if location_id not in allowed_locations or action not in ROLE_ACTIONS.get(role, set()):
        raise PolicyError("Access denied")


# OTP delivery is a private, encrypted LOCAL outbox. No SMS or email is sent.
def _otp_tables(self):
    self._conn.executescript("""
    CREATE TABLE IF NOT EXISTS otp_challenges(scope TEXT PRIMARY KEY, patient_id TEXT,
      location_id TEXT, code_hash TEXT, expires REAL, attempts INTEGER, consumed INTEGER,
      requested REAL, locked_until REAL);
    CREATE TABLE IF NOT EXISTS otp_outbox(id INTEGER PRIMARY KEY, location_id TEXT,
      patient_id TEXT, payload_encrypted TEXT, created REAL);
    """)


def _otp_scope(self, contact, location_id, conversation_id):
    return self.privacy.fingerprint(self.normalize_phone(contact) + "|" + location_id + "|" + conversation_id)


def _request_otp(self, contact: str, location_id: str, conversation_id: str) -> dict:
    response = {
        "message": "If the details match, a verification code will be available through the configured delivery channel."
    }
    with self._lock, self._conn:
        self._conn.execute("BEGIN IMMEDIATE")
        scope = _otp_scope(self, contact, location_id, conversation_id)
        now = self.clock()
        # Contact-wide request cap cannot be bypassed by rotating conversation IDs.
        rate_scope = "otp:" + self.privacy.fingerprint(self.normalize_phone(contact))
        rate = self._conn.execute(
            "SELECT attempts,locked_until FROM identity_attempts WHERE scope=?", (rate_scope,)
        ).fetchone()
        if rate and rate[1] > now:
            self._audit("otp_request_locked", None, [])
            return response
        prior = self._conn.execute(
            "SELECT requested,locked_until FROM otp_challenges WHERE scope=?", (scope,)
        ).fetchone()
        if prior and (now - prior[0] < 60 or prior[1] > now):
            return response
        count = (rate[0] if rate and rate[1] == 0 else 0) + 1
        self._conn.execute(
            "INSERT OR REPLACE INTO identity_attempts VALUES(?,?,?)",
            (rate_scope, count, now + self.lock_seconds if count >= self.max_attempts else 0),
        )
        row = self._conn.execute(
            "SELECT patient_id FROM identities WHERE contact_hash=? AND location_id=?",
            (self.privacy.fingerprint(self.normalize_phone(contact)), location_id),
        ).fetchone()
        code = f"{secrets.randbelow(1000000):06d}"
        self._conn.execute(
            "INSERT OR REPLACE INTO otp_challenges VALUES(?,?,?,?,?,?,?,?,?)",
            (
                scope,
                row[0] if row else None,
                location_id,
                self.privacy.fingerprint(scope + code),
                now + 300,
                0,
                0,
                now,
                0,
            ),
        )
        if row:
            self._conn.execute(
                "INSERT INTO otp_outbox(location_id,patient_id,payload_encrypted,created) VALUES(?,?,?,?)",
                (location_id, row[0], self.privacy.encrypt({"code": code, "scope": scope}), now),
            )
        self._audit("otp_requested", None, [])
    return response


def _verify_otp(self, contact: str, code: str, location_id: str, conversation_id: str) -> str | None:
    with self._lock, self._conn:
        self._conn.execute("BEGIN IMMEDIATE")
        scope = _otp_scope(self, contact, location_id, conversation_id)
        row = self._conn.execute(
            "SELECT patient_id,code_hash,expires,attempts,consumed,locked_until FROM otp_challenges WHERE scope=?",
            (scope,),
        ).fetchone()
        now = self.clock()
        if not row or row[2] <= now or row[4] or row[5] > now:
            return None
        if not hmac.compare_digest(row[1], self.privacy.fingerprint(scope + code)) or not row[0]:
            count = row[3] + 1
            locked = now + self.lock_seconds if count >= self.max_attempts else 0
            self._conn.execute(
                "UPDATE otp_challenges SET attempts=?,locked_until=? WHERE scope=?", (count, locked, scope)
            )
            self._audit("staff_alert_lockout" if locked else "otp_failed", None, [])
            return None
        # Recheck identity still exists; deletion must revoke pending codes too.
        if not self._conn.execute(
            "SELECT 1 FROM identities WHERE patient_id=? AND location_id=?", (row[0], location_id)
        ).fetchone():
            return None
        self._conn.execute("UPDATE otp_challenges SET consumed=1 WHERE scope=?", (scope,))
        token = secrets.token_urlsafe(32)
        self._conn.execute(
            "INSERT INTO identity_tokens VALUES(?,?,?,?)",
            (self.privacy.fingerprint(token), row[0], location_id, now + self.token_seconds),
        )
        self._audit("otp_verified", row[0], [])
        return token


IdentityStore.request_otp = _request_otp
IdentityStore.verify_otp = _verify_otp
