Threat Model — Job Application Agent (Phase 1)

Scope
-----
This threat model covers Phase 1 system components running in a developer's local environment and addresses threats relevant to privacy, data leakage, integrity of application artifacts, and accidental submission of sensitive data to external services.

Assets
------
- Candidate personal data (resumes, identifiers, contact information)
- Evidence records and generated documents (resume, cover letters)
- OAuth credentials and tokens for Google Sheets
- PostgreSQL local database containing job/application data and audit logs

Assumptions
-----------
- Development and testing occur on developer machines or controlled CI environments.
- No production secrets or third-party credentials are stored in this repository.
- All third-party providers are accessed via abstracted interfaces with fake implementations used in tests.

Primary security goals
----------------------
- Confidentiality: candidate PII and credentials must not be leaked or committed.
- Integrity: audit trail (append-only) provides tamper-evidence for workflow transitions.
- Availability: local environment should be resilient to accidental failures; operations are idempotent to reduce accidental duplication.
- Safety: Phase 1 must not perform any unauthorised live submissions or evasive automation; dry-run default prevents final submissions.

Threats and mitigations
-----------------------
1. Accidental commit of secrets
   - Mitigation: .gitignore includes .env, credentials*.json, token*.json, and data/private/.gitkeep; provide `.env.example` only. Enforce developer checklist in README.

2. Sensitive data leakage in logs or CI artifacts
   - Mitigation: redact candidate PII from logs by default; AuditEvent contains references (IDs) and metadata but not raw documents unless explicitly allowed by operator commands. CI must avoid printing private fixtures.

3. Unauthorized external submissions or automation
   - Mitigation: DRY_RUN defaults to true; ATS provider implementations raise SubmissionDisabledError in dry-run mode. Documentation and code include explicit checks.

4. Tampering with audit events or database
   - Mitigation: use append-only AuditEvent table with created_at timestamps and immutable rows; record actor and reason for each event. For production, consider write-once storage or secure access controls (out of scope for Phase 1).

5. Malicious fixture or provider content causing injection
   - Mitigation: sanitize and validate incoming text; do not execute untrusted content. Providers are interfaces; fake implementations used in tests.

6. OAuth credential misuse
   - Mitigation: Google OAuth credentials are never committed. The GoogleSheetsRepository is disabled unless credentials exist locally and the operator enables it. The README and .env.example warn operators.

7. Exposure of PII via sheets sync
   - Mitigation: Sheets sync is opt-in and configurable; CSV and Fake repositories exist for dev/testing. When enabling real Google Sheets, operator must confirm consent and provide local credentials.

Operational safeguards and developer guidance
-------------------------------------------
- Developers must keep `.env` local and the credentials files out of version control.
- Pre-commit hooks (suggested) should check for common secrets patterns.
- All external operations are mediated by provider interfaces; by default, test and dev providers are harmless.
- The audit trail must be reviewed during testing to verify expected transitions.

Open concerns for later phases
-----------------------------
- Hardening audit trail (append-only file-backed ledger, signatures).
- Access controls and multi-user separation for candidate data.
- Secure storage for production credentials and tokens (secrets manager).
- Rate-limiting and throttling for real provider adapters.

