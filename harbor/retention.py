"""Administrative synthetic-record deletion and conversation retention.

This is not a legal retention policy. Identity is a separate store: its revocation
happens first so a later database failure remains fail-closed and can be retried.
Audit entries retain pseudonymous subject references; backups are outside this demo.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from .engine import dt, SchedulingError
from .policy import authorize_staff


class Retention:
    PATIENT_COLLECTIONS = (
        "consents",
        "jobs",
        "outbox",
        "intakes",
        "intake_links",
        "waitlist",
        "slots",
        "holds",
        "appointments",
        "messages",
        "conversations",
    )

    def __init__(self, db, engine, identity, clock=None):
        self.db, self.engine, self.identity = db, engine, identity
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _authorize(self, role, allowed_locations, location_id):
        authorize_staff(role, allowed_locations, location_id, "delete")

    def _audit(self, location_id, action, subject="", counts=None):
        key = str(uuid4())
        self.db.put(
            "audit",
            key,
            {
                "id": key,
                "actor": "admin",
                "action": action,
                "location_id": location_id,
                "subject_ref": subject,
                "fields_accessed": [],
                "trace_id": str(uuid4()),
                "ts": self.clock().isoformat(),
                "counts": counts or {},
            },
        )

    def delete_patient(self, patient_id, location_id, role, allowed_locations):
        self._authorize(role, allowed_locations, location_id)
        counts = {}
        with self.db.transaction():
            patient = self.db.get("patients", patient_id)
            if not patient or patient.get("location_id") != location_id:
                raise SchedulingError("Patient record unavailable")
            self.engine.expire_holds()
            appointments = [r for r in self.db.list("appointments") if r.get("patient_id") == patient_id]
            holds = [r for r in self.db.list("holds") if r.get("patient_id") == patient_id]
            if any(r.get("status") not in ("cancelled", "completed", "no_show") for r in appointments) or any(
                r.get("status") == "held" for r in holds
            ):
                raise SchedulingError("Active bookings or holds require staff resolution before deletion")
            # Global patient pseudonyms must never silently cross location scope.
            if any(r.get("location_id") != location_id for r in appointments + holds):
                raise SchedulingError("Cross-location records require separate administrative review")
            self.identity.delete(patient_id)
            for row in appointments + holds:
                self.engine._release(row["id"])
            for collection in self.PATIENT_COLLECTIONS:
                count = 0
                for row in self.db.list(collection):
                    if (row.get("patient_id") == patient_id or row.get("patient_ref") == patient_id) and row.get(
                        "location_id"
                    ) == location_id:
                        self.db.delete(collection, row["id"])
                        count += 1
                counts[collection] = count
            self.db.delete("patients", patient_id)
            counts["patients"] = 1
            self._audit(location_id, "patient_deleted", patient_id, counts)
        return counts

    def purge_conversations(self, retention_days, location_id, role, allowed_locations):
        self._authorize(role, allowed_locations, location_id)
        if not isinstance(retention_days, int) or not 1 <= retention_days <= 3650:
            raise SchedulingError("Retention must be 1 to 3650 days")
        cutoff = dt(self.clock()) - timedelta(days=retention_days)
        counts = {"conversations": 0, "messages": 0}
        with self.db.transaction():
            expired_ids = set()
            for row in self.db.list("conversations"):
                if row.get("location_id") != location_id:
                    continue
                timestamp = row.get("updated_at", row.get("created_at", row.get("ts")))
                if timestamp and dt(timestamp) < cutoff:
                    expired_ids.add(row["id"])
                    self.db.delete("conversations", row["id"])
                    counts["conversations"] += 1
            for row in self.db.list("messages"):
                if row.get("location_id") != location_id:
                    continue
                timestamp = row.get("created_at", row.get("ts"))
                if row.get("conversation_id") in expired_ids or (timestamp and dt(timestamp) < cutoff):
                    self.db.delete("messages", row["id"])
                    counts["messages"] += 1
            self._audit(location_id, "conversation_retention_purge", counts=counts)
        return counts
