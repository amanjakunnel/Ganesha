Runbook (Phase 1) — Developer operations

Setup (first time)
------------------
1. Copy the example env:
   cp .env.example .env
2. Start Postgres locally:
   docker compose up -d
3. Install dependencies:
   make setup
4. Initialize DB and run migrations:
   make migrate

Startup (local dashboard)
-------------------------
```bash
cd /path/to/Ganesha
PYTHONPATH=. .venv/bin/python -m apps.cli.main dashboard run --port 8000
```

See also: [job-feed-refresh.md](job-feed-refresh.md), [scheduled-refresh.md](scheduled-refresh.md),
[gmail-readonly-matching.md](gmail-readonly-matching.md), [git-workflow.md](git-workflow.md).

Common tasks
------------
- Run unit tests:
  make test

- Linting:
  make lint

- Type checking:
  make typecheck

- Seed demo workflow (creates demo job, fit, referral decision, application):
  make seed-demo

- Workflow dashboard:
  make workflow-dashboard

- Sheets initialization (creates tabs if missing):
  make sheets-init

- Role-family audit samples:
  `python -m apps.cli.main jobs audit-role-feed`

- Refresh + score + enrich (opt-in scheduler path):
  `python -m apps.cli.main jobs scheduled-refresh --dry-run`

- Ready-to-apply: prepare from the job detail page; enrichment for ready
  candidates is part of `jobs scheduled-refresh` (never auto-submit).

Resume generation
-----------------
See `docs/resume-generation.md` for corpus scan, doctor, generate, Desktop PDF
output, and optional Anthropic semantic provider setup.

```bash
python -m apps.cli.main resumes doctor
python -m apps.cli.main resumes scan --dry-run
python -m apps.cli.main resumes generate <job-id> --deterministic-only
```

Notes
-----
- Keep `.env` and credential files local and out of git.
- Gmail / role-profile / overrides live under `~/.ganesha/` (outside the repo).
- The default `DRY_RUN=true` ensures no accidental final submissions occur.
- Use the FrozenClock in tests to simulate timers and referral hold expirations.
