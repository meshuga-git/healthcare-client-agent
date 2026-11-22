# Harbor Health Collective
## Synthetic US scheduling, intake and no-show workflow portfolio

**Runnable local prototype, not a production healthcare system.** Six configurable verticals: dental, physiotherapy, clinic, lab, aesthetics, fitness. No real patients, external SMS/email, clinical advice, billing, or live deployment. Jurisdiction: **USA demonstration profile**; state-specific consent, guardian age and retention need review.

### Start on Windows (PowerShell)
```powershell
cd healthcare-agent
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m uvicorn harbor.api:create_app --factory --host 127.0.0.1 --port 8000
```
In a second terminal:
```powershell
.\.venv\Scripts\python -m streamlit run harbor/console.py
```
Open **http://localhost:8501**. API docs: **http://localhost:8000/docs**. Health: `/health`. Optional third terminal: `.\.venv\Scripts\python -m harbor.worker` to run the durable local reminder/expiry worker every 15 seconds.

### Linux / macOS
```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn harbor.api:create_app --factory --host 127.0.0.1 --port 8000
# Other terminals:
.venv/bin/python -m streamlit run harbor/console.py
.venv/bin/python -m harbor.worker
```
No API key is required. Models default to explicit mocks. `INTENT_MODEL` and `DIALOG_MODEL` are independently configurable; external model use requires your separately supplied configuration and authorization. Provider costs without explicit pricing remain unknown, not zero.

### Demonstration identities and access
Phone is `2025550100` for dental, `2025550101` physio, `2025550102` clinic, `2025550103` lab, `2025550104` aesthetics, `2025550105` fitness. DOB is `1990-01-01`. These are openly documented synthetic fixtures, **not real identity proofing**. Eligibility flags default empty: restricted services require staff-reviewed prerequisites, not a patient assertion. Start with dental exam.

### Mock OTP flow (API docs)
1. `POST /otp/request` with `contact`, `location_id`, `conversation_id`. The response is deliberately identical for unknown and known contacts and contains no code.
2. As demo admin only, `GET /staff/{location_id}/otp-outbox` with `Authorization: Bearer local-demo-admin-not-for-deployment`. This local private viewer decrypts mock codes; no SMS/email is sent. Guest/desk cannot read it.
3. `POST /otp/verify` with those same fields plus `code`. A correct unexpired single-use code returns a scoped bearer token. Replay/expired/failed attempts are rejected and limited.

The console's simpler `/verify` DOB+phone fixture login remains a **weaker synthetic-demo fallback**, not mandatory OTP and not evidence of real possession. Never use either publicly documented fixture path for real patient identity.

### Model-assisted API
`POST /agent` accepts text, location/conversation IDs and explicit service/time or returned slot/hold/appointment IDs. Use a verified bearer token. Intent and dialog models are separately configured; only allowlisted handlers can execute. Writes require `user_confirmed: true`, and `action` can select an explicit hold/book step. The conversation budget is eight model calls; exhausted requests hand off. Service/time entity selection is structured, not autonomous free-text extraction. The console's simple chat is deterministic; use `/agent` in API docs to exercise configurable model orchestration.

Staff credentials are local demo conveniences, never production secrets:
- `local-demo-guest`: read-only aggregate counts, no records
- `local-demo-dental-desk`: dental-only front desk
- `local-demo-admin-not-for-deployment`: all-location administrator (override `STAFF_API_TOKEN`)

Never expose these servers beyond loopback or reuse demo credentials. The app creates `.demo-encryption.key` (mode 0600) for local restart-safe encryption if no `FIELD_ENCRYPTION_KEY` is supplied. Preserve this key to read persisted identity/intake data; production key management is out of scope. Do not commit databases, keys, or `.env`.

### Docker / PostgreSQL
```sh
docker compose up --build
```
Compose binds API/console to loopback and uses PostgreSQL for booking state. PostgreSQL migrations include exclusion constraints on resource allocation intervals; the default local SQLite fallback serializes writes with `BEGIN IMMEDIATE` and is tested separately. Identity remains an encrypted local SQLite store in this prototype. Compose/runtime PostgreSQL execution may be unverified in this delivery; consult `reports/validation.md`. No Redis is required: persisted jobs + worker use the permitted database-job design.

### Validate
```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check harbor tests
.venv/bin/python -m harbor.evaluate --help
.venv/bin/python -m harbor.simulation
```
Windows: replace `.venv/bin/python` with `.\.venv\Scripts\python`. See actual reports rather than treating target metrics as achieved. Mock classifier, deterministic API, available-host-model smoke and no-show simulation are **different evidence sets**. Simulation effect sizes are assumptions, not real outcomes.

### Design
```mermaid
flowchart LR
  UI[Streamlit synthetic chat and staff console] --> API[FastAPI gateway]
  API --> SAFE[Static safety before identity and rate gates]
  API --> AUTH[Scoped identity and server RBAC]
  AUTH --> AGENT[Bounded configurable intent/dialog boundary]
  AGENT --> ENGINE[Deterministic scheduling tools]
  ENGINE --> DB[(Postgres exclusions / serialized SQLite demo)]
  API --> PRIVATE[Encrypted identity and intake]
  WORKER[Durable reminder/waitlist worker] --> DB
  WORKER --> CONSENT[Send-time consent and quiet hours]
  CONSENT --> MOCK[Local mock outbox only]
  API --> AUDIT[Redacted audit and local staff queue]
```

Read [scope matrix](docs/scope-matrix.md), [design-for-compliance](docs/design-for-compliance.md), [case study](docs/case-study.md), and [demo walkthrough](docs/demo-script.md). Live hosting, public repository publication and video recording are not performed. v1.1 remains deferred until all MVP exit gates can be evidenced.
