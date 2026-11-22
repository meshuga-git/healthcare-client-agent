# Scheduling, Intake and No-Show Reduction Agent for Healthcare and Wellness Practices

Tech spec and implementation plan

## 1. Overview

**Problem.** Gyms, dental, physio, clinic, lab, aesthetics practices lose revenue to missed calls, no-shows, and late cancellations, and front-desk staff spend their day on bookings, reminders, and intake paperwork.

**Solution.** An agent that books, reschedules, and cancels appointments over chat, SMS, and email; sends reminders and handles confirmations; fills cancelled slots from a waitlist; collects pre-visit intake; and hands anything clinical, sensitive, or unclear to staff.

**Portfolio goal.** Show a safe, auditable, privacy-aware agent for a fictional multi-site group (Harbor Health Collective: dental, physio, aesthetics, lab, and fitness locations), using synthetic data only.

**Non-goals (MVP).** Medical advice, triage or diagnosis, billing and insurance adjudication, voice calls, real patient data, real PMS integrations (client-specific paid work).

**Compliance stance.** The design supports HIPAA and GDPR-style controls (access control, audit logs, data minimization, consent, retention). Compliance depends on deployment (agreements with vendors, hosting, policies), not code alone. In the portfolio say compliance-aware design, never HIPAA compliant.

## 2. Scope

| Module | Function | Phase |
|---|---|---|
| Booking | Book, reschedule, cancel with a deterministic scheduling engine | MVP |
| Identity, consent, safety | Verification before disclosure, channel consent, emergency handling | MVP |
| Reminders | Multi-step reminders, confirm/reschedule/cancel by reply | MVP |
| Waitlist backfill | Offer freed slots with time-limited holds | MVP |
| Intake | Pre-visit forms, completeness check, staff summary | MVP |
| Staff console | Schedule, escalations, conversations, audit view | MVP |
| No-show risk scoring | Risk-based reminders and overbooking suggestions | v1.1 |
| Post-visit follow-up | Rebooking prompts and review requests (non-clinical) | v1.1 |
| Lab results notice | Results-ready notification only, no content | v1.1 |

**Vertical config packs (YAML):** per practice type, define services, durations, buffers, resources (provider, room, equipment), prerequisites, intake templates, FAQ, and reminder wording.

| Vertical | Example rules in its pack |
|---|---|
| Dental | New-patient exam before treatment; hygiene recall intervals |
| Physio | Referral or first-assessment requirement; series booking |
| Clinic | Appointment types by provider skill; urgent-slot reserve |
| Lab | Fasting and preparation instructions; walk-in windows |
| Aesthetics | Consultation before procedure; discreet reminder wording |
| Fitness | Class capacity and waitlists; personal trainer; membership status check |

## 3. Success Metrics

Measured on a synthetic scenario suite and a simulator (section 10). Targets:

- Booking completion without staff ≥ 80% of booking scenarios
- Double bookings: 0; slots offered that violate constraints: 0
- Identity-verification bypass or cross-patient disclosure on adversarial scenarios: 0
- Emergency and clinical-question escalation recall ≥ 99%
- Intent accuracy ≥ 95%
- Reply latency p95 ≤ 4 s; cost ≤ $0.02 per conversation
- Simulated no-show reduction and waitlist fill rate (≥ 50% of cancelled slots refilled): reported as simulation outcomes with all assumptions stated; they are a measurement framework, not evidence of real-world impact

## 4. Architecture

**Flow:** Channel → gateway (consent, rate limit) → safety screen → identity check → conversation manager and agent (tools) → scheduling engine → calendar/PMS adapter; reminder scheduler and waitlist worker run alongside; staff console reads from the same store; an audit log records every PHI access.

| Component | Choice |
|---|---|
| Channels | Web chat widget, SMS (Twilio-style, mocked in demo), email; voice deferred |
| Backend | Python 3.12, FastAPI, Pydantic, async worker |
| Scheduler | Arq with Redis, or Postgres-based job table, for reminders, holds, and expiry |
| LLM | user-configurable both for intent and extraction and for dialog and tool use |
| Scheduling engine | Pure Python over Postgres; exclusion constraints prevent overlapping bookings |
| Database | Postgres; field-level encryption for personal data |
| Adapters | Calendar/PMS interface (list_availability, create, update, cancel, lookup_patient); implementations: internal store (default), mock PMS, optional Google Calendar |
| Knowledge base | Per-practice FAQ (hours, location, parking, preparation, policies), embedded and retrieved |
| Staff console | Streamlit or React |
| Observability | Structured logs with redaction, trace ID per conversation, token and cost counters |
| Deployment | Docker Compose; hosted demo with synthetic data only |

