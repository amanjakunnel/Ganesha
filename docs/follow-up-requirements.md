# Follow-up requirements (assessment only)

Recorded during the eligibility/layout correction pass. Not implemented here.

## 1. Referral-company careers monitoring

### Existing behavior

- Referral contacts import: `jobs import-referrals` / `jobs import-sheets` reads
  `sheets/Referral Contacts.xlsx` via
  `packages/core/services/importers/referral_contacts_xlsx.py`.
- Contacts are stored on `referral_contacts` with company normalization
  (`company_normalize`).
- Ranking can boost jobs that already have referral contacts
  (`has_referral_contacts` in `job_ranking`).
- Cover-letter research may probe generic `https://{domain}/careers` URLs; that is
  not a scheduled careers crawler and is not tied to the referral sheet as a job
  discovery source.
- Source priority includes `company_page` and `referral_sheet`, but there is no
  active pipeline that crawls referral companies’ careers sites for new jobs.

### Gaps

- No verified official careers URL registry per company.
- No unique-company derivation → careers refresh schedule.
- No referral-company → job ingestion with dedupe against existing postings.
- Current sheet path is local (`sheets/Referral Contacts.xlsx`); do not assume an
  older uploaded copy is current without user confirmation.

### Proposed small pilot (next milestone)

1. Confirm the current referral workbook path/access with the user.
2. Derive unique companies from `referral_contacts` (one crawl target per company).
3. For 3–5 pilot companies, record a verified official careers URL manually.
4. Ingest listings through existing job intake + eligibility/fit rules.
5. Deduplicate with existing `find_duplicate_job` / import keys.
6. Never expose contact names/emails in job feeds or logs.
7. Do not send referral requests or messages.

## 2. Simplify application-history sync

### Verified supported options (as of assessment)

- **Public API / OpenAPI / webhooks / MCP:** not published
  ([simplify.jobs/llms.txt](https://simplify.jobs/llms.txt) states this explicitly;
  partnership inquiries → support@simplify.jobs).
- **Job Tracker Export CSV:** documented user export from Simplify Job Tracker
  ([help article](https://help.simplify.jobs/articles/2140179-use-the-job-tracker)).
  This is a manual export, not live sync.
- **Job Tracker Import CSV:** also documented; irrelevant as a Ganesha→Simplify
  pull.
- **Developer API:** still a feature request on Simplify feedback, not a shipped
  product API.
- Unofficial cookie/session scrapers exist on GitHub; **out of scope** (no private
  endpoint reverse-engineering, no cookie/token extraction).

### Proposed import workflow (if user provides Export CSV)

1. User exports CSV from Simplify Job Tracker.
2. Ganesha imports with source provenance `simplify` / `simplify_tracker_export`.
3. Map fields when present: company, role/title, posting URL, application date,
   status.
4. Deduplicate against existing `job_applications` / attempts by URL + company/role.
5. Distinguish missing vs known values; never mark saved/viewed as submitted.
6. Merge policy: do not overwrite richer Ganesha notes/artifacts blindly.
7. One-way import only (no two-way sync in the near term).

### Next question for the user

Please export one Simplify Job Tracker CSV (or confirm Export CSV is available on
your account) and share the column headers so an importer can be scoped. Until
then, no live Simplify history sync should be claimed.
