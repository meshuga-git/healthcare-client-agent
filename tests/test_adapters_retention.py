from datetime import timedelta
from cryptography.fernet import Fernet
import pytest
from harbor.adapters import InternalAdapter, MockPMSAdapter, CalendarPMS
from harbor.retention import Retention
from harbor.policy import Privacy, IdentityStore, PolicyError
from harbor.engine import SchedulingError
from test_engine import setup, book, START, NOW


def test_adapter_protocol_lookup_and_mock_failure():
    db, e, p, c = setup()
    db.put("patients", "p", dict(p, location_id="site", name="must never appear"))
    adapter = InternalAdapter(e)
    assert isinstance(adapter, CalendarPMS)
    assert adapter.lookup_patient("p", "p", "site") == {"patient_ref": "p", "location_id": "site"}
    for args in [("p", "other", "site"), ("p", "p", "other"), ("missing", "missing", "site")]:
        with pytest.raises(SchedulingError):
            adapter.lookup_patient(*args)
    slots = adapter.list_availability("exam", "site", p, START, START + timedelta(hours=3))
    a = adapter.create(e.hold_slot(slots[0]["id"], "p", "c")["id"], p)
    assert adapter.cancel(a["id"], "p")["status"] == "cancelled"
    mock = MockPMSAdapter(e, unavailable=True)
    with pytest.raises(SchedulingError):
        mock.lookup_patient("p", "p", "site")
    with pytest.raises(SchedulingError):
        mock.create("missing", p)


def make_retention():
    db, e, p, c = setup()
    identity = IdentityStore(":memory:", Privacy(Fernet.generate_key()), clock=lambda: c[0].timestamp())
    db.put("patients", "p", dict(p, location_id="site"))
    identity.register("p", "site", "1990-01-01", "2025550100", name="Synthetic")
    token = identity.verify("2025550100", "1990-01-01", "site", "conversation")
    return db, e, p, c, identity, token, Retention(db, e, identity, clock=lambda: c[0])


def test_deletion_refuses_active_and_wrong_scope_then_revokes():
    db, e, p, c, identity, token, r = make_retention()
    a = book(e, p)
    with pytest.raises(PolicyError):
        r.delete_patient("p", "site", "front_desk", ["site"])
    with pytest.raises(PolicyError):
        r.delete_patient("p", "site", "admin", ["other"])
    with pytest.raises(SchedulingError):
        r.delete_patient("p", "site", "admin", ["site"])
    assert identity.require(token, "site") == "p"
    e.cancel(a["id"], "p")
    for collection in ("intakes", "intake_links", "jobs", "outbox", "consents"):
        db.put(collection, "x", dict(id="x", patient_id="p", location_id="site", answers_encrypted="ciphertext"))
        db.put(collection, "other", dict(id="other", patient_id="other", location_id="site"))
    counts = r.delete_patient("p", "site", "admin", ["site"])
    assert counts["patients"] == 1 and counts["appointments"] == 1 and counts["intakes"] == 1
    with pytest.raises(PolicyError):
        identity.require(token, "site")
    assert db.get("patients", "p") is None
    assert db.get("intakes", "other") is not None
    assert db.list("audit")[-1]["action"] == "patient_deleted"
    assert db.execute("SELECT count(*) FROM allocations").fetchone()[0] == 0


def test_retention_only_scoped_expired_conversations():
    db, e, p, c, identity, token, r = make_retention()
    for key, age, location in [("old", 31, "site"), ("new", 1, "site"), ("other", 40, "other")]:
        db.put(
            "conversations",
            key,
            dict(
                id=key,
                location_id=location,
                created_at=(NOW - timedelta(days=age)).isoformat(),
                redacted_body="[REDACTED]",
            ),
        )
        db.put(
            "messages",
            key,
            dict(id=key, conversation_id=key, location_id=location, created_at=(NOW - timedelta(days=age)).isoformat()),
        )
    assert r.purge_conversations(30, "site", "admin", ["site"]) == {"conversations": 1, "messages": 1}
    assert {x["id"] for x in db.list("conversations")} == {"new", "other"}
    with pytest.raises(SchedulingError):
        r.purge_conversations(0, "site", "admin", ["site"])
