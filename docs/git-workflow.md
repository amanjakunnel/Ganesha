# Git workflow (Ganesha)

## Rules

- Work on focused feature branches (`feat/…`, `fix/…`, `chore/…`), not long-lived
  direct commits intended for `main` alone.
- Use conventional commit messages (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Push feature branches at meaningful milestones.
- Open a pull request for review. Do **not** merge to `main` from the agent.
- Never force-push. Never rewrite published history without an explicit ask.
- Never commit generated artifacts, local databases, browser profiles, Simplify
  exports, referral contact dumps, `.env`, or other personal/contact data.

## Publish checklist (before every push)

1. `git status` — no untracked private files staged.
2. Confirm `.gitignore` covers: `data/generated_*`, `data/application_attempts/`,
   `data/browser_profiles/`, `data/import_archive/`, `sheets/*`, `*.sqlite`, `.env`,
   Simplify CSVs, referral xlsx/csv.
3. Scan the commit range to push (`git diff --name-only origin/main..HEAD`) for
   paths that look like secrets or personal data.
4. Prefer `gh pr create` after `git push -u origin HEAD`.

## Manual dashboard

```bash
cd /Users/amanjakunnel/Tech/Ganesha
PYTHONPATH=. .venv/bin/python -m apps.cli.main dashboard run --port 8000
```

Stop the process when verification is done; do not leave servers running.

## Related ops docs

- [Runbook / startup](runbook.md)
- [Job feed refresh](job-feed-refresh.md)
- [Scheduled refresh](scheduled-refresh.md)
- [Gmail setup / revoke](gmail-readonly-matching.md)
- [Workday / Amazon feasibility](source-feasibility-workday-amazon.md) (no ingest)
