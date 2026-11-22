# Harbor Health Collective — portfolio case study

**Problem.** A fictional multi-site practice needs fewer administrative scheduling errors, clearer intake completion and a measurable way to study reminder and backfill workflows.

**Built.** Six YAML vertical packs feed a deterministic resource scheduler rather than letting a model invent availability. A bounded model-assisted administrative layer classifies redacted text and selects only trusted tool actions; explicit parameters and confirmation protect writes. Identity, consent, static safety, local staff escalation, encrypted intake, role/location separation and auditable state transitions surround the scheduling core. PostgreSQL exclusion constraints are shipped; a serialized SQLite fallback provides runnable local testing.

**Measured.** The delivered reports record actual automated checks: property-generated booking sequences, fixed synthetic API/security scenarios and local concurrent requests. See `reports/validation.md`, `eval_dev.json`, `eval_heldout.json` and `loadcheck.json` for run-specific counts and limitations. These are not real patient outcomes or clinical safety certification.

**Simulated, not proven.** The no-show simulator specifies a synthetic population equation, assumes a reminder effect, assumes cancellation and offer acceptance, and compares matched draws across ten random seeds. Its baseline/no-show and cancelled-slot fill numbers illustrate an evaluation method; they do not establish that this agent reduces no-shows. A real pilot would need prospective consented measurement, audited workflow definitions, a comparison design, and legal/clinical review.

**Trade-offs.** Strong boundaries are more important than fluent medical conversation. Clinical questions never go to a generative advice path. Ambiguous, unsupported and failed operations go to a local staff queue. The prototype prioritizes deterministic correctness and transparent reporting over external integrations or a public deployment.

**Next gates.** Validate PostgreSQL and cross-platform packaging, harden staff/transport authentication, clinically review safety language and test coverage, review US state-specific policy requirements and vendors, and conduct a supervised pilot before considering real data. v1.1 risk modules remain deferred.
