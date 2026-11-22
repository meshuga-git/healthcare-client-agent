from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import pytest
from hypothesis import given, settings, strategies as st
from harbor.db import Database
from harbor.engine import Engine, SchedulingError

UTC = timezone.utc
NOW = datetime(2026, 10, 23, 8, tzinfo=UTC)
START = NOW + timedelta(days=3, hours=1)


def pack():
    return {
        "location": {
            "id": "site",
            "timezone": "UTC",
            "hours": {str(i): ["09:00", "17:00"] for i in range(5)},
            "holidays": [],
        },
        "scheduling": {
            "min_lead_minutes": 60,
            "max_horizon_days": 60,
            "slot_step_minutes": 15,
            "cancel_window_hours": 24,
        },
        "providers": [{"id": "provider", "skills": ["exam"]}],
        "resources": [{"id": "room", "type": "room"}],
        "services": [
            {
                "id": "exam",
                "duration_minutes": 30,
                "buffer_minutes": 15,
                "skill": "exam",
                "resources": ["room"],
                "prerequisites": ["consent"],
            }
        ],
    }


def setup():
    db = Database()
    clock = [NOW]
    engine = Engine(db, {"test": pack()}, lambda: clock[0])
    patient = {"id": "p", "prerequisites": ["consent"]}
    return db, engine, patient, clock


def slot(engine, patient, start=START):
    return engine.search_availability("exam", "site", patient, start, start + timedelta(hours=3), 1)[0]


def book(engine, patient, start=START):
    return engine.book(engine.hold_slot(slot(engine, patient, start)["id"], patient["id"], "c")["id"], patient)


def test_book_overlap_buffer_and_owner():
    db, e, p, c = setup()
    old = slot(e, p)
    appointment = book(e, p)
    with pytest.raises(ValueError):
        e.hold_slot(old["id"], "p", "c")
    assert (
        e.search_availability("exam", "site", p, START + timedelta(minutes=30), START + timedelta(hours=2), 1)[0][
            "start"
        ]
        == (START + timedelta(minutes=45)).isoformat()
    )
    with pytest.raises(SchedulingError):
        e.get_appointment(appointment["id"], "other")


def test_expiry_and_revalidation():
    db, e, p, c = setup()
    s = slot(e, p)
    h = e.hold_slot(s["id"], "p", "c")
    c[0] += timedelta(minutes=10)
    with pytest.raises(SchedulingError):
        e.book(h["id"], p)
    assert e.hold_slot(s["id"], "p", "c")["status"] == "held"


def test_prerequisites_guardian_hours_holiday_and_reserved():
    db, e, p, c = setup()
    for bad in [{"id": "x"}, {**p, "minor": True}, {**p, "minor": True, "guardian_contact": True}]:
        with pytest.raises(SchedulingError):
            slot(e, bad)
    assert slot(e, {**p, "minor": True, "guardian_contact": True, "guardian_consent": True})
    e.packs["test"]["location"]["holidays"] = ["2026-10-26"]
    assert e.search_availability("exam", "site", p, START, START + timedelta(hours=4)) == []
    e.packs["test"]["location"]["holidays"] = []
    e.packs["test"]["scheduling"]["urgent_reserved_hours"] = [9]
    assert slot(e, p)["start"].endswith("10:00:00+00:00")
    e.packs["test"]["providers"][0]["hours"] = {"0": ["13:00", "17:00"]}
    assert e.search_availability("exam", "site", p, START, START + timedelta(hours=3)) == []


def test_atomic_reschedule_rollback_and_late_cancel():
    db, e, p, c = setup()
    a = book(e, p)
    with pytest.raises(SchedulingError):
        e.reschedule(a["id"], "missing", p)
    assert e.get_appointment(a["id"], "p")["status"] == "booked"
    c[0] = START - timedelta(hours=1)
    with pytest.raises(SchedulingError):
        e.cancel(a["id"], "p")
    assert e.cancel(a["id"], "p", staff_approved=True)["status"] == "cancelled"


