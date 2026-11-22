# Implementation and evidence matrix

The attachment describes a full portfolio roadmap, **not all delivered here**. This is a substantial runnable local prototype. v1.1 is deliberately deferred because complete MVP production/deployment exits are not met.

| Area | Implemented | Remaining / evidence limits |
|---|---|---|
| Runtime | Python3.12, FastAPI, Streamlit, requirements, Docker Compose, CI recipe | Docker/Windows/visual-browser UI not executed in this environment; Streamlit AppTest with actual loopback API passed verify/search/hold/book; no live deployment/public repo/video |
| Scheduling | Engine-only slots; skills, providers/rooms/equipment, hours/closures/holidays, lead/horizon, buffers, urgent reserve, prerequisite flags, guardians, class capacity; atomic hold/book/reschedule/cancel | No clinical eligibility inference; recall dates and multi-appointment series not implemented; no walk-in orchestration |
| Persistence | PostgreSQL exclusion migration + integration test; serialized SQLite local fallback tested | PostgreSQL runtime test skipped without TEST_POSTGRES_URL; identity remains separate encrypted SQLite; no multi-node scale claim |
| Holds/waitlist | 10-minute persisted holds, owner/expiry checks, sequential ranked offers, capacity release and consent gating | Offers local only; acceptance through verified hold/book API; no external push transport |
| Identity | DOB+phone synthetic fixture fallback; hashed expiring single-use mock OTP, replay/rate controls, scoped session tokens | Publicly documented synthetic identities are not real identity proofing; real possession transport/SSO/MFA deferred |
| Safety | Static emergency/clinical screen before identity; local escalation queue; USA911 wording | Lexical screen not clinically validated; no 99% real-world recall claim; local queue is not real staff notification |
| Agent | Independent configurable intent/dialog models; actual redacted utterance classification, bounded allowed tool plans; max8 model calls, budgets/retry/timeout, explicit write confirmation, engine-grounded results | Default mocked models; freeform entity extraction deferred: service/time/record selection is structured API input; no arbitrary autonomous prose or clinical tool use |
| FAQ | Six authored FAQ packs with config citations and transparent lexical matching | Embedding/vector retrieval deferred; citations are synthetic configuration references, not real practice policies |
| Messaging | Durable reminder jobs72/24/2h; consent revocation/STOP for verified channel, send-time consent/quiet hours, minimal/discreet text, C/R/X, local outbox, unconfirmed call queue | No SMS/email provider, trusted inbound webhook identity, automatic actual staff calling, calendar attachments; quiet hours/profile not legal policy |
| Intake | Single-use hashed expiring links, encrypted answers, completeness summary; provider/admin-only answers; desk completeness | No clinical summaries/inference; template optional-field UI incomplete; clinical free text not sent to model |
| Staff | Server RBAC/location boundaries, aggregate guest, schedule/escalations/audit/conversation metadata/intake; explicit late-cancel approval | Staff credentials are demo tokens; no SSO or production settings editor; universal manual override intentionally absent |
| Privacy | Personal-field and intake encryption, HMAC lookup, scoped audit, deletion refusing active bookings, retention purge | No TLS in loopback demo, key rotation, backup deletion, regulatory retention validation or full PHI audit certification |
| Adapters | Calendar/PMS protocol, internal implementation, outage-injectable mock | No real PMS or Google Calendar integration |
| Evaluation | 200 parameterized synthetic scenarios, fixed150/50 split, deterministic API/DB checks;10,000 Hypothesis sequences;100-concurrency local load and reminder burst | Not200 independent human/LLM patient dialogues; simulator-LLM conversations deferred; no live-provider cost/latency SLA evidence |
| Simulation |10 seeds with explicit baseline/reminder/backfill assumptions | Assumed effects are not demonstrated causal impact |
| v1.1 | Not started | Risk scoring, overbooking suggestions, post-visit prompts and results-ready notices deferred by roadmap gate |

## Target interpretation
Target booking completion, intent accuracy, emergency/clinical recall, p95 latency and per-conversation cost cannot be promoted to production claims from local fixtures. Use actual JSON reports and the parent-provided host-model smoke as separately labeled evidence. Zero failures in a bounded test set is not proof of universal safety.
