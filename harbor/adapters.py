"""PMS boundary: internal and explicitly mocked implementations only.

Patient arguments must come from the verified server-side identity context. Neither
adapter accepts names, phone numbers, or DOB as a lookup mechanism.
"""

from typing import Protocol, runtime_checkable
from .engine import SchedulingError


@runtime_checkable
class CalendarPMS(Protocol):
    def list_availability(self, service_id, location_id, patient, start, end, limit=20): ...
    def create(self, hold_id, patient): ...
    def update(self, appointment_id, hold_id, patient, staff_approved=False): ...
    def cancel(self, appointment_id, patient_id, staff_approved=False): ...
    def lookup_patient(self, patient_id, verified_patient_id, location_id): ...


class InternalAdapter:
    def __init__(self, engine):
        self.engine = engine

    def list_availability(self, service_id, location_id, patient, start, end, limit=20):
        return self.engine.search_availability(service_id, location_id, patient, start, end, limit)

    def create(self, hold_id, patient):
        return self.engine.book(hold_id, patient)

    def update(self, appointment_id, hold_id, patient, staff_approved=False):
        return self.engine.reschedule(appointment_id, hold_id, patient, staff_approved)

    def cancel(self, appointment_id, patient_id, staff_approved=False):
        return self.engine.cancel(appointment_id, patient_id, staff_approved)

    def lookup_patient(self, patient_id, verified_patient_id, location_id):
        if patient_id != verified_patient_id:
            raise SchedulingError("Patient record unavailable")
        row = self.engine.db.get("patients", patient_id)
        if not row or row.get("location_id") != location_id:
            raise SchedulingError("Patient record unavailable")
        # No existence lookup by contact and no personal/intake fields returned.
        return {"patient_ref": patient_id, "location_id": location_id}


class MockPMSAdapter(InternalAdapter):
    """Same deterministic contract with injected upstream outages; no network IO."""

    def __init__(self, engine, unavailable=False):
        super().__init__(engine)
        self.unavailable = unavailable

    def _check(self):
        if self.unavailable:
            raise SchedulingError("Mock PMS unavailable; staff handoff required")

    def list_availability(self, *args, **kwargs):
        self._check()
        return super().list_availability(*args, **kwargs)

    def create(self, *args, **kwargs):
        self._check()
        return super().create(*args, **kwargs)

    def update(self, *args, **kwargs):
        self._check()
        return super().update(*args, **kwargs)

    def cancel(self, *args, **kwargs):
        self._check()
        return super().cancel(*args, **kwargs)

    def lookup_patient(self, *args, **kwargs):
        self._check()
        return super().lookup_patient(*args, **kwargs)