**Design principles**

1. The model converses; code schedules. Slots, rules, and holds come from the engine, never from the model.
2. Safety first: the emergency screen runs before any other processing, and its response text is static and configured.
3. Verify before disclosing; reveal the minimum necessary.
4. Consent governs every outbound message.
5. Anything clinical, sensitive, or uncertain goes to staff. On any failure, fail closed to handoff.

## 5. Conversation Workflow

1. **Receive and gate.** Check channel consent, rate limits, and quiet hours; identify the practice and location.
2. **Safety screen.** Keyword rules. Emergency or crisis signals trigger a static message (configured emergency instructions) and an immediate staff alert; no model-written advice. Clinical questions (symptoms, medication, results) get a static deflection and a staff task.
3. **Intent**: book, reschedule, cancel, confirm, FAQ, intake, waitlist, other.
4. **Identity.** For anything involving an existing appointment or record, verify two factors (for example date of birth plus phone number on file, or an SMS one-time code). Before verification, the agent neither confirms nor denies that a patient exists.
5. **Agent loop** (LLM, tools, max 8 steps): collect needs, call the engine, present only returned slots, hold the chosen slot, confirm with the patient, book.
6. **Confirm and notify.** Send confirmation with minimal details; schedule reminders; attach intake link if required by the service.
7. **Handoff.** Staff receive a summary, the conversation, and context; the patient is told what happens next and when.

**Tools**

| Tool | Type | Notes |
|---|---|---|
| verify_identity(contact, factors) | read | Rate-limited; returns verified patient token only |
| search_availability(service, constraints) | read | Engine output only |
| hold_slot(slot_id) | write | TTL of 10 minutes |
| book / reschedule / cancel(appointment) | write | Verified patients; policy-window checks |
| get_appointment | read | Verified patients only; minimal fields |
| join_waitlist(service, window) | write | Requires channel consent |
| send_intake_link(appointment) | write | Single-use link |
| answer_faq(query) | read | Practice KB with citations |
| handoff_to_staff(reason, summary) | write | Always available |

## 6. Scheduling Engine

- **Inputs.** Services, durations and buffers, provider (e.g., a personal trainer), skills and hours, rooms and equipment, holidays, minimum lead time, maximum horizon, location rules.
- **Constraints.** No overlaps (database exclusion constraints inside transactions); resources and providers matched to service; prerequisites satisfied (new-patient exam, referral, consent forms, class capacity); cancellation window and fee notices; urgent-slot reserves.
- **Holds.** A slot is held for 10 minutes while the patient confirms; expiry releases it automatically.
- **Waitlist.** On cancellation, rank eligible entries by priority, time window match, and wait time; offer sequentially with a short hold; release to the next entry on decline or expiry.
- **Testing.** Property-based tests (Hypothesis) generate random schedules and booking sequences and assert no overlaps, no constraint violations, and correct hold expiry.

## 7. Reminders and No-Show Reduction

- **Cadence (configurable).** 72 hours, 24 hours, and 2 hours before; channel by patient preference; replies C (confirm), R (reschedule), X (cancel) handled by the agent.
- **Content.** Practice name, date, time, location, and preparation notes only; discreet mode omits the service name for sensitive vertical settings.
- **Escalation.** Unconfirmed appointments at 24 hours go to a staff call list, ordered by risk.
- **Backfill.** Cancellations trigger the waitlist worker immediately.
- **v1.1 risk model.** A simple gradient-boosted or logistic model on lead time, history, weekday and hour, and appointment type; used to adjust reminders and suggest (never auto-apply) overbooking.

## 8. Safety and Policy Rules

| ID | Rule | Severity |
|---|---|---|
| H01 | No disclosure or change of appointment data before identity is verified | Critical |
| H02 | Emergency or crisis signals: static safety message plus immediate staff alert | Critical |
| H03 | No diagnosis, treatment, medication, or interpretation of results; static deflection and staff task | Critical |
| H04 | Every offered slot comes from the engine and satisfies all constraints | Critical |
| H05 | No outbound message without channel consent; honor STOP and quiet hours | Critical |
| H06 | Prerequisites checked before booking (referral, new-patient exam, forms) | Major |
| H07 | Minors: guardian contact and consent required per configuration | Major |
| H08 | Late cancellation or fee policy: notice shown, staff approval if configured | Major |
| H09 | Reminders contain minimum necessary information; discreet mode honored | Major |
| H10 | Out-of-scope or low-confidence requests are handed to staff | Major |
| H11 | Abuse, repeated failed verification, or flooding triggers lockout and alert | Major |

