# Job feed refresh

## Why the feed went stale

**Root cause (2026-11-11):** There is no in-app or launchd scheduler. Source syncs ran only from the CLI (`jobs sync-simplifyjobs`). Last successful SimplifyJobs scrape before this work was **2026-11-06**; nothing failed silently—syncs simply were not run. Static LinkedIn/Symplicity imports are file-based and never auto-refresh.

Ganesha has **no default scheduler**. SimplifyJobs listings are ingested only when:

1. You run `python -m apps.cli.main jobs sync-simplifyjobs`, or
2. You click **Refresh jobs** on the dashboard (same sync path).

LinkedIn / Symplicity imports are static local files and are **not** re-fetched on refresh.

## Dashboard Refresh

- **Refresh jobs** starts a background sync (non-blocking).
- Concurrent refreshes are rejected (HTTP 409).
- A short rate limit applies between completes (default 120s; `JOB_REFRESH_MIN_INTERVAL_SECONDS`).
- Overall timeout default 600s (`JOB_REFRESH_TIMEOUT_SECONDS`).
- Stale notice appears when the last successful sync is older than `JOB_FEED_STALE_HOURS` (default 24).

Refresh updates SimplifyJobs via the existing `sync_simplifyjobs` service: dedupe, eligibility on new/updated rows, fit assessment **only for newly created jobs**, and `listing_closed` for postings missing from the latest README. Saved jobs, notes, applications, and artifacts are preserved.

## Recency presets

“Recent” uses:

1. Source **posted** date when present (SimplifyJobs age → `posted_at`), labeled **Posted**.
2. Otherwise Ganesha **first seen** (`discovered_at`), labeled **First seen**.

Presets only (no date picker): Last 24 hours, Last 48 hours, Last 7 days, Last 30 days, All.
Boundary is inclusive (exactly 24h ago still matches Last 24 hours).
Jobs with approximate/unknown first-seen (and no source `posted_at`) are excluded from time presets; their count is shown beside the preset counts. They remain visible under **All**.

Within the selected fit + recency filters, the feed sorts newest-first by the same recency clock.

Filter choices persist in `localStorage` key `ganesha.dashboard.filters` (same preference mechanism as the URL query string).

### Recommended feed honesty gap

Default **Recommended** already requires `eligibility == actionable` and fit band `recommended`, so excluded eligibility and non-recommended bands cannot top that feed. Remaining gap if JD enrichment is not yet merged: a PhD-only/ineligible role whose *stored* title/description still omit the hard requirement may be scored Recommended until enrichment refreshes the JD text.

## Optional scheduled refresh (opt-in, default off)

Prefer the fuller pipeline in **[scheduled-refresh.md](scheduled-refresh.md)**
(`jobs scheduled-refresh`: sync + score new + enrich ready-to-apply). Examples
live under `ops/` (launchd plist + cron). Default remains **off**.

Legacy SimplifyJobs-only plist still exists at
`docs/examples/com.ganesha.job-refresh.plist` if you only want sync.

Does **not** require an API key. Does **not** submit applications or open browsers.

Dashboard shows a stale notice when the last successful refresh is older than
`JOB_FEED_STALE_HOURS` (default 24), and a separate notice when the Gmail OAuth
token is expired/invalid.