def test_waitlist_rank_expiry_next_and_consent():
    db, e, p, c = setup()
    a = book(e, p)
    for i, priority in [("low", 0), ("high", 10)]:
        e.join_waitlist({**p, "id": i}, "exam", "site", START, START + timedelta(minutes=30), priority)
    assert not e.process_waitlist()
    e.cancel(a["id"], "p")
    assert not e.process_waitlist(lambda *_: False)
    first = e.process_waitlist(lambda *_: True)
    assert [x["patient_id"] for x in first] == ["high"]
    c[0] += timedelta(minutes=10)
    second = e.process_waitlist(lambda *_: True)
    assert [x["patient_id"] for x in second] == ["low"]
    assert e.book(second[0]["hold_id"], {**p, "id": "low"})["status"] == "booked"


def test_concurrent_hold_single_winner():
    db, e, p, c = setup()
    s = slot(e, p)

    def race(_):
        try:
            return e.hold_slot(s["id"], "p", "c")["id"]
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(race, range(100)))
    assert sum(r is not None for r in results) == 1


@settings(max_examples=10000, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=16), min_size=1, max_size=10))
def test_10000_random_booking_sequences_no_overlap(offsets):
    db, e, p, c = setup()
    for offset in offsets:
        slots = e.search_availability(
            "exam", "site", p, START + timedelta(minutes=15 * offset), START + timedelta(minutes=15 * offset + 30), 1
        )
        if slots:
            e.book(e.hold_slot(slots[0]["id"], "p", "c")["id"], p)
    rows = db.list("appointments")
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            assert a["blocked_end"] <= b["start"] or b["blocked_end"] <= a["start"]
    db.close()


def test_class_capacity_shared_resources_and_release():
    db, e, p, c = setup()
    e.packs["test"]["services"][0]["capacity"] = 3
    e.packs["test"]["resources"][0]["capacity"] = 3
    appointments = []
    for i in range(3):
        appointments.append(book(e, {**p, "id": str(i)}))
    assert len({a["start"] for a in appointments}) == 1
    assert e.search_availability("exam", "site", p, START, START + timedelta(minutes=30)) == []
    e.cancel(appointments[0]["id"], "0")
    assert len(e.search_availability("exam", "site", p, START, START + timedelta(minutes=30))) == 1
    # Existing class still blocks a different overlapping service.
    e.packs["test"]["services"].append({**e.packs["test"]["services"][0], "id": "individual", "capacity": 1})
    assert e.search_availability("individual", "site", p, START, START + timedelta(minutes=30)) == []


def test_each_vertical_books_valid_appointment():
    from harbor.config import load_packs

    packs = load_packs()
    e = Engine(Database(), packs, lambda: NOW)
    for vertical, config in packs.items():
        service = config["services"][0]
        p = {"id": vertical, "prerequisites": service["prerequisites"]}
        slots = e.search_availability(
            service["id"], config["location"]["id"], p, NOW + timedelta(days=3), NOW + timedelta(days=4), 1
        )
        assert slots, vertical
        appointment = e.book(e.hold_slot(slots[0]["id"], p["id"], "test")["id"], p)
        assert appointment["location_id"] == config["location"]["id"]


def test_cross_connection_sqlite_race(tmp_path):
    filename = tmp_path / "shared.sqlite"
    db = Database(filename)
    e = Engine(db, {"test": pack()}, lambda: NOW)
    p = {"id": "p", "prerequisites": ["consent"]}
    s = slot(e, p)

    def contender(i):
        conn = Database(filename)
        other = Engine(conn, {"test": pack()}, lambda: NOW)
        try:
            other.hold_slot(s["id"], "p", str(i))
            return True
        except ValueError:
            return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(contender, range(20)))
    assert sum(results) == 1


def test_postgresql_exclusion_rejects_direct_overlap():
    import os

    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("TEST_POSTGRES_URL not set; PostgreSQL exclusion unverified locally")
    db = Database(url)
    from uuid import uuid4

    r = str(uuid4())
    with db.transaction():
        db.allocate(r, 1, 3, "first" + r)
    with pytest.raises(Exception):
        with db.transaction():
            db.execute(
                "INSERT INTO allocations(id,resource_id,starts,ends,owner) VALUES(?,?,?,?,?)",
                ("second" + r, r, 2, 4, "second" + r),
            )
    with db.transaction():
        db.release("first" + r)
