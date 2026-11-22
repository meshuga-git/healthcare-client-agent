"""Deterministic scheduling, persisted short-lived offers and atomic mutations.
Authorization belongs at the gateway; owner checks also apply to appointment mutations.
No model output is accepted as a slot or resource allocation.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from hashlib import sha256
from uuid import uuid4

UTC = timezone.utc


def dt(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(UTC)


def uid():
    return str(uuid4())


class SchedulingError(ValueError):
    pass


class Engine:
    def __init__(self, db, packs, clock=None):
        self.db, self.packs = db, packs
        self.clock = clock or (lambda: datetime.now(UTC))

    def now(self):
        return dt(self.clock())

    def pack(self, location_id):
        for pack in self.packs.values():
            if pack["location"]["id"] == location_id:
                return pack
        raise SchedulingError("Unknown location")

    def service(self, service_id, location_id):
        pack = self.pack(location_id)
        for service in pack["services"]:
            if service["id"] == service_id:
                return pack, service
        raise SchedulingError("Service unavailable at location")

    def eligible(self, patient, service):
        if not set(service.get("prerequisites", [])) <= set(patient.get("prerequisites") or []):
            raise SchedulingError("Staff review required: prerequisites not satisfied")
        if patient.get("minor") and not (patient.get("guardian_contact") and patient.get("guardian_consent")):
            raise SchedulingError("Guardian contact and consent required")

    def _resource_sets(self, pack, service):
        providers = [p["id"] for p in pack["providers"] if service["skill"] in p["skills"]]
        groups = [[p] for p in providers]
        for kind in service.get("resources", []):
            options = [r["id"] for r in pack["resources"] if r["type"] == kind]
            groups = [g + [r] for g in groups for r in options]
        return [g for g in groups if len(g) == len(set(g))]

    def _valid(self, pack, service, starts, ends):
        now = self.now()
        rules = pack.get("scheduling", {})
        if starts < now + timedelta(minutes=rules.get("min_lead_minutes", 60)):
            return False
        if starts > now + timedelta(days=rules.get("max_horizon_days", 60)):
            return False
        local = starts.astimezone(ZoneInfo(pack["location"].get("timezone", "UTC")))
        local_end = ends.astimezone(local.tzinfo)
        if local.date().isoformat() in pack["location"].get("holidays", []):
            return False
        hours = pack["location"]["hours"]
        day = str(local.weekday())
        window = hours.get(day, hours.get(["mon", "tue", "wed", "thu", "fri", "sat", "sun"][local.weekday()]))
        if not window or local.date() != local_end.date():
            return False
        if not (window[0] <= local.strftime("%H:%M") and local_end.strftime("%H:%M") <= window[1]):
            return False
        if not service.get("urgent", False):
            occupied = local
            while occupied < local_end:
                if occupied.hour in rules.get("urgent_reserved_hours", []):
                    return False
                occupied = occupied.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        for closure in pack["location"].get("closures", []):
            if starts < dt(closure["end"]) and ends > dt(closure["start"]):
                return False
        return True

    def _resource_hours(self, pack, resources, starts, ends):
        local = starts.astimezone(ZoneInfo(pack["location"].get("timezone", "UTC")))
        finish = ends.astimezone(local.tzinfo)
        for resource in pack["providers"] + pack["resources"]:
            if resource["id"] not in resources:
                continue
            if resource.get("location_id", pack["location"]["id"]) != pack["location"]["id"]:
                return False
            hours = resource.get("hours", pack["location"]["hours"])
            window = hours.get(str(local.weekday()))
            if not window or not (window[0] <= local.strftime("%H:%M") and finish.strftime("%H:%M") <= window[1]):
                return False
            if local.date().isoformat() in resource.get("holidays", []):
                return False
            for closure in resource.get("closures", []):
                if starts < dt(closure["end"]) and ends > dt(closure["start"]):
                    return False
        return True

    def _session_id(self, service_id, resources, starts):
        return (
            "session:"
            + sha256(
                (service_id + "|" + ",".join(sorted(resources)) + "|" + dt(starts).isoformat()).encode()
            ).hexdigest()
        )

    def _capacity(self, pack, service, resources):
        caps = [service.get("capacity", 1)]
        caps.extend(r.get("capacity", 1) for r in pack["resources"] if r["id"] in resources)
        return min(caps)

    def _available(self, pack, service, resources, starts, ends):
        session = self.db.get("sessions", self._session_id(service["id"], resources, starts))
        if session and session["owners"]:
            return len(session["owners"]) < self._capacity(pack, service, resources)
        return self.db.available(resources, dt(starts).timestamp(), dt(ends).timestamp())

    def _allocate(self, row):
        pack, service = self.service(row["service_id"], row["location_id"])
        capacity = self._capacity(pack, service, row["resources"])
        owner = row["id"]
        self.db.allocate(
            "patient:" + row["patient_id"], dt(row["start"]).timestamp(), dt(row["blocked_end"]).timestamp(), owner
        )
        if capacity > 1:
            sid = self._session_id(service["id"], row["resources"], row["start"])
            session = self.db.get("sessions", sid) or {"id": sid, "owners": []}
            if len(session["owners"]) >= capacity:
                raise SchedulingError("Class capacity reached")
            if not session["owners"]:
                for resource in row["resources"]:
                    self.db.allocate(resource, dt(row["start"]).timestamp(), dt(row["blocked_end"]).timestamp(), sid)
            session["owners"].append(owner)
            self.db.put("sessions", sid, session)
        else:
            for resource in row["resources"]:
                self.db.allocate(resource, dt(row["start"]).timestamp(), dt(row["blocked_end"]).timestamp(), owner)

    def _release(self, owner):
        for session in self.db.list("sessions"):
            if owner in session["owners"]:
                session["owners"].remove(owner)
                if not session["owners"]:
                    self.db.release(session["id"])
                self.db.put("sessions", session["id"], session)
                self.db.release(owner)
                return
        self.db.release(owner)

    def expire_holds(self):
        with self.db.transaction():
            for hold in self.db.list("holds"):
                if hold["status"] == "held" and dt(hold["expires_at"]) <= self.now():
                    hold["status"] = "expired"
                    self._release(hold["id"])
                    self.db.put("holds", hold["id"], hold)
                    for entry in self.db.list("waitlist"):
                        if entry.get("hold_id") == hold["id"] and entry["status"] == "offered":
                            entry["status"] = "expired"
                            self.db.put("waitlist", entry["id"], entry)

    def search_availability(self, service_id, location_id, patient, start, end, limit=20):
        pack, service = self.service(service_id, location_id)
        self.eligible(patient, service)
        start, end = dt(start), dt(end)
        if end <= start or end - start > timedelta(days=90):
            raise SchedulingError("Search window must be between zero and 90 days")
        limit = max(1, min(limit, 100))
        slots = []
        with self.db.transaction():
            self.expire_holds()
            step = pack.get("scheduling", {}).get("slot_step_minutes", 15)
            # Align to the configured grid, never offer arbitrary minute/second offsets.
            cursor = start.replace(second=0, microsecond=0)
            if cursor < start:
                cursor += timedelta(minutes=1)
            if cursor.minute % step:
                cursor += timedelta(minutes=step - cursor.minute % step)
            while cursor < end and len(slots) < limit:
                finish = cursor + timedelta(minutes=service["duration_minutes"])
                blocked_end = finish + timedelta(minutes=service.get("buffer_minutes", 0))
                if finish <= end and self._valid(pack, service, cursor, blocked_end):
                    for resources in self._resource_sets(pack, service):
                        if (
                            self._resource_hours(pack, resources, cursor, blocked_end)
                            and self._available(pack, service, resources, cursor, blocked_end)
                            and self.db.available(
                                ["patient:" + patient["id"]], cursor.timestamp(), blocked_end.timestamp()
                            )
                        ):
                            slot = {
                                "id": uid(),
                                "service_id": service_id,
                                "location_id": location_id,
                                "start": cursor.isoformat(),
                                "end": finish.isoformat(),
                                "blocked_end": blocked_end.isoformat(),
                                "resources": resources,
                                "patient_id": patient["id"],
                                "offered_at": self.now().isoformat(),
                            }
                            self.db.put("slots", slot["id"], slot)
                            slots.append(slot)
                            if len(slots) >= limit:
                                break
                cursor += timedelta(minutes=step)
        return slots

    def hold_slot(self, slot_id, patient_id, conversation_id):
        with self.db.transaction():
            self.expire_holds()
            slot = self.db.get("slots", slot_id)
            if not slot or slot["patient_id"] != patient_id:
                raise SchedulingError("Invalid slot")
            pack, service = self.service(slot["service_id"], slot["location_id"])
            if (
                slot["resources"] not in self._resource_sets(pack, service)
                or not self._valid(pack, service, dt(slot["start"]), dt(slot["blocked_end"]))
                or not self._resource_hours(pack, slot["resources"], dt(slot["start"]), dt(slot["blocked_end"]))
            ):
                raise SchedulingError("Slot no longer valid")
            hold = {
                **slot,
                "id": uid(),
                "slot_id": slot_id,
                "conversation_id": conversation_id,
                "status": "held",
                "expires_at": (self.now() + timedelta(minutes=10)).isoformat(),
            }
            self._allocate(hold)
            self.db.put("holds", hold["id"], hold)
            return hold

    def book(self, hold_id, patient):
        with self.db.transaction():
            self.expire_holds()
            hold = self.db.get("holds", hold_id)
            if not hold or hold["status"] != "held" or hold["patient_id"] != patient["id"]:
                raise SchedulingError("Hold unavailable")
            pack, service = self.service(hold["service_id"], hold["location_id"])
            self.eligible(patient, service)
            if (
                hold["resources"] not in self._resource_sets(pack, service)
                or not self._resource_hours(pack, hold["resources"], dt(hold["start"]), dt(hold["blocked_end"]))
                or not self._valid(pack, service, dt(hold["start"]), dt(hold["blocked_end"]))
            ):
                raise SchedulingError("Slot no longer valid")
            appointment = {
                k: hold[k]
                for k in ("patient_id", "service_id", "location_id", "start", "end", "blocked_end", "resources")
            }
            appointment.update(id=uid(), status="booked", confirmed=False, created_at=self.now().isoformat())
            self._release(hold_id)
            self._allocate(appointment)
            hold["status"] = "booked"
            self.db.put("holds", hold_id, hold)
            self.db.put("appointments", appointment["id"], appointment)
            for entry in self.db.list("waitlist"):
                if entry.get("hold_id") == hold_id:
                    entry["status"] = "booked"
                    self.db.put("waitlist", entry["id"], entry)
            return appointment

    def get_appointment(self, appointment_id, patient_id):
        row = self.db.get("appointments", appointment_id)
        if not row or row["patient_id"] != patient_id:
            raise SchedulingError("Appointment unavailable")
        return row

    def cancel(self, appointment_id, patient_id, staff_approved=False):
        with self.db.transaction():
            row = self.get_appointment(appointment_id, patient_id)
            if row["status"] == "cancelled":
                return row
            pack = self.pack(row["location_id"])
            if (
                dt(row["start"]) - self.now()
                < timedelta(hours=pack.get("scheduling", {}).get("cancel_window_hours", 24))
                and not staff_approved
            ):
                raise SchedulingError("Late cancellation policy: a fee may apply; staff approval required")
            row["status"] = "cancelled"
            self._release(row["id"])
            self.db.put("appointments", row["id"], row)
            return row

    def reschedule(self, appointment_id, hold_id, patient, staff_approved=False):
        with self.db.transaction():
            self.cancel(appointment_id, patient["id"], staff_approved)
            return self.book(hold_id, patient)

    def join_waitlist(self, patient, service_id, location_id, start, end, priority=0, channel="sms", consent=True):
        if not consent:
            raise SchedulingError("Channel consent required")
        _, service = self.service(service_id, location_id)
        self.eligible(patient, service)
        start, end = dt(start), dt(end)
        if end <= start:
            raise SchedulingError("Invalid window")
        row = {
            "id": uid(),
            "patient_id": patient["id"],
            "eligibility": {
                "prerequisites": patient.get("prerequisites", []),
                "minor": bool(patient.get("minor")),
                "guardian_contact": bool(patient.get("guardian_contact")),
                "guardian_consent": bool(patient.get("guardian_consent")),
            },
            "service_id": service_id,
            "location_id": location_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "priority": int(priority),
            "channel": channel,
            "status": "waiting",
            "created_at": self.now().isoformat(),
        }
        self.db.put("waitlist", row["id"], row)
        return row

    def process_waitlist(self, consent_check=None, location_id=None):
        offers = []
        with self.db.transaction():
            self.expire_holds()
            entries = sorted(self.db.list("waitlist"), key=lambda e: (-e["priority"], e["created_at"], e["id"]))
            for entry in entries:
                if entry["status"] != "waiting" or (location_id is not None and entry["location_id"] != location_id):
                    continue
                if consent_check is not None and not consent_check(
                    entry["patient_id"], entry["channel"], entry["location_id"]
                ):
                    continue
                patient = {"id": entry["patient_id"], **entry["eligibility"]}
                try:
                    slots = self.search_availability(
                        entry["service_id"], entry["location_id"], patient, entry["start"], entry["end"], limit=1
                    )
                except SchedulingError:
                    continue
                if not slots:
                    continue
                hold = self.hold_slot(slots[0]["id"], entry["patient_id"], "waitlist:" + entry["id"])
                entry.update(status="offered", hold_id=hold["id"], expires_at=hold["expires_at"])
                self.db.put("waitlist", entry["id"], entry)
                offers.append(entry)
        return offers

    def decline_offer(self, waitlist_id, patient_id):
        with self.db.transaction():
            entry = self.db.get("waitlist", waitlist_id)
            if not entry or entry["patient_id"] != patient_id or entry["status"] != "offered":
                raise SchedulingError("Offer unavailable")
            hold = self.db.get("holds", entry["hold_id"])
            hold["status"] = "declined"
            self._release(hold["id"])
            self.db.put("holds", hold["id"], hold)
            entry["status"] = "declined"
            self.db.put("waitlist", entry["id"], entry)
            return entry