Each rule is code with unit tests; messages with static text are configured per practice and reviewed by a human.

## 9. Data Model and Privacy

| Table | Key fields |
|---|---|
| practices, locations | id, name, vertical, timezone, emergency_text |
| providers, resources | id, location_id, skills, hours, type |
| services, service_rules | id, practice_id, duration, buffer, prerequisites, discreet |
| availability_rules, closures | id, owner_ref, schedule, exceptions |
| patients (encrypted fields) | id, practice_id, name, dob, phone, email, guardian_ref |
| consents | id, patient_id, channel, status, source, ts |
| appointments | id, patient_id, service_id, provider_id, resource_id, start, end, status |
| holds | id, slot, conversation_id, expires_at |
| waitlist_entries | id, patient_id, service_id, window, priority, status |
| reminders | id, appointment_id, step, channel, status, sent_at, response |
| intake_forms, intake_responses | id, appointment_id, template, answers_encrypted, completeness |
| conversations, messages | id, patient_ref, channel, redacted_body, intent, status |
| verification_events | id, conversation_id, method, result, ts |
| escalations | id, conversation_id, reason, severity, assigned_to, status |
| audit_log | id, actor, action, subject_ref, fields_accessed, trace_id, ts |
| eval_scenarios, eval_runs | id, vertical, script, expected_state, metrics, ts |

**Privacy measures**

- **Data minimization to the LLM.** The model receives pseudonymous tokens and only the fields the step needs; names, dates of birth, and phone numbers are substituted by code and restored only in outbound messages.
- **Encryption and access.** Encryption in transit and at rest, field-level encryption for personal data, role-based access (front desk, provider, admin), secrets in environment variables.
- **Logging.** Redacted logs; every PHI access recorded in audit_log.
- **Retention and deletion.** Configurable retention and a deletion workflow.
- **Production readiness.** Before any real patient data: signed agreements with all processors (including the model provider), hosting review, and a documented risk assessment. The portfolio uses synthetic data only.

## 10. Evaluation

