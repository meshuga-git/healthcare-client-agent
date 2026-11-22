# Actual validation — 2026-10-23

## Passed
- Final independent parent run: **82 passed, 1 skipped**, 22.33 seconds. Skip: live PostgreSQL integration (`TEST_POSTGRES_URL` absent). One Starlette/httpx deprecation warning; no test failure.
- Hypothesis includes **10,000 generated booking sequences**, plus class capacity, separate SQLite connection race, 100 simultaneous hold contenders, expiry, rescheduling rollback, prerequisites and resource rules.
- Ruff F-rule static checks pass; Ruff format check passes. Python compileall passes. These are the precise configured lint checks, not a claim of full static typing.
- Fixed parameterized API/database suite: **150/150 development**, **50/50 first held-out**. Detailed scenario categories and source/config hashes in JSON. Held-out was not used for tuning.
- Local concurrency: **100/100 requests**, p95 **396.09ms** in that run. This is local TestClient scheduling/safety traffic, not live-model or distributed throughput.
- Mock reminder burst: **100 fixtures; 10 revoked; 90 sent; 0 duplicate sends on second tick**. These fixtures bypass booking and are not capacity evidence.
- Actual loopback Uvicorn health returned200. Streamlit AppTest completed initial render, synthetic verification, search, hold and confirm with **no exceptions**, final status Booked. This is functional framework QA, not pixel/browser/device visual QA.
- Parent available-host-model smoke: baseline **11/13** ordinary intent matches; discovered missing standalone reply-code prompt instructions. Revised prompt explicitly defines standalone C/R/X; revised smoke **13/13**. Seven safety examples were intercepted before host model use, not evidence of clinical recall. Baseline and revised reports both retained.

## Limits / not run
- PostgreSQL exclusion DDL and optional integration test included; server unavailable here. Docker Compose not executed; Windows quickstart not executed on Windows.
- Model smoke is not a configured-provider deployment, not the 200-case LLM patient simulator, and not production safety/intent evidence. The normal suite uses mocks. Real provider latency/cost targets are unestablished.
- Safety is lexical, not clinically validated. No real patients, real messaging, live deployment, vendor agreements, or clinical efficacy evaluation.
- No-show/backfill statistics derive from explicit assumed probabilities; see simulation.json. They are not measured real-world impact.

## Reproduce
```sh
python -m pytest -q
python -m ruff check harbor tests
python -m ruff format --check harbor tests
python -m compileall -q harbor
python -m harbor.evaluate run --split dev
python -m harbor.evaluate run --split heldout
python -m harbor.loadcheck
python -m harbor.simulation
```

The first held-out run preceded the small C/R/X prompt clarification; deterministic API scenario code and labels were unchanged. Do not describe the clarification as held-out tuning. Source-hashed JSON reports preserve the tested snapshots; the final aggregate tests cover the prompt regression.
