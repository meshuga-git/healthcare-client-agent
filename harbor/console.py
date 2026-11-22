"""Streamlit local staff console and synthetic patient booking panel."""

import os
from datetime import datetime, timedelta, timezone
import httpx
import streamlit as st

st.set_page_config(page_title="Harbor · Scheduling", page_icon="⚓", layout="wide")
st.title("⚓ Harbor Health Collective")
st.caption("Synthetic US demonstration · No medical advice · No external messages · Not a production system")
base = os.getenv("API_URL", "http://127.0.0.1:8000")


def call(method, path, token=None, **kwargs):
    try:
        r = httpx.request(
            method, base + path, headers={"Authorization": "Bearer " + token} if token else {}, timeout=15, **kwargs
        )
        if not r.is_success:
            st.error(r.json().get("detail", "Request failed"))
            return None
        return r.json()
    except httpx.HTTPError:
        st.error("API unavailable. Start the FastAPI server first.")
        return None


catalog = call("GET", "/catalog")
if not catalog:
    st.stop()
loc = st.sidebar.selectbox("Location", [x["location_id"] for x in catalog])
entry = next(x for x in catalog if x["location_id"] == loc)
tabs = st.tabs(["Patient demo", "Staff console", "About & limits"])
with tabs[0]:
    st.subheader("Book using real engine output")
    st.info("Use synthetic records only. For emergency help in the USA, call 911. This demo is not monitored.")
    index = [x["location_id"] for x in catalog].index(loc)
    with st.expander("Synthetic login", expanded=True):
        phone = st.text_input("Synthetic phone", value=f"20255501{index:02d}", key="phone-" + loc)
        dob = st.text_input("Synthetic date of birth", value="1990-01-01")
        if st.button("Verify demo identity"):
            result = call(
                "POST", "/verify", json=dict(contact=phone, dob=dob, location_id=loc, conversation_id="streamlit-demo")
            )
            if result and result.get("token"):
                st.session_state["patient:" + loc] = result["token"]
                st.success("Verified for this location")
    token = st.session_state.get("patient:" + loc)
    service = st.selectbox("Service", entry["services"], format_func=lambda s: s["name"])
    st.caption("Required eligibility flags: " + (", ".join(service["prerequisites"]) or "None"))
    date = st.date_input("Search from", value=(datetime.now() + timedelta(days=2)).date())
    if st.button("Find available times", disabled=not token):
        start = datetime.combine(date, datetime.min.time(), tzinfo=timezone.utc)
        result = call(
            "GET",
            "/availability",
            token,
            params=dict(
                location_id=loc,
                service_id=service["id"],
                start=start.isoformat(),
                end=(start + timedelta(days=7)).isoformat(),
            ),
        )
        st.session_state["slots:" + loc] = result or []
    slots = st.session_state.get("slots:" + loc, [])
    if slots:
        slot = st.selectbox("Returned slots (UTC)", slots, format_func=lambda s: s["start"])
        if st.button("Hold this slot for 10 minutes"):
            result = call(
                "POST",
                "/holds",
                token,
                json=dict(location_id=loc, slot_id=slot["id"], conversation_id="streamlit-demo"),
            )
            if result:
                st.session_state["hold:" + loc] = result
                st.success("Held. Confirm to complete booking.")
    hold = st.session_state.get("hold:" + loc)
    if hold and st.button("Confirm booking"):
        result = call("POST", "/book", token, json=dict(location_id=loc, hold_id=hold["id"]))
        if result:
            st.session_state.pop("hold:" + loc, None)
            st.session_state["appointment:" + loc] = result
            st.success("Booked")
            st.json(result)
    ap = st.session_state.get("appointment:" + loc)
    if ap:
        st.write("Latest appointment", ap["id"])
        if st.button("Cancel latest booking"):
            result = call("POST", f"/appointments/{ap['id']}/cancel", token, params={"location_id": loc})
            if result:
                st.json(result)
        if st.button("Create single-use intake link"):
            result = call("POST", f"/appointments/{ap['id']}/intake-link", token, params={"location_id": loc})
            if result:
                st.session_state["intake:" + loc] = result["token"]
        if st.session_state.get("intake:" + loc):
            contact = st.checkbox("Contact details confirmed")
            privacy = st.checkbox("Demonstration privacy notice acknowledged")
            if st.button("Submit intake"):
                result = call(
                    "POST",
                    "/intake",
                    token,
                    json=dict(
                        location_id=loc,
                        token=st.session_state["intake:" + loc],
                        answers=dict(contact_confirmed=contact, privacy_acknowledged=privacy),
                    ),
                )
                if result:
                    st.json(result)
                    st.session_state.pop("intake:" + loc, None)
    if token:
        consent = st.checkbox("I consent to mocked SMS reminders (nothing is actually sent)")
        if st.button("Save channel consent"):
            call("POST", "/consent", token, json=dict(location_id=loc, channel="sms", granted=consent))
    text = st.text_input("Scheduling chat / safety demonstration")
    if st.button("Send message") and text:
        result = call(
            "POST",
            "/chat",
            token,
            json=dict(
                text=text, location_id=loc, conversation_id="streamlit-demo", appointment_id=ap["id"] if ap else None
            ),
        )
        if result:
            st.write(result.get("message", result))
            st.json(result)
with tabs[1]:
    st.warning("Local demo credentials only. Never deploy these defaults or enter real patient data.")
    staff_token = st.text_input("Staff token", value="local-demo-guest", type="password")
    st.caption(
        "Read-only guest: local-demo-guest · Dental desk: local-demo-dental-desk · Admin default documented in README"
    )
    view = st.selectbox(
        "View", ["summary", "schedule", "escalations", "intake_completeness", "intake", "conversations", "audit"]
    )
    if st.button("Load staff view"):
        rows = call("GET", f"/staff/{loc}/{view}", staff_token)
        if rows is not None:
            st.json(rows)
    if st.button("Run local reminder / waitlist worker"):
        result = call("POST", f"/staff/{loc}/tick", staff_token)
        if result:
            st.json(result)
with tabs[2]:
    st.markdown("""### Code schedules; no model invents slots
Deterministic availability, transactional resource allocations, expiring holds, prerequisites, cancellation review and waitlist ordering. Personal identity and intake fields are encrypted. Local synthetic transport only.

### Honest scope
This is a portfolio prototype, not HIPAA-compliant software or emergency care. Natural-language generative orchestration, real PMS, external messaging, clinical protocols, deployment agreements and jurisdiction-specific legal review are not delivered. See `docs/scope-matrix.md` and validation reports for precise tested limits.""")
