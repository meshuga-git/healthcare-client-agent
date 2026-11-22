# Independent synthetic-demo security review

Scope: local API, identity policy, transactional scheduling and workflow source. No real patient data, no outbound messages, no legal compliance certification, and no clinical validation.

## Findings corrected during review

- A `C` reply could change a cancelled appointment back to confirmed without reclaiming its resource allocation. Confirmation now checks current state inside a transaction.
- Consent revocation and pending-job suppression were not atomic with the send worker. They now share the database transaction boundary.
- The reminder worker looked up timezone at the wrong YAML level. It now honors the location timezone.
- A location-specific staff tick processed all sites. Reminder and waitlist processing now accept a location filter.
- Cancellation-triggered waitlist processing did not check current consent or quiet hours. The API now supplies both checks when creating local offers.
- Booking/rescheduling now explicitly check hold location; authenticated patient records must match the token's location.

## Independent regression coverage

`tests/test_security_review.py` checks safety before invalid auth/location and verification flooding; identical failed verification payloads for known/unknown contacts; patient-token location/expiry; location and role restrictions; cancelled-confirmation rejection; patient-bound encrypted single-use intake; and STOP plus location-specific quiet hours.

The broader engine suite additionally exercises expiry/backfill, atomic reschedule rollback, class capacity, 100 contenders, cross-connection SQLite races, and randomized no-overlap sequences. PostgreSQL exclusion must be separately exercised against a real test server; SQLite tests are not PostgreSQL validation.

## Remaining deployment barriers

- Safety screening is a conservative lexical demo, not a validated emergency/clinical classifier; no 99% recall claim is justified by these tests.
- Demo staff credentials and synthetic identities are deliberately known. Do not expose this as a real-patient service.
- Inbound SMS/email adapters are mocked. STOP currently needs verified in-app identity; a real channel must securely bind the sender and accept opt-out without making the recipient complete a separate authentication journey.
- Local staff escalations are database records, not monitored clinical alerts. Static responses must not imply real staff have been contacted.
- Consent, guardian, retention and emergency settings require jurisdiction/practice review. Configuration alone is not evidence of legal compliance.
- Clinical answers are encrypted locally; key management, transport protection, vendor contracts, audit review and operational controls need deployment-specific review.