**Scenario suite.** About 200 scripted scenarios across the six verticals (roughly 25 to 35 each), covering booking, rescheduling, cancellation, FAQ, intake, waitlist offers, emergencies, clinical questions (for example medication dosage), identity attacks (impersonation, social engineering for another patient's appointment, enumeration of patients), prompt injection, out-of-hours requests, minors, ambiguous requests, and multi-intent messages.

**Method.** A user-simulator LLM plays the patient from each script, with fixed adversarial scripts for attacks. Deterministic checkers inspect the final database state (appointment correct, no overlap, no disclosure, escalation created, consent respected) and the transcript (no medical advice, static safety text used). Split about 150 dev / 50 held-out; do not tune on held-out.

**No-show simulator.** Generate a synthetic patient population with no-show propensity driven by lead time, history, weekday, hour, and appointment type; apply reminder policies and waitlist backfill; compare against a no-reminder baseline across multiple random seeds. State every assumption; present results as an illustration of the measurement method and the intended pilot protocol, not as proof of effect.

**Harness.** One CLI command (eval run) reports completion rate, constraint violations, safety and verification results per rule, intent accuracy, latency, and cost; prints failures; stores results in eval_runs. Run on every prompt, model, rule, or config change.

## 11. Implementation Plan

**Phase 0:**

1. Set up repo, environment, Docker Compose (app, Postgres, Redis), linting, CI with tests. *Exit:* docker compose up serves a health endpoint.
2. Build a test LLM SDK wrapper with retries, timeouts, token and cost logging, redaction hooks, and model names from config. *Exit:* a test call logs cost with no personal data in the log.

**Phase 1: Domain model and scheduling engine**

3. Create the schema with exclusion constraints and encrypted personal-data fields.
4. Implement availability computation with constraints, prerequisites, and policies.
5. Implement holds with expiry and the waitlist ranking and offer logic.
6. Write unit and property-based tests. *Exit:* 0 overlaps across 10,000 random booking sequences.

**Phase 2: Vertical config packs**

7. Define the YAML schema; write packs for dental, physio, clinic, lab, aesthetics, and fitness, with FAQ and intake templates each.
8. Load and validate packs; seed the synthetic group with providers, patients, and appointments. *Exit:* each vertical books a valid appointment through the engine API.

**Phase 3: Identity, consent, and safety layer**

9. Implement identity verification, rate limits, lockouts, and one-time codes (mocked SMS).
10. Implement consent tracking, STOP handling, quiet hours, and discreet mode.
11. Implement the safety screen (keywords plus classifier) with static configured responses and staff alerts.
12. Unit test rules H01, H02, H03, H05, H11. *Exit:* all pass, including a negative test for every rule.

**Phase 4: Conversation agent**

13. Build intent classification, entity extraction, and an LLM tool-use loop with step and token limits.
14. Implement all tools against the engine and adapter interface; implement the practice FAQ with retrieval.
15. Implement handoff with summaries and context.
16. Run the first scenario pass on the dev set. *Exit:* 0 constraint violations and 0 disclosure incidents on dev set.

**Phase 5: Reminders and waitlist**

17. Build the reminder scheduler, templates, and reply handling (confirm, reschedule, cancel).
18. Build the waitlist worker that triggers on cancellation, with timed offers and release. *Exit:* a simulated cancellation refills from the waitlist end to end.

**Phase 6: Intake**

19. Build form templates, single-use links, encrypted storage, and completeness checks; generate a non-clinical staff summary (what is missing, forms signed), with clinical answers displayed verbatim to staff only.

**Phase 7: Staff console**

20. Build today's schedule, escalation queue with severity, conversation view, manual booking override, and audit view.
21. Add role-based access and per-practice settings (reminder cadence, policy windows, emergency text). *Exit:* a front-desk user cannot see data outside their location.

**Phase 8: Evaluation and simulation**

22. Write 200 scenarios and the deterministic checkers; build the user simulator.
23. Build the no-show simulator with documented assumptions and a baseline comparison.
24. Run dev evals; fix failures; tune thresholds. *Exit:* dev results meet section 3 targets or gaps are documented.

**Phase 9: Hardening**

25. Run the held-out suite and a red-team pass (impersonation, enumeration, injection, flooding); fix findings; freeze and tag prompt, rule, and config versions.
26. Load test with 100 concurrent conversations and a reminder burst; verify holds, retries, and cost caps. *Exit:* 0 critical findings open.

**Phase 10: v1.1 modules**

27. Risk model on simulated history, with risk-ordered call list and overbooking suggestions as advisory output.
28. Post-visit follow-up and lab results-ready notices, using non-clinical templates only. Give each module a 20-case mini eval.

**Phase 11: Packaging**

29. Deploy the demo with seeded synthetic practices and a read-only guest login; no real credentials or patient data.
30. Write a one-page design-for-compliance note: data flows, controls, what is out of scope, and what a production deployment would still require.
31. Produce the architecture diagram, README (problem, results, limits), and a 3-minute demo video showing booking, an attempted impersonation being refused, and a cancellation refilled from the waitlist.
32. Write a one-page case study that labels simulator results as simulated.

## 12. Portfolio Deliverables

- Public repo with README and architecture diagram
- Live demo with guest access
- 3-minute demo video
- Eval report (completion, constraint violations, per-rule safety results, failure analysis)
- Design-for-compliance note
- One-page case study with clearly labeled simulation results

## 13. Risks

| Risk | Mitigation |
|---|---|
| Patient safety: missed emergency or clinical question | Static screen before everything, high-recall target, mandatory staff alert, regular red-team runs |
| Disclosure to the wrong person | Verification before any data, no existence confirmation, rate limits, adversarial scenarios as release gate |
| Double booking or invalid slots | Engine-only slots, database exclusion constraints, property-based tests |
| Overclaiming compliance or impact | Compliance-aware wording, simulated results labeled, assumptions published |
| Consent or messaging violations | Consent table checked at send time, STOP handling, quiet hours, frequency caps |
| Real-world integration cost (PMS vendors) | Adapter interface; real integrations scoped as paid client work |
| Scope creep | v1.1 modules start only after MVP exit criteria |

## 14. Assumptions and Open Decisions

- English only in MVP; one timezone per location.
- Streamlit is the default staff console.
- SMS, email, and code delivery are mocked in the public demo.
- Jurisdiction-specific rules (consent wording, retention periods, guardian age) live in config; implement one profile first.
- A voice channel is a later add-on built on the same engine and tools.
