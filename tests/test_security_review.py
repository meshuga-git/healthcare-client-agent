"""Independent adversarial review: synthetic API security boundaries."""

from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from harbor.api import create_app
from harbor.policy import PolicyError

UTC = timezone.utc


@pytest.fixture
def demo():
    now = [datetime(2026, 10, 26, 14, tzinfo=UTC)]
    app = create_app(db_path=":memory:", clock=lambda: now[0])
    client = TestClient(app)
    yield app, client, now
    app.state.identity.close()
    app.state.db.close()


def auth(client, loc="dental-site", phone="2025550100"):
    r = client.post("/verify", json=dict(contact=phone, dob="1990-01-01", location_id=loc, conversation_id="review"))
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def appointment(app, client, now):
    headers = auth(client)
    pack = app.state.packs["dental-site"]
    service = pack["services"][0]["id"]
    r = client.get(
        "/availability",
        params=dict(
            location_id="dental-site",
            service_id=service,
            start=(now[0] + timedelta(days=2)).isoformat(),
            end=(now[0] + timedelta(days=3)).isoformat(),
        ),
        headers=headers,
    )
    assert r.status_code == 200, r.text
    slot = r.json()[0]
    hold = client.post("/holds", json=dict(location_id="dental-site", slot_id=slot["id"]), headers=headers).json()
    ap = client.post("/book", json=dict(location_id="dental-site", hold_id=hold["id"]), headers=headers)
    assert ap.status_code == 200, ap.text
    return headers, ap.json()


def test_emergency_precedes_bad_auth_location_and_flood(demo):
    app, c, now = demo
    for _ in range(21):
        c.post(
            "/verify",
            json=dict(contact="0000000000", dob="1900-01-01", location_id="dental-site", conversation_id="flood"),
        )
    r = c.post(
        "/chat",
        json=dict(text="I cannot breathe", location_id="invalid-site"),
        headers={"Authorization": "Bearer nonsense"},
    )
    assert r.status_code == 200 and r.json()["static"] and r.json()["intent"] == "emergency"
    assert any(
        e["reason"] == "emergency" and e["severity"] == "critical" and e["location_id"] in app.state.packs
        for e in app.state.db.list("escalations")
    )


def test_failed_identity_no_existence_disclosure(demo):
    app, c, _ = demo

    def attempt(contact):
        return c.post(
            "/verify", json=dict(contact=contact, dob="1900-01-01", location_id="dental-site", conversation_id="same")
        ).json()

    assert attempt("2025550100") == attempt("0000000000")


def test_patient_scope_and_session_expiry(demo):
    app, c, now = demo
    h = auth(c)
    token = h["Authorization"][7:]
    with pytest.raises(PolicyError):
        app.state.identity.require(token, "lab-site")
    now[0] += timedelta(minutes=16)
    with pytest.raises(PolicyError):
        app.state.identity.require(token, "dental-site")


def test_staff_scope_and_guest_are_enforced(demo):
    _, c, _ = demo
    h = {"Authorization": "Bearer local-demo-dental-desk"}
    assert c.get("/staff/lab-site/schedule", headers=h).status_code == 403
    assert c.get("/staff/dental-site/intake", headers=h).status_code == 403
    assert c.get("/staff/dental-site/audit", headers=h).status_code == 403
    assert c.get("/staff/dental-site/schedule", headers={"Authorization": "Bearer local-demo-guest"}).status_code == 403


def test_cancelled_cannot_be_confirmed(demo):
    app, c, now = demo
    headers, ap = appointment(app, c, now)
    assert (
        c.post(f"/appointments/{ap['id']}/cancel", params={"location_id": "dental-site"}, headers=headers).status_code
        == 200
    )
    r = c.post("/chat", json=dict(text="C", location_id="dental-site", appointment_id=ap["id"]), headers=headers)
    assert r.status_code == 400
    assert app.state.db.get("appointments", ap["id"])["status"] == "cancelled"


def test_intake_encrypted_single_use_and_patient_bound(demo):
    app, c, now = demo
    h, ap = appointment(app, c, now)
    token = app.state.workflows.intake_link(ap, ["concern"])
    with pytest.raises(ValueError):
        app.state.workflows.submit_intake(token, "other", {"concern": "sensitive synthetic answer"})
    app.state.workflows.submit_intake(token, ap["patient_id"], {"concern": "sensitive synthetic answer"})
    assert "sensitive synthetic answer" not in str(app.state.db.list("intakes"))
    with pytest.raises(ValueError):
        app.state.workflows.submit_intake(token, ap["patient_id"], {"concern": "again"})


def test_stop_suppresses_jobs_and_site_timezone(demo):
    app, c, now = demo
    h, ap = appointment(app, c, now)
    w = app.state.workflows
    w.consent(ap["patient_id"], "dental-site", "sms", True)
    for job in app.state.db.list("jobs"):
        job["due"] = now[0].isoformat()
        app.state.db.put("jobs", job["id"], job)
    # 14 UTC is 07 in Los Angeles in October: no SMS send.
    app.state.packs["dental-site"]["location"]["timezone"] = "America/Los_Angeles"
    assert w.tick(app.state.packs) == 0
    r = c.post("/chat", json=dict(text="STOP", location_id="dental-site", channel="sms"), headers=h)
    assert r.status_code == 200
    now[0] += timedelta(hours=2)
    assert w.tick(app.state.packs) == 0
    assert not app.state.db.list("outbox")


def test_otp_api_no_code_disclosure_single_use(demo):
    app, c, now = demo
    body = dict(contact="2025550100", location_id="dental-site", conversation_id="otp-review")
    known = c.post("/otp/request", json=body)
    unknown = c.post("/otp/request", json={**body, "contact": "0000000000"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    row = app.state.identity._conn.execute(
        "SELECT payload_encrypted FROM otp_outbox ORDER BY id DESC LIMIT 1"
    ).fetchone()
    code = app.state.privacy.decrypt(row[0])["code"]
    assert code not in known.text
    assert c.post("/otp/verify", json={**body, "conversation_id": "wrong", "code": code}).json()["verified"] is False
    good = c.post("/otp/verify", json={**body, "code": code}).json()
    assert good["verified"] and good["token"]
    assert c.post("/otp/verify", json={**body, "code": code}).json()["verified"] is False
