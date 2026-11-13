# Opt-in scheduled refresh (default OFF)

Ganesha does **not** enable a scheduler by default. Sync/score/enrich run only when you click **Refresh jobs**, run CLI commands, or deliberately install launchd/cron.

## What the job does

`scripts/ganesha-scheduled-refresh.sh` → `jobs scheduled-refresh`:

1. **Refresh** — SimplifyJobs + bounded company-board sync (same path as dashboard Refresh)
2. **Score new** — Layer-1 role-family classify + Layer-2 evidence fit for unscored jobs that pass the role gate
3. **Enrich ready** — description enrichment for `ready_to_apply` candidates only

Guarantees:

- **Never** opens a browser
- **Never** fills application forms
- **Never** submits applications
- Logs under `~/.ganesha/logs/` (outside the repo)

Flags: `--dry-run`, `--skip-refresh`, `--skip-score`, `--skip-enrich`, `--score-limit`, `--enrich-limit`.

## Manual one-shot

```bash
chmod +x scripts/ganesha-scheduled-refresh.sh
./scripts/ganesha-scheduled-refresh.sh --dry-run
./scripts/ganesha-scheduled-refresh.sh
```

## launchd (macOS) — opt-in

1. Copy and edit the example plist paths:

```bash
mkdir -p ~/Library/LaunchAgents
cp docs/examples/com.ganesha.scheduled-refresh.plist \
  ~/Library/LaunchAgents/com.ganesha.scheduled-refresh.plist
# Edit WorkingDirectory / ProgramArguments to your worktree + venv
launchctl load ~/Library/LaunchAgents/com.ganesha.scheduled-refresh.plist
```

2. Disable:

```bash
launchctl unload ~/Library/LaunchAgents/com.ganesha.scheduled-refresh.plist
```

## cron — opt-in

```cron
# Every 6 hours while the machine is on (edit paths)
0 */6 * * * /Users/amanjakunnel/Tech/Ganesha-classifier-gmail/scripts/ganesha-scheduled-refresh.sh >> /Users/amanjakunnel/.ganesha/logs/cron-refresh.out 2>&1
```

Remove the crontab line to disable.

## Stale feed notice

Dashboard shows a stale notice when last successful sync is older than `JOB_FEED_STALE_HOURS` (default 24). Gmail token expired/invalid uses the same notice strip.
