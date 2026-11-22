"""Local API concurrency and mock reminder burst checks, not deployment benchmarks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import threading
import time
from fastapi.testclient import TestClient
from .api import create_app


def run(output="reports/loadcheck.json"):
    now = [datetime(2026, 10, 26, 14, tzinfo=timezone.utc)]
    app = create_app(db_path=":memory:", identity_path=":memory:", clock=lambda: now[0])
    barrier = threading.Barrier(100)
    samples = [
        ("I cannot breathe", "emergency"),
        ("What medication dose should I take?", "clinical"),
        ("What are your opening hours?", "faq"),
        ("Please explain orbital mechanics", "handoff"),
    ]

    def worker(i):
        text, expected = samples[i % len(samples)]
        with TestClient(app) as client:
            barrier.wait(timeout=30)
            started = time.perf_counter()
            response = client.post(
                "/chat", json=dict(text=text, location_id="dental-site", conversation_id=f"load-{i}")
            )
            return dict(
                id=i,
                status=response.status_code,
                expected=expected,
                actual=response.json().get("intent"),
                latency_ms=(time.perf_counter() - started) * 1000,
            )

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=100) as pool:
        results = list(pool.map(worker, range(100)))
    wall = time.perf_counter() - started
    workflows, db = app.state.workflows, app.state.db
    for i in range(100):
        pid = f"burst-synthetic-{i}"
        workflows.consent(pid, "dental-site", "sms", True)
        ap = dict(
            id=f"burst-ap-{i}",
            patient_id=pid,
            location_id="dental-site",
            start=(now[0] + timedelta(hours=25)).isoformat(),
            status="booked",
        )
        # Isolated worker fixture; these are not engine-booked or overlapping resource claims.
        db.put("appointments", ap["id"], ap)
        workflows.schedule(ap)
    for i in range(10):
        workflows.consent(f"burst-synthetic-{i}", "dental-site", "sms", False)
    now[0] += timedelta(hours=1)
    t = time.perf_counter()
    sent = workflows.tick(app.state.packs)
    burst_ms = (time.perf_counter() - t) * 1000
    resent = workflows.tick(app.state.packs)
    outbox = db.list("outbox")
    report = {
        "kind": "local_in_process_concurrency_not_live_model_or_deployment",
        "concurrent_conversations": 100,
        "passed": sum(r["status"] == 200 and r["expected"] == r["actual"] for r in results),
        "failed": sum(not (r["status"] == 200 and r["expected"] == r["actual"]) for r in results),
        "wall_seconds": wall,
        "request_p95_ms": sorted(r["latency_ms"] for r in results)[94],
        "model_calls": 0,
        "reminder_burst": {
            "appointments_fixture": 100,
            "consent_revoked_before_send": 10,
            "expected_mock_messages": 90,
            "actual_mock_messages": sent,
            "duplicate_messages_on_second_tick": resent,
            "elapsed_ms": burst_ms,
            "passed": sent == 90
            and resent == 0
            and len(outbox) == 90
            and all("service" not in m["body"].lower() and m["mocked"] for m in outbox),
        },
        "limitations": [
            "One local in-process SQLite application; not network, Postgres, distributed worker, or LLM load.",
            "Reminder fixture bypasses booking and tests only delivery job idempotency, send-time consent, and minimal content.",
            "100 simultaneous thread starts do not imply 100 requests executing Python bytecode in parallel.",
        ],
        "requests": results,
    }
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    db.close()
    app.state.identity._conn.close()
    print(json.dumps({k: v for k, v in report.items() if k != "requests"}, indent=2))
    return report


if __name__ == "__main__":
    result = run()
    raise SystemExit(bool(result["failed"] or not result["reminder_burst"]["passed"]))
