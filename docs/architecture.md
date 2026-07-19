Architecture — Job Application Agent (Phase 1)

Purpose
-------
This document describes the boundaries, dataflows, and major components for Phase 1 of the Job Application Agent. Phase 1 focuses on local foundations: data models, state machine, policy, fake provider implementations for testing, and a Safe dry-run application pipeline.

High-level components
---------------------
- API (FastAPI): lightweight HTTP surface for ingestion, operations, and inspection.
- CLI (Typer): developer and operator commands (init-db, migrate, seed-demo, sheets-sync).
- Database: PostgreSQL (docker-compose) for local development; SQLite supported for tests. ORM: SQLAlchemy 2.0 with Alembic migrations.
- Domain models: SQLAlchemy models + Pydantic DTOs for Job, Company, Application, CandidateEvidence, CandidateDocument, DocumentClaim, ReferralOpportunity, AuditEvent, WorkflowTimer, etc.
- Workflow engine: deterministic state machine governing job transitions (DISCOVERED -> NORMALIZED -> QUALIFIED -> DOCUMENTS_READY -> REFERRAL_HOLD -> READY_FOR_MANUAL_REVIEW -> READY_TO_APPLY -> SUBMITTED). Terminal states: REJECTED, DUPLICATE, MANUAL_REVIEW, FAILED, WITHDRAWN.
- Audit trail: append-only AuditEvent table. Every state change, external operation, document generation, and manual decision must write an immutable audit event with a UTC timestamp and actor metadata.
- Providers (interfaces): JobSourceProvider, DocumentGenerator, EvidenceExtractor, ReferralProvider, NotificationProvider, ATSApplicationProvider, SpreadsheetRepository, Clock.
  - Each provider has a Fake implementation for tests and a real implementation interface.
- Google Sheets integration: SpreadsheetRepository interface plus CsvRepository and FakeRepository for dev/tests. GoogleSheetsRepository implemented but guarded by configuration and local credentials files (never committed).

Design constraints and important choices
---------------------------------------
- Privacy-first: all sensitive data defaults to being local. .env.example and .gitignore are provided; credentials and private data must not be committed.
- Dry-run default: the system defaults to DRY_RUN=true and raises SubmissionDisabledError if any code attempts final submission in Phase 1.
- Idempotency: domain entities include idempotency keys and unique constraints (e.g., per-job duplicate_key). All upserts and external writes are written to be idempotent (e.g., spreadsheet upserts by job_id).
- Time and clocks: all timestamps are timezone-aware UTC. Clock interface allows FrozenClock for deterministic tests.
- External dependencies: abstracted by interfaces. Tests use fake implementations.

Dataflow (happy path — ingest to documents ready)
-------------------------------------------------
1. Ingest: A JobSourceProvider returns JobSourceRecord(s) (fixtures in Phase 1). Each record is deduplicated and stored as Job rows (state DISCOVERED). AuditEvent created: job.ingest.received.
2. Normalize: normalization service extracts canonical fields (company, title, location, ats_platform), computes job_text_hash, and duplicate_key. Transition to NORMALIZED on success; audit event recorded.
3. Qualification: policies applied (job_targeting, candidate config). Eligibility status set (ELIGIBLE, INELIGIBLE, MANUAL_REVIEW). Transition to QUALIFIED; audit event recorded.
4. Document generation: DocumentGenerator (fake in Phase 1) builds a Document package anchored to CandidateEvidence entries; DocumentClaim entries map claims to evidence IDs. Document validation requires every claim to have evidence mapping; otherwise fail and emit audit event.
5. Referral check: ReferralProvider (fake) returns referral results; if referral requested, record ReferralOpportunity, set referral_status, and transition to REFERRAL_HOLD with timer set for hold_hours (48h). Audit event recorded.
6. Hold expiration: WorkflowTimer (backed by Clock) triggers state change after hold; move to READY_TO_APPLY only if eligibility remains ELIGIBLE and no accepted referral exists.

Phase 1 exclusions and safeguards
---------------------------------
- No scraping or automation of LinkedIn or other platforms that would violate terms or require credentials.
- No CAPTCHA bypassing, no credential harvesting, and no evasion of rate limits.
- Final application SUBMITTED state is allowed in the state machine but the final transition is disabled in dry-run mode and will raise SubmissionDisabledError.
- GoogleSheetsRepository will only run if spreadsheet_id is configured and credentials are present locally; tests use Fake/Csv repositories.

Observability and logging
-------------------------
- AuditEvent table is the primary record of system behavior.
- Application logs should be structured and sufficiently verbose for debugging, but must not print private candidate documents or secret content in logs by default.

Next steps (Phase 1)
--------------------
- Implement SQLAlchemy models and Alembic migrations for the described models.
- Implement the state machine and services that perform transitions while writing AuditEvents.
- Implement fake providers and contract tests.
- Implement FastAPI endpoints and Typer CLI entries tied to services.
