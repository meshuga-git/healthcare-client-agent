"""Persistent consent, minimal mock outbox, reminders and single-use intake links."""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import hashlib
import secrets
import uuid

UTC = timezone.utc


def uid():
    return uuid.uuid4().hex


def stamp(clock):
    return clock().isoformat()


class Workflows:
    def __init__(self, db, privacy, clock=lambda: datetime.now(UTC)):
        self.db, self.privacy, self.clock = db, privacy, clock

    def audit(self, actor, action, location_id, subject="", fields=()):
        record = dict(
            id=uid(),
            actor=actor,
            action=action,
            location_id=location_id,
            subject_ref=subject,
            fields_accessed=list(fields),
            trace_id=uid(),
            ts=stamp(self.clock),
        )
        self.db.put("audit", record["id"], record)

    def escalate(self, location_id, reason, severity="normal", conversation_id=""):
        record = dict(
            id=uid(),
            location_id=location_id,
            reason=reason,
            severity=severity,
            conversation_id=conversation_id,
            status="open",
            created_at=stamp(self.clock),
        )
        self.db.put("escalations", record["id"], record)
        return record

    def consent(self, patient_id, location_id, channel, granted, source="verified_patient"):
        if channel not in ("sms", "email", "chat"):
            raise ValueError("Unsupported channel")
        with self.db.transaction():
            key = f"{patient_id}:{location_id}:{channel}"
            self.db.put(
                "consents",
                key,
                dict(
                    id=key,
                    patient_id=patient_id,
                    location_id=location_id,
                    channel=channel,
                    granted=granted,
                    source=source,
                    ts=stamp(self.clock),
                ),
            )
            if not granted:
                for job in self.db.list("jobs"):
                    if (
                        job["patient_id"] == patient_id
                        and job["channel"] == channel
                        and job["location_id"] == location_id
                        and job["status"] == "pending"
                    ):
                        job["status"] = "suppressed"
                        self.db.put("jobs", job["id"], job)
            self.audit(patient_id, "consent_changed", location_id, fields=["channel", "granted"])

    def allowed(self, patient_id, location_id, channel, timezone_name="America/New_York"):
        c = self.db.get("consents", f"{patient_id}:{location_id}:{channel}")
        local = self.clock().astimezone(ZoneInfo(timezone_name))
        return bool(c and c["granted"]) and (channel == "chat" or 8 <= local.hour < 20)

    def schedule(self, appointment, channel="sms"):
        for hours in (72, 24, 2):
            due = datetime.fromisoformat(appointment["start"]) - timedelta(hours=hours)
            if due <= self.clock():
                continue
            key = f"{appointment['id']}:{hours}:{channel}"
            self.db.put(
                "jobs",
                key,
                dict(
                    id=key,
                    appointment_id=appointment["id"],
                    patient_id=appointment["patient_id"],
                    location_id=appointment["location_id"],
                    channel=channel,
                    step=hours,
                    due=due.isoformat(),
                    status="pending",
                ),
            )

    def tick(self, packs, location_id=None):
        sent = 0
        with self.db.transaction():
            for job in self.db.list("jobs"):
                if location_id is not None and job["location_id"] != location_id:
                    continue
                if job["status"] != "pending" or datetime.fromisoformat(job["due"]) > self.clock():
                    continue
                ap = self.db.get("appointments", job["appointment_id"])
                if not ap or ap["status"] not in ("booked", "confirmed"):
                    job["status"] = "suppressed"
                    self.db.put("jobs", job["id"], job)
                    continue
                pack = packs[ap["location_id"]]
                if job["step"] == 24 and ap["status"] != "confirmed" and not job.get("call_listed"):
                    self.escalate(
                        ap["location_id"], "Unconfirmed appointment: staff follow-up", conversation_id=ap["id"]
                    )
                    job["call_listed"] = True
                    self.db.put("jobs", job["id"], job)
                if not self.allowed(
                    job["patient_id"],
                    job["location_id"],
                    job["channel"],
                    pack["location"].get("timezone", "America/New_York"),
                ):
                    continue
                # Never include clinical content, service names, names, contacts or intake.
                body = f"Harbor Health Collective appointment reminder: {ap['start']} at {ap['location_id']}. Reply C, R, or X."
                self.db.put(
                    "outbox",
                    job["id"],
                    dict(
                        id=job["id"],
                        location_id=ap["location_id"],
                        patient_id=ap["patient_id"],
                        channel=job["channel"],
                        body=body,
                        mocked=True,
                        sent_at=stamp(self.clock),
                    ),
                )
                job["status"] = "sent"
                self.db.put("jobs", job["id"], job)
                sent += 1
        return sent

    def intake_link(self, appointment, fields):
        token = secrets.token_urlsafe(32)
        key = hashlib.sha256(token.encode()).hexdigest()
        self.db.put(
            "intake_links",
            key,
            dict(
                id=key,
                appointment_id=appointment["id"],
                patient_id=appointment["patient_id"],
                location_id=appointment["location_id"],
                fields=fields,
                expires_at=(self.clock() + timedelta(hours=24)).isoformat(),
                used=False,
            ),
        )
        return token

    def submit_intake(self, token, patient_id, answers):
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.db.transaction():
            link = self.db.get("intake_links", key)
            if (
                not link
                or link["used"]
                or link["patient_id"] != patient_id
                or datetime.fromisoformat(link["expires_at"]) <= self.clock()
            ):
                raise ValueError("Invalid intake link")
            allowed = set(link["fields"])
            if not set(answers) <= allowed:
                raise ValueError("Unexpected intake fields")
            import json

            missing = sorted(k for k in allowed if not answers.get(k))
            record = dict(
                id=uid(),
                appointment_id=link["appointment_id"],
                patient_id=patient_id,
                location_id=link["location_id"],
                answers_encrypted=self.privacy.encrypt(json.dumps(answers)),
                missing=missing,
                complete=not missing,
            )
            self.db.put("intakes", record["id"], record)
            link["used"] = True
            self.db.put("intake_links", key, link)
            self.audit(patient_id, "intake_submitted", link["location_id"], record["id"], ["answers"])
            return dict(id=record["id"], complete=record["complete"], missing=missing)
