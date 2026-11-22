from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from harbor.api import create_app


def fixture():
    now = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    app = create_app(db_path=":memory:", clock=lambda: now)
    client = TestClient(app)
    token = client.post(
        "/verify",
        json=dict(contact="2025550100", dob="1990-01-01", location_id="dental-site", conversation_id="verify"),
    ).json()["token"]
    return app, client, {"Authorization": "Bearer " + token}, now


def test_agent_engine_grounded_booking_confirmation_and_audit():
    app, c, h, now = fixture()
    request = dict(
        text="Book an appointment",
        location_id="dental-site",
        conversation_id="agent-book",
        service_id="dental-exam",
        start=(now + timedelta(days=2)).isoformat(),
        end=(now + timedelta(days=3)).isoformat(),
    )
    r = c.post("/agent", json=request, headers=h)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["status"] == "completed", result
    slot = result["results"][0]["result"][0]
    request.update(action="hold_slot", slot_id=slot["id"])
    result = c.post("/agent", json=request, headers=h).json()
    assert result["status"] == "confirmation_required"
    request.update(user_confirmed=True, conversation_id="hold-new")
    result = c.post("/agent", json=request, headers=h).json()
    assert result["status"] == "completed", result
    hold = result["results"][0]["result"]
    request.update(action="book", hold_id=hold["id"], conversation_id="book-new")
    result = c.post("/agent", json=request, headers=h).json()
    assert result["status"] == "completed", result
    assert result["results"][0]["result"]["status"] == "booked"
    assert app.state.db.list("audit")


def test_agent_safety_precedes_auth_and_no_slots_invented():
    app, c, h, now = fixture()
    r = c.post("/agent", json=dict(text="I cannot breathe", location_id="invalid", conversation_id="emergency"))
    assert r.status_code == 200 and r.json()["static"]
    assert app.state.db.list("escalations")
    r = c.post(
        "/agent",
        json=dict(
            text="Book now",
            location_id="dental-site",
            conversation_id="fake",
            service_id="dental-exam",
            action="book",
            hold_id="invented",
            user_confirmed=True,
        ),
        headers=h,
    )
    assert r.json()["status"] == "handoff"
    assert not app.state.db.list("appointments")


def test_private_otp_delivery_and_single_use_flow():
    app, c, h, now = fixture()
    body = dict(contact="2025550100", location_id="dental-site", conversation_id="otp-ui")
    response = c.post("/otp/request", json=body)
    assert response.status_code == 200 and "code" not in response.json()
    assert (
        c.get("/staff/dental-site/otp-outbox", headers={"Authorization": "Bearer local-demo-guest"}).status_code == 403
    )
    assert (
        c.get("/staff/clinic-site/otp-outbox", headers={"Authorization": "Bearer local-demo-dental-desk"}).status_code
        == 403
    )
    response = c.get(
        "/staff/dental-site/otp-outbox", headers={"Authorization": "Bearer local-demo-admin-not-for-deployment"}
    )
    code = response.json()[0]["payload"]["code"]
    response = c.post("/otp/verify", json=dict(body, code=code))
    assert response.json()["verified"]
    assert not c.post("/otp/verify", json=dict(body, code=code)).json()["verified"]
