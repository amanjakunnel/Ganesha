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

Does **not** require an API key. Does **not** submit applications or open browsers. Syncs listings only.

### launchd (macOS)

1. Copy the example plist and edit paths:

```bash
mkdir -p ~/Library/LaunchAgents
cp docs/examples/com.ganesha.job-refresh.plist \
  ~/Library/LaunchAgents/com.ganesha.job-refresh.plist
# Edit ProgramArguments to your repo + venv python
launchctl load ~/Library/LaunchAgents/com.ganesha.job-refresh.plist
```

2. Disable:

```bash
launchctl unload ~/Library/LaunchAgents/com.ganesha.job-refresh.plist
```

### cron

```cron
# Every 6 hours while the machine is on
0 */6 * * * cd /path/to/Ganesha && .venv/bin/python -m apps.cli.main jobs sync-simplifyjobs >> /tmp/ganesha-job-refresh.log 2>&1
```

Remove the crontab line to disable.
