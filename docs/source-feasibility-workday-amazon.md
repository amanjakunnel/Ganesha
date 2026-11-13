# Source feasibility: Workday + Amazon Jobs

**Status:** docs / research only. **No Workday or Amazon ingest code** is
shipped with this change. Do not bypass CAPTCHA, login walls, or robots
protections.

Ganesha already reads individual Workday job-description pages via the public
CXS detail path during **JD enrichment** (when a known Workday URL is already
on a posting). That is single-URL enrichment, not board ingestion.

## Workday (myworkdayjobs.com CXS)

### Observed / documented public endpoints

Career sites of the form
`https://{tenant}.{wdN}.myworkdayjobs.com/{site}` back their UI with:

| Method | Path | Notes |
|--------|------|-------|
| `POST` | `/wday/cxs/{tenant}/{site}/jobs` | List JSON. Body: `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}`. `limit` capped at **20**. |
| `GET`  | `/wday/cxs/{tenant}/{site}{externalPath}` | Per-job detail (HTML description, ISO start date, locations). |

Unauthenticated. Workday does **not** publish this as a third-party API; it is
the careers-page candidate experience service (CXS). Tenant, `wdN` shard, and
site name are **not** guessable from a company name alone — you need a real
board URL first.

### Limits / quirks (community measurements)

- ~2,000 results cap per query; large tenants need facet/`searchText` splits.
- Later pages often report `total: 0`; stop on empty/repeat pages.
- Rate limits undocumented; no reliable rate-limit headers observed.
- Case-sensitive tenant path segments on some hosts.

### ToS / risk

- Employer / Workday terms and each tenant’s `robots.txt` apply.
- Undocumented endpoint → breakage and policy risk if polled aggressively.
- Fine for **human-paced, identifying UA, cached, small pilots** starting from
  known board URLs; poor fit for speculative slug probing.

### Recommendation

| Question | Answer |
|----------|--------|
| Ingest now? | **No** |
| Pilot size if revisited | **≤ 5 known board URLs**, list-only first (no full JD fan-out), ≥1–2s delay, hard daily cap |
| Why wait | Shard/site discovery gap; ToS ambiguity; enrichment already covers single known URLs |

## Amazon Jobs (amazon.jobs)

### Observed / documented public endpoints

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `https://www.amazon.jobs/en/search.json` | Search JSON. Typical params: `offset`, `result_limit` (max **100**), `sort`, `base_query`, optional `category[]` / `normalized_country_code[]`. |

Unauthenticated. Not documented as a public partner API. `robots.txt` historically
restricts `/internal` more than search; still treat automation as sensitive.

### Limits / quirks

- `result_limit > 100` can return **zero** jobs.
- `hits` often caps around **10,000**; deeper coverage needs category/country splits.
- Offsets past the window may return `jobs: null` with an error payload (HTTP 200).
- Full descriptions often inline (no N+1), which raises bandwidth/ToS sensitivity.

### ToS / risk

- Review [Amazon Jobs legal notices](https://www.amazon.jobs/content/en/legal-disclosures-and-notices)
  and Amazon site Terms before any automation.
- Bulk collection / republishing is higher risk than occasional human search.
- No official rate-limit contract → throttle conservatively.

### Recommendation

| Question | Answer |
|----------|--------|
| Ingest now? | **No** |
| Pilot size if revisited | **≤ 100 jobs** (one `result_limit=100` page) under a narrow `base_query` / category, identifying UA, no fan-out crawlers |
| Why wait | ToS ambiguity for automation; huge catalog; Ganesha already gets Amazon-ish roles via SimplifyJobs when present |

## Cross-cutting

- Prefer Greenhouse / Lever / Ashby public boards (already in company-board
  discovery) when an employer exposes them.
- Keep Workday JD enrichment on **known URLs** only; do not add Workday/Amazon
  board sync until product + legal comfort and a tiny opt-in pilot plan exist.
- Never bypass bot protections, login, or CAPTCHA.
