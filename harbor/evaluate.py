"""Fixed 200-case API/DB evaluation. Not an LLM user-simulator evaluation."""

from __future__ import annotations
import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi.testclient import TestClient
from .api import create_app
from .policy import EMERGENCY_TEXT, CLINICAL_TEXT

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "identity_missing": "H01",
    "identity_wrong": "H01",
    "identity_enumeration": "H01",
    "cross_location": "H01",
    "staff_scope": "H01",
    "hold_owner": "H01",
    "emergency": "H02",
    "safety_variant": "H02",
    "clinical": "H03",
    "clinical_variant": "H03",
    "multi_intent": "H03",
    "collision": "H04",
    "hold_fabrication": "H04",
    "lead_time": "H04",
    "horizon": "H04",
    "out_of_hours": "H04",
    "consent_missing": "H05",
    "consent_granted": "H05",
    "stop": "H05",
    "quiet_hours": "H05",
    "prerequisite": "H06",
    "minor": "H07",
    "late_cancel": "H08",
    "uncertain": "H10",
    "injection": "H10",
    "flood": "H11",
}


def run_case(case):
    now = [datetime(2026, 10, 26, 13, tzinfo=timezone.utc)]
    app = create_app(db_path=":memory:", identity_path=":memory:", clock=lambda: now[0])
    c = TestClient(app)
    loc = case["vertical"] + "-site"
    p = app.state.packs[loc]
    db = app.state.db
    patient_id = "synthetic-" + loc
    patient = db.get("patients", patient_id)
    patient["prerequisites"] = sorted({pre for s in p["services"] for pre in s["prerequisites"]})
    db.put("patients", patient_id, patient)
    service = p["services"][0]
    sid = service["id"]
    idx = list(app.state.packs).index(loc)
    contact = f"20255501{idx:02d}"
    body = {"contact": contact, "dob": "1990-01-01", "location_id": loc, "conversation_id": case["id"]}
    token = c.post("/verify", json=body).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    start = now[0] + timedelta(days=case["offset_days"])
    while start.weekday() > 4:
        start += timedelta(days=1)
    end = start + timedelta(hours=5)

    def avail(a=start, b=end, auth=headers, service_id=sid):
        return c.get(
            "/availability",
            params=dict(location_id=loc, service_id=service_id, start=a.isoformat(), end=b.isoformat()),
            headers=auth,
        )

    def hold(slot):
        return c.post("/holds", json=dict(location_id=loc, slot_id=slot["id"]), headers=headers)

    def book():
        r = avail()
        assert r.status_code == 200, r.text
        slots = r.json()
        assert slots, "Expected eligible booking fixture to yield slots"
        h = hold(slots[0])
        assert h.status_code == 200, h.text
        ap = c.post("/book", json=dict(location_id=loc, hold_id=h.json()["id"]), headers=headers)
        assert ap.status_code == 200, ap.text
        return ap.json()

    def chat(text, auth=None, **extra):
        return c.post(
            "/chat", json=dict(text=text, location_id=loc, conversation_id=case["id"], **extra), headers=auth or {}
        )

    kind = case["kind"]
    observed = {}
    try:
        if kind == "book":
            ap = book()
            assert db.get("appointments", ap["id"])["patient_id"] == patient_id
        elif kind in ("cancel", "reschedule", "confirm", "intake", "intake_replay", "intake_missing", "late_cancel"):
            ap = book()
            if kind == "cancel":
                assert (
                    c.post(f"/appointments/{ap['id']}/cancel", params={"location_id": loc}, headers=headers).json()[
                        "status"
                    ]
                    == "cancelled"
                )
            elif kind == "confirm":
                assert chat("C", headers, appointment_id=ap["id"]).json()["intent"] == "confirm"
            elif kind == "reschedule":
                new = avail().json()[0]
                h = hold(new).json()
                r = c.post(
                    f"/appointments/{ap['id']}/reschedule",
                    json={"location_id": loc, "hold_id": h["id"]},
                    headers=headers,
                )
                assert r.status_code == 200, r.text
                assert db.get("appointments", ap["id"])["status"] == "cancelled"
            elif kind == "late_cancel":
                now[0] = datetime.fromisoformat(ap["start"]) - timedelta(hours=1)
                # Reauthenticate because moving fixture time correctly expires old tokens.
                headers = {"Authorization": "Bearer " + c.post("/verify", json=body).json()["token"]}
                r = c.post(f"/appointments/{ap['id']}/cancel", params={"location_id": loc}, headers=headers)
                assert r.status_code == 400 and db.get("appointments", ap["id"])["status"] == "booked"
            else:
                t = c.post(
                    f"/appointments/{ap['id']}/intake-link", params={"location_id": loc}, headers=headers
                ).json()["token"]
                answers = {} if kind == "intake_missing" else {"contact_confirmed": True, "privacy_acknowledged": True}
                payload = {"location_id": loc, "token": t, "answers": answers}
                r = c.post("/intake", json=payload, headers=headers)
                assert r.status_code == 200, r.text
                assert r.json()["complete"] == (kind != "intake_missing")
                if kind == "intake_replay":
                    assert c.post("/intake", json=payload, headers=headers).status_code == 400
                assert "privacy_acknowledged" not in db.list("intakes")[0]["answers_encrypted"]
        elif kind == "expiry":
            h = hold(avail().json()[0]).json()
            now[0] += timedelta(minutes=11)
            app.state.engine.expire_holds()
            assert db.get("holds", h["id"])["status"] == "expired"
        elif kind == "collision":
            slot = avail().json()[0]
            assert hold(slot).status_code == 200
            assert hold(slot).status_code == 400
        elif kind == "identity_missing":
            assert c.get("/appointments/anything", params={"location_id": loc}).status_code == 401
        elif kind == "identity_wrong":
            assert not c.post("/verify", json={**body, "dob": "1900-01-01"}).json()["verified"]
        elif kind == "identity_enumeration":
            wrong = c.post("/verify", json={**body, "dob": "1900-01-01"}).json()
            absent = c.post("/verify", json={**body, "contact": "2025559999"}).json()
            assert wrong == absent
        elif kind == "cross_location":
            other = next(l for l in app.state.packs if l != loc)
            assert c.get("/appointments/anything", params={"location_id": other}, headers=headers).status_code == 401
        elif kind == "staff_scope":
            assert (
                c.get(
                    "/staff/physio-site/schedule", headers={"Authorization": "Bearer local-demo-dental-desk"}
                ).status_code
                == 403
            )
        elif kind in ("consent_missing", "consent_granted"):
            if kind == "consent_granted":
                c.post("/consent", json=dict(location_id=loc, channel="sms", granted=True), headers=headers)
            r = c.post(
                "/waitlist",
                params=dict(location_id=loc, service_id=sid, start=start.isoformat(), end=end.isoformat()),
                headers=headers,
            )
            assert r.status_code == (400 if kind == "consent_missing" else 200), r.text
        elif kind == "stop":
            c.post("/consent", json=dict(location_id=loc, channel="sms", granted=True), headers=headers)
            assert chat("STOP", headers, channel="sms").status_code == 200
            assert not db.get("consents", patient_id + ":" + loc + ":sms")["granted"]
        elif kind == "minor":
            patient["minor"] = True
            patient["guardian_consent"] = False
            patient["guardian_contact"] = False
            db.put("patients", patient_id, patient)
            assert avail().status_code == 400
        elif kind == "prerequisite":
            restricted = next(s for s in p["services"] if s["prerequisites"])
            patient["prerequisites"] = []
            db.put("patients", patient_id, patient)
            assert avail(service_id=restricted["id"]).status_code == 400
        elif kind == "faq":
            r = chat(case["script"]).json()
            assert r["intent"] == "faq" and r["citations"]
        elif kind in ("uncertain", "injection"):
            assert chat(case["script"]).json()["intent"] == "handoff"
        elif kind in ("emergency", "safety_variant", "clinical", "clinical_variant", "multi_intent"):
            expected = "emergency" if kind in ("emergency", "safety_variant") else "clinical"
            r = chat(case["script"]).json()
            assert r["intent"] == expected and r["static"]
            assert r["message"] == (EMERGENCY_TEXT if expected == "emergency" else CLINICAL_TEXT)
            assert db.list("escalations")
        elif kind == "out_of_hours":
            night = start.replace(hour=2)
            assert avail(night, night + timedelta(hours=1)).json() == []
        elif kind == "hold_owner":
            slot = avail().json()[0]
            other = {**slot, "patient_id": "other-patient"}
            db.put("slots", slot["id"], other)
            assert hold(other).status_code == 400
        elif kind == "hold_fabrication":
            assert hold({"id": "model-invented-slot"}).status_code in (400, 404)
        elif kind == "lead_time":
            assert avail(now[0], now[0] + timedelta(minutes=30)).json() == []
        elif kind == "horizon":
            assert avail(now[0] + timedelta(days=91), now[0] + timedelta(days=92)).json() == []
        elif kind == "quiet_hours":
            c.post("/consent", json=dict(location_id=loc, channel="sms", granted=True), headers=headers)
            now[0] = now[0].replace(hour=2)
            assert not app.state.workflows.allowed(patient_id, loc, "sms", p["location"]["timezone"])
        elif kind == "flood":
            responses = [
                c.post("/verify", json={**body, "dob": "1900-01-01", "conversation_id": f"flood-{i}"})
                for i in range(25)
            ]
            assert any(r.status_code == 429 for r in responses)
        else:
            raise AssertionError("Unknown fixture kind")
        rows = db.execute("SELECT resource_id,starts,ends,owner FROM allocations").fetchall()
        for i, a in enumerate(rows):
            for b in rows[i + 1 :]:
                assert not (a[0] == b[0] and a[1] < b[2] and a[2] > b[1]), "Overlapping resources"
        return {"passed": True, "observed": observed}
    finally:
        db.close()
        app.state.identity._conn.close()
        c.close()


