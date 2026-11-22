# Evaluation contract and limitations

## Fixed API / database matrix

`python -m harbor.evaluate run --split dev` runs 150 development cases. The committed `evals/scenarios.json` contains a separately assigned 50-case held-out partition. Each case runs a fresh in-memory application and synthetic identity store, calls the real FastAPI routes, and checks resulting persisted state. The suite spans six verticals (33–34 cases each). This is a **parameterized stratified scenario matrix**, not 200 independent natural-language patient dialogues. Common workflows are deliberately repeated across configurations to check provider/resource/prerequisite differences. Adversarial cases check missing/wrong credentials, enumeration, location scoping, fabricated holds, and injection.

The evaluator checks all live resource allocation pairs for overlap after each successful fixture. Safety cases require exact static text and an escalation record. They do not establish population-level safety recall. The matrix does not cover every possible spelling, paraphrase, multilingual request, or emergency presentation. Unit/property tests complement it but are separate evidence.

Results distinguish `passed`, `failed`, and unmeasured values. The reported latency includes application construction, fixtures, and multiple local TestClient requests; it is **not** network or LLM reply latency. LLM intent accuracy, LLM latency, and paid model cost are null when no model runs. A fixed development-case model smoke, if performed separately, does not turn these API tests into a user-simulator evaluation.

A held-out result is valid only for its saved code/config version. If an issue is fixed after seeing held-out results, a rerun is a regression check, not a fresh held-out claim. Store the JSON report and its manifest SHA-256 with the release. The source matrix was fixed before first dev evaluation; no held-out execution occurred during initial dev implementation.

## Simulated no-show and backfill illustration

`python -m harbor.simulation` uses ten reproducible seeds and 10,000 synthetic visits per seed. A logistic equation combines lead time, prior missed visits, weekday, hour, and service category to create an **assumed**, not learned, baseline propensity. Reminder delivery is assumed to be 90%; delivered reminders are assumed to reduce the propensity by 25% relative. Paired uniform draws are used in both arms. Cancellations are independent at 10%; an eligible waiting patient exists with probability 80%, and accepts with probability 70%. Thus the expected backfill proportion is 56% by construction.

The full equation, raw counts, seeds, and arm rates are in `simulation.json`. Cancellations are excluded from the no-show denominator and evaluated separately for backfill. Seed ranges summarize Monte Carlo variation only. They do not address uncertainty in the assumed effects. No real patient data, fitted clinical model, causal evaluation, or real-world effectiveness evidence is involved. A production pilot would need prospective eligibility criteria, consent and deployment review, a pre-specified comparator, monitoring for harms, and an analysis plan independent of these illustrative outcomes.
