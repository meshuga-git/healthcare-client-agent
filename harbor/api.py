"""Local synthetic-only API. Every patient mutation requires a scoped identity token."""

from __future__ import annotations
from datetime import datetime, timezone
import os
from pathlib import Path
from collections import defaultdict
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from cryptography.fernet import Fernet
from .db import Database
from .engine import Engine
from .policy import Privacy, IdentityStore, PolicyError, safety_screen, authorize_staff
from .workflows import Workflows

UTC = timezone.utc


class Verify(BaseModel):
    contact: str = Field(max_length=40)
    dob: str = Field(max_length=20)
    location_id: str
    conversation_id: str = Field(min_length=1, max_length=100)


class OTPRequest(BaseModel):
    contact: str = Field(max_length=40)
    location_id: str
    conversation_id: str = Field(min_length=1, max_length=100)


class OTPVerify(OTPRequest):
    code: str = Field(min_length=1, max_length=20)


class Chat(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    location_id: str
    conversation_id: str = Field(default="demo", max_length=100)
    appointment_id: str | None = None
    channel: str = "chat"


class Hold(BaseModel):
    location_id: str
    slot_id: str
    conversation_id: str = "demo"


class Booking(BaseModel):
    location_id: str
    hold_id: str


class Consent(BaseModel):
    location_id: str
    channel: str
    granted: bool


class Intake(BaseModel):
    location_id: str
    token: str
    answers: dict = Field(default_factory=dict)


class AgentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    location_id: str
    conversation_id: str = Field(min_length=1, max_length=100)
    service_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    slot_id: str | None = None
    hold_id: str | None = None
    appointment_id: str | None = None
    action: str | None = None
    user_confirmed: bool = False


def create_app(db_path=None, identity_path=None, key=None, clock=None, seed=True):
    from .config import load_packs

    clock = clock or (lambda: datetime.now(UTC))
    packs0 = load_packs()
    packs = {p["location"]["id"]: p for p in (packs0.values() if isinstance(packs0, dict) else packs0)}
    db = Database(db_path or os.getenv("DATABASE_URL", "harbor.db"))
    key = key or os.getenv("FIELD_ENCRYPTION_KEY")
    if not key:
        if db_path == ":memory:":
            key = Fernet.generate_key()
        else:
            path = Path(".demo-encryption.key")
            if not path.exists():
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(Fernet.generate_key())
            key = path.read_bytes()
    privacy = Privacy(key)
    identity = IdentityStore(
        identity_path or (":memory:" if db_path == ":memory:" else os.getenv("IDENTITY_DB", "identity.db")),
        privacy,
        clock=lambda: clock().timestamp(),
    )
    engine = Engine(db, packs, clock=clock)
    workflows = Workflows(db, privacy, clock)
    app = FastAPI(title="Harbor Health Collective · Synthetic scheduling demo", version="0.1.0")
    app.state.db, app.state.engine, app.state.identity, app.state.workflows = db, engine, identity, workflows
    app.state.packs, app.state.privacy = packs, privacy
    app.state.clock = clock
    staff_token = os.getenv("STAFF_API_TOKEN", "local-demo-admin-not-for-deployment")
    staff = {
        staff_token: dict(role="admin", locations=list(packs)),
        "local-demo-dental-desk": dict(role="front_desk", locations=["dental-site"]),
        "local-demo-guest": dict(role="guest", locations=list(packs)),
    }
    app.state.staff = staff
    from .llm import ModelClient
    from .agent import AgentOrchestrator

    model_client = ModelClient()
    orchestrator = AgentOrchestrator(model_client)
    app.state.model_client = model_client
    rates = defaultdict(list)
    if seed:
        for i, (loc, pack) in enumerate(packs.items()):
            pid = "synthetic-" + loc
            identity.register(pid, loc, "1990-01-01", f"20255501{i:02d}", name="Synthetic Patient")
            if not db.get("patients", pid):
                db.put(
                    "patients",
                    pid,
                    dict(
                        id=pid,
                        location_id=loc,
                        prerequisites=[],
                        minor=False,
                        guardian_contact=False,
                        guardian_consent=False,
                    ),
                )

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    def location(loc):
        if loc not in packs:
            raise HTTPException(404, "Unknown demonstration location")
        return packs[loc]

    def patient(auth, loc):
        location(loc)
        if not auth or not auth.startswith("Bearer "):
            raise HTTPException(401, "Verification required")
        try:
            pid = identity.require(auth[7:], loc)
        except PolicyError:
            raise HTTPException(401, "Verification required")
        p = db.get("patients", pid)
        if not p or p.get("location_id") != loc:
            raise HTTPException(401, "Verification required")
        workflows.audit(pid, "patient_record_access", loc, pid, ["prerequisites", "minor"])
        return p

    def staff_auth(auth, loc, action):
        who = staff.get((auth or "").removeprefix("Bearer "))
        if not who:
            raise HTTPException(401, "Staff authentication required")
        try:
            authorize_staff(who["role"], who["locations"], loc, action)
        except PolicyError:
            raise HTTPException(403, "Access denied")
        workflows.audit(who["role"], "staff_" + action, loc, fields=[action])
        return who

    def process_waitlist(loc):
        # This demo creates local offers only; it never sends an external notification.
        return engine.process_waitlist(
            consent_check=lambda pid, channel, site: workflows.allowed(
                pid, site, channel, packs[site]["location"]["timezone"]
            ),
            location_id=loc,
        )

    @app.get("/health")
    def health():
        return dict(
            status="ok",
            synthetic_only=True,
            outbound="mock",
            database="postgresql" if db.postgres else "sqlite-serialized-demo",
            jurisdiction="USA demonstration profile; not legal compliance",
        )

    @app.get("/catalog")
    def catalog():
        return [
            dict(
                location_id=loc,
                name=p["name"],
                services=[
                    dict(id=s["id"], name=s["name"], prerequisites=s.get("prerequisites", [])) for s in p["services"]
                ],
            )
            for loc, p in packs.items()
        ]

    def throttle_identity(request, loc):
        client = request.client.host if request.client else "local"
        now = clock().timestamp()
        rates[client] = [t for t in rates[client] if now - t < 60]
        if len(rates[client]) >= 20:
            workflows.escalate(loc, "Verification flooding", "high")
            raise HTTPException(429, "Please contact the practice for assistance")
        rates[client].append(now)

    @app.post("/otp/request")
    def otp_request(body: OTPRequest, request: Request):
        throttle_identity(request, body.location_id)
        return identity.request_otp(body.contact, body.location_id, body.conversation_id)

    @app.post("/otp/verify")
    def otp_verify(body: OTPVerify, request: Request):
        throttle_identity(request, body.location_id)
        token = identity.verify_otp(body.contact, body.code, body.location_id, body.conversation_id)
        return dict(
            verified=bool(token),
            token=token,
            message="Verified"
            if token
            else "We could not verify those details. Please contact the practice for assistance.",
        )

    @app.post("/verify")
    def verify(body: Verify, request: Request):
        # Client-scoped throttle supplements contact and conversation lockouts.
        throttle_identity(request, body.location_id)
        token = identity.verify(body.contact, body.dob, body.location_id, body.conversation_id)
        if not token:
            workflows.escalate(body.location_id, "Verification unsuccessful", "normal")
        return dict(
            verified=bool(token),
            token=token,
            message="Verified"
            if token
            else "We could not verify those details. Please contact the practice for assistance.",
        )

    @app.get("/availability")
    def availability(
        location_id: str, service_id: str, start: datetime, end: datetime, authorization: str | None = Header(None)
    ):
        p = patient(authorization, location_id)
        return engine.search_availability(service_id, location_id, p, start, end)

    @app.post("/holds")
    def hold(body: Hold, authorization: str | None = Header(None)):
        p = patient(authorization, body.location_id)
        slot = db.get("slots", body.slot_id)
        if not slot or slot["location_id"] != body.location_id:
            raise HTTPException(404, "Slot unavailable")
        return engine.hold_slot(body.slot_id, p["id"], body.conversation_id)

    @app.post("/book")
    def book(body: Booking, authorization: str | None = Header(None)):
        p = patient(authorization, body.location_id)
        hold = db.get("holds", body.hold_id)
        if not hold or hold["location_id"] != body.location_id:
            raise HTTPException(404, "Hold unavailable")
        ap = engine.book(body.hold_id, p)
        workflows.schedule(ap)
        workflows.audit(p["id"], "book", body.location_id, ap["id"])
        return ap

    @app.get("/appointments/{aid}")
    def appointment(aid: str, location_id: str, authorization: str | None = Header(None)):
        p = patient(authorization, location_id)
        return engine.get_appointment(aid, p["id"])

    @app.post("/appointments/{aid}/cancel")
    def cancel(aid: str, location_id: str, authorization: str | None = Header(None)):
        p = patient(authorization, location_id)
        ap = engine.cancel(aid, p["id"])
        process_waitlist(location_id)
        return ap

    @app.post("/appointments/{aid}/reschedule")
    def reschedule(aid: str, body: Booking, authorization: str | None = Header(None)):
        p = patient(authorization, body.location_id)
        hold = db.get("holds", body.hold_id)
        if not hold or hold["location_id"] != body.location_id:
            raise HTTPException(404, "Hold unavailable")
        ap = engine.reschedule(aid, body.hold_id, p)
        workflows.schedule(ap)
        return ap

    @app.post("/consent")
    def consent(body: Consent, authorization: str | None = Header(None)):
        p = patient(authorization, body.location_id)
        workflows.consent(p["id"], body.location_id, body.channel, body.granted)
        return {"saved": True}

    @app.post("/waitlist")
    def waitlist(
        location_id: str,
        service_id: str,
        start: datetime,
        end: datetime,
        channel: str = "sms",
        authorization: str | None = Header(None),
    ):
        p = patient(authorization, location_id)
        c = db.get("consents", f"{p['id']}:{location_id}:{channel}")
        return engine.join_waitlist(
            p, service_id, location_id, start, end, channel=channel, consent=bool(c and c["granted"])
        )

    @app.post("/appointments/{aid}/intake-link")
    def intake_link(aid: str, location_id: str, authorization: str | None = Header(None)):
        p = patient(authorization, location_id)
        ap = engine.get_appointment(aid, p["id"])
        pack = location(location_id)
        s = next(s for s in pack["services"] if s["id"] == ap["service_id"])
        template = pack["intake_templates"][s["intake_template"]]
        return dict(token=workflows.intake_link(ap, template["required"]), expires_in_seconds=86400)

    @app.post("/intake")
    def intake(body: Intake, authorization: str | None = Header(None)):
        p = patient(authorization, body.location_id)
        return workflows.submit_intake(body.token, p["id"], body.answers)

    @app.post("/chat")
    def chat(body: Chat, request: Request, authorization: str | None = Header(None)):
        # Safety MUST precede identity, consent, and throttling. Text never persisted.
        safe = safety_screen(body.text)
        if safe:
            # Never strand a safety alert in an unknown/inaccessible location queue.
            alert_location = body.location_id if body.location_id in packs else next(iter(packs))
            workflows.escalate(
                alert_location, safe.kind, "critical" if safe.kind == "emergency" else "high", body.conversation_id
            )
            return dict(intent=safe.kind, message=safe.message, static=True)
        pack = location(body.location_id)
        text = body.text.strip().lower()
        if text in ("stop", "unsubscribe"):
            p = patient(authorization, body.location_id)
            workflows.consent(p["id"], body.location_id, body.channel, False)
            return dict(intent="stop", message="Your channel consent has been revoked.")
        if text in ("c", "r", "x", "confirm", "cancel", "reschedule"):
            p = patient(authorization, body.location_id)
            if not body.appointment_id:
                return dict(intent="handoff", message="Select your appointment after verification.")
            ap = engine.get_appointment(body.appointment_id, p["id"])
            if text in ("x", "cancel"):
                ap = engine.cancel(ap["id"], p["id"])
                process_waitlist(body.location_id)
            elif text in ("c", "confirm"):
                with db.transaction():
                    ap = engine.get_appointment(body.appointment_id, p["id"])
                    if ap["status"] not in ("booked", "confirmed"):
                        raise HTTPException(400, "Appointment unavailable")
                    ap["status"] = "confirmed"
                    db.put("appointments", ap["id"], ap)
            else:
                return dict(
                    intent="reschedule",
                    message="Choose a returned engine slot and confirm the new hold in the booking panel.",
                )
            return dict(intent="cancel" if text in ("x", "cancel") else "confirm", appointment=ap)
        for faq in pack["faq"]:
            if faq["id"] in text or ("hours" in text and faq["id"] == "hours"):
                return dict(intent="faq", message=faq["answer"], citations=[faq["source"]])
        if any(word in text for word in ("book", "appointment", "availability")):
            return dict(
                intent="book",
                message="Verify your synthetic identity, select a service, and choose a slot returned by the booking panel. No slot is confirmed until you accept its hold.",
            )
        workflows.escalate(body.location_id, "Uncertain or out-of-scope request", conversation_id=body.conversation_id)
        return dict(
            intent="handoff",
            message="This request needs staff review. It has been added to the local demonstration queue; no real staff have been contacted.",
        )

    @app.post("/agent")
    async def agent(body: AgentRequest, authorization: str | None = Header(None)):
        safe = safety_screen(body.text)
        if safe:
            loc = body.location_id if body.location_id in packs else next(iter(packs))
            workflows.escalate(loc, safe.kind, "critical" if safe.kind == "emergency" else "high", body.conversation_id)
            return dict(status="handoff", message=safe.message, static=True, results=[])
        pack = location(body.location_id)
        # All administrative tools need scoped verification; FAQ-only users can use /chat.
        p = patient(authorization, body.location_id)

        def require(value):
            if value is None:
                raise ValueError("Missing required scheduling parameter")
            return value

        def search():
            return engine.search_availability(
                require(body.service_id), body.location_id, p, require(body.start), require(body.end)
            )

        def do_hold():
            return hold(
                Hold(location_id=body.location_id, slot_id=require(body.slot_id), conversation_id=body.conversation_id),
                authorization,
            )

        def do_book():
            return book(Booking(location_id=body.location_id, hold_id=require(body.hold_id)), authorization)

        def do_reschedule():
            return reschedule(
                require(body.appointment_id),
                Booking(location_id=body.location_id, hold_id=require(body.hold_id)),
                authorization,
            )

        def do_cancel():
            return cancel(require(body.appointment_id), body.location_id, authorization)

        def do_confirm():
            with db.transaction():
                ap = engine.get_appointment(require(body.appointment_id), p["id"])
                if ap["status"] not in ("booked", "confirmed"):
                    raise ValueError("Appointment is not active")
                ap["status"] = "confirmed"
                db.put("appointments", ap["id"], ap)
                return ap

        def faq():
            from .config import answer_faq

            return answer_faq({pack["vertical"]: pack}, pack["vertical"], body.text) or {
                "answer": "Please ask staff for this information."
            }

        handlers = {
            "search_availability": search,
            "hold_slot": do_hold,
            "book": do_book,
            "reschedule": do_reschedule,
            "cancel": do_cancel,
            "confirm": do_confirm,
            "answer_faq": faq,
            "send_intake_link": lambda: intake_link(require(body.appointment_id), body.location_id, authorization),
            "join_waitlist": lambda: waitlist(
                body.location_id, require(body.service_id), require(body.start), require(body.end), "sms", authorization
            ),
        }
        # Do not send identities, DOB, phone, intake, contact values, or tool results to models.
        personal = identity.personal_values(p["id"]) if hasattr(identity, "personal_values") else ()
        context = {}
        if body.service_id:
            engine.service(body.service_id, body.location_id)
            context["service_key"] = body.service_id
        result = await orchestrator.run(
            body.text,
            context,
            handlers,
            body.conversation_id,
            known_personal_values=tuple(personal),
            verified=True,
            user_confirmed=body.user_confirmed,
            requested_action=body.action,
        )
        if result["status"] == "handoff":
            workflows.escalate(
                body.location_id,
                "Model uncertainty, unavailable tool, or budget failure",
                conversation_id=body.conversation_id,
            )
        db.put(
            "conversations",
            body.conversation_id,
            dict(
                id=body.conversation_id,
                location_id=body.location_id,
                patient_ref=p["id"],
                intent=result.get("intent", "other"),
                status=result["status"],
                redacted_body="[Message not retained]",
                ts=clock().isoformat(),
            ),
        )
        workflows.audit(p["id"], "agent_request", body.location_id, body.conversation_id, ["intent", "status"])
        return result

    @app.get("/staff/{location_id}/otp-outbox")
    def otp_outbox(location_id: str, authorization: str | None = Header(None)):
        staff_auth(authorization, location_id, "audit")
        # Local synthetic-only private delivery viewer. Guest/desk denied.
        with identity._lock:
            exists = identity._conn.execute("SELECT 1 FROM sqlite_master WHERE name='otp_outbox'").fetchone()
            if not exists:
                return []
            rows = identity._conn.execute(
                "SELECT id,patient_id,payload_encrypted,created FROM otp_outbox WHERE location_id=? ORDER BY id DESC LIMIT 20",
                (location_id,),
            ).fetchall()
            return [
                dict(id=r[0], patient_ref=r[1], payload=privacy.decrypt(r[2]), created=r[3], mocked=True) for r in rows
            ]

    @app.get("/staff/{location_id}/{view}")
    def staff_view(location_id: str, view: str, authorization: str | None = Header(None)):
        allowed = {
            "schedule": "appointments",
            "escalations": "escalations",
            "audit": "audit",
            "conversations": "conversations",
            "intake": "intakes",
            "intake_completeness": "intakes",
            "summary": "appointments",
        }
        if view not in allowed:
            raise HTTPException(404, "Unknown view")
        staff_auth(authorization, location_id, view)
        rows = [r for r in db.list(allowed[view]) if r.get("location_id") == location_id]
        if view == "summary":
            return {"appointments": len(rows), "synthetic_only": True}
        if view == "intake":
            for row in rows:
                row["answers"] = privacy.decrypt(row.pop("answers_encrypted"))
        if view == "intake_completeness":
            rows = [{k: v for k, v in r.items() if k not in ("answers_encrypted", "patient_id")} for r in rows]
        return rows

    @app.delete("/staff/{location_id}/patients/{patient_id}")
    def delete_patient(location_id: str, patient_id: str, authorization: str | None = Header(None)):
        who = staff_auth(authorization, location_id, "delete")
        from .retention import Retention

        return Retention(db, engine, identity, clock).delete_patient(
            patient_id, location_id, who["role"], who["locations"]
        )

    @app.post("/staff/{location_id}/purge-conversations")
    def purge_conversations(location_id: str, authorization: str | None = Header(None)):
        who = staff_auth(authorization, location_id, "delete")
        from .retention import Retention
        from .config import load_profile

        return Retention(db, engine, identity, clock).purge_conversations(
            load_profile()["retention_days"], location_id, who["role"], who["locations"]
        )

    @app.post("/staff/{location_id}/appointments/{aid}/approve-cancel")
    def approve_cancel(location_id: str, aid: str, authorization: str | None = Header(None)):
        staff_auth(authorization, location_id, "booking")
        ap = db.get("appointments", aid)
        if not ap or ap["location_id"] != location_id:
            raise HTTPException(404, "Appointment unavailable")
        result = engine.cancel(aid, ap["patient_id"], staff_approved=True)
        workflows.audit("staff", "late_cancel_approved", location_id, aid)
        return result

    @app.post("/staff/{location_id}/tick")
    def tick(location_id: str, authorization: str | None = Header(None)):
        staff_auth(authorization, location_id, "settings")
        process_waitlist(location_id)
        return {"mock_reminders_sent": workflows.tick(packs, location_id)}

    return app