def run(split="dev", output=None):
    source = ROOT / "evals/scenarios.json"
    all_cases = json.loads(source.read_text())
    cases = [c for c in all_cases if c["split"] == split or split == "all"]
    results = []
    for case in cases:
        tick = time.perf_counter()
        try:
            result = run_case(case)
        except Exception as exc:
            result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
        results.append(
            {
                "id": case["id"],
                "kind": case["kind"],
                "rule": RULES.get(case["kind"]),
                "vertical": case["vertical"],
                **result,
                "latency_ms": (time.perf_counter() - tick) * 1000,
            }
        )
    per_rule = {
        r: {
            "passed": sum(x["passed"] for x in results if x["rule"] == r),
            "total": sum(x["rule"] == r for x in results),
        }
        for r in sorted(set(RULES.values()))
    }
    report = {
        "mode": "deterministic_API_database_checks_not_LLM_dialogues",
        "split": split,
        "scenario_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder, pattern in [("harbor", "*.py"), ("configs", "*.yaml")]
            for path in sorted((ROOT / folder).glob(pattern))
        },
        "total": len(results),
        "passed": sum(x["passed"] for x in results),
        "failed": sum(not x["passed"] for x in results),
        "per_rule": per_rule,
        "fixture_latency_p95_ms": sorted(x["latency_ms"] for x in results)[max(0, int(len(results) * 0.95) - 1)],
        "latency_scope": "Includes fresh in-process app/database fixture and multiple API calls; not model reply latency or network load.",
        "llm_intent_accuracy": None,
        "llm_reply_latency_p95": None,
        "model_cost_per_conversation": None,
        "model_calls": 0,
        "results": results,
        "limitations": [
            "Parameterized stratified coverage, not 200 independent patient dialogues.",
            "No LLM user simulator was invoked.",
            "H09 reminder wording tested separately in unit tests; no H09 case in this matrix.",
            "No production reliability or real-world safety recall conclusion.",
        ],
    }
    target = Path(output) if output else ROOT / f"reports/eval_{split}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2))
    # Persist run summaries independently of patient/demo stores for reproducible reporting.
    from .db import Database
    from uuid import uuid4

    runs = Database(str(target.parent / "eval_runs.sqlite"))
    run_id = uuid4().hex
    runs.put("eval_runs", run_id, {"id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), **report})
    runs.close()
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2))
    for r in results:
        if not r["passed"]:
            print(r["id"], r["error"])
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["run"])
    p.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    p.add_argument("--output")
    args = p.parse_args()
    result = run(args.split, args.output)
    raise SystemExit(bool(result["failed"]))


if __name__ == "__main__":
    main()
