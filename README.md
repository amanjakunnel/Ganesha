# Job Application Agent — Phase 1

This repository implements Phase 1 of a privacy-conscious, evidence-grounded job-application automation system.

Phase 1 scope (high level):
- Local project foundation and developer tooling
- Database schema and migrations (SQLAlchemy + Alembic)
- Workflow state machine and append-only audit events
- Candidate evidence model and document claim mapping
- Fake provider implementations for tests (job sources, referrals, documents, ATS, notifications)
- Google Sheets staging integration (fake and CSV implementations; Google API adapter scaffolded but guarded)
- FastAPI + Typer CLI surface for local control
- Dry-run-only application pipeline (final submission disabled by default)

Non-goals in Phase 1 (explicit):
- Any live LinkedIn automation, scraping, or messaging
- CAPTCHA bypassing or evasion of platform controls
- Live application submissions in non-dry-run mode
- Storing secrets or credentials in Git

See docs/* for architecture, threat model, and runbook notes.

Prerequisites
-------------
- Python 3.12 or newer (macOS: install via Homebrew or official installer)
- Docker Desktop (or equivalent) with Docker Compose (docker-compose command available)
- Do NOT commit secrets or credentials to this repository (.env, credentials*.json, token*.json, data/private/ are gitignored)

Quick start (macOS)
-------------------
1. Copy the example env (do NOT commit the resulting .env):
   cp .env.example .env

2. Create a Python 3.12 virtual environment and install dev deps:
   python3.12 -m venv .venv
   source .venv/bin/activate
   .venv/bin/python -m pip install --upgrade pip
   .venv/bin/python -m pip install -e ".[dev]"

3. Start local Postgres (docker-compose):
   docker-compose up -d

4. Verify database connectivity:
   .venv/bin/python -m apps.cli.main db doctor

5. Initialize DB and run migrations:
   make migrate

6. Run tests:
   make test

Product loop (local)
--------------------
After `make migrate`, run the end-to-end demo workflow:

   make seed-demo
   .venv/bin/python -m apps.cli.main workflow dashboard
   .venv/bin/python -m apps.cli.main workflow queue
   .venv/bin/python -m apps.cli.main decisions list

Job search (local operator loop)
--------------------------------
Load static local files and sync the live SimplifyJobs GitHub source:

   .venv/bin/python -m apps.cli.main jobs import-sheets
   .venv/bin/python -m apps.cli.main jobs sync-simplifyjobs
   .venv/bin/python -m apps.cli.main jobs queue --limit 10
   .venv/bin/python -m apps.cli.main jobs show <job_id>

Review or audit non-actionable jobs:

   .venv/bin/python -m apps.cli.main jobs queue-review
   .venv/bin/python -m apps.cli.main jobs queue-excluded

Telegram (after `telegram run`):

   /queue — actionable ranked jobs with source + freshness
   /job <id> — detail with apply link and eligibility state
   /sources — import/sync status summary

Source policy (this phase)
--------------------------
- **SimplifyJobs** — public GitHub README, manual CLI sync (`jobs sync-simplifyjobs`). No browser automation.
- **LinkedIn** — static CSV import from local `sheets/` only. No LinkedIn automation or authenticated scraping.
- **Symplicity** — static XLSX import from local `sheets/` only. No Penn State authenticated scraping.
- No automated job applications or external submission in this phase.
- Clearance, citizenship, and sponsorship constraints are classified internally; Telegram messages use neutral category labels only.

Supported static inputs: LinkedIn scraper CSV, Symplicity manual XLSX, referral contacts XLSX.
Ranking is deterministic (source priority, freshness, track fit, early-career vs senior/clearance signals, target companies, referral matches).
Default `jobs queue` and Telegram `/queue` show **actionable** jobs only.

Optional Telegram operator console (requires local `.env`; see [docs/telegram-setup.md](docs/telegram-setup.md)):

   .venv/bin/python -m apps.cli.main telegram doctor
   .venv/bin/python -m apps.cli.main telegram run

Local job dashboard
-------------------
Browse jobs in the local Ganesha database (binds to `127.0.0.1` only):

```bash
export PYTHONPATH=.
/Users/amanjakunnel/Tech/Ganesha/.venv/bin/python run_dashboard.py
# or: .venv/bin/python -m apps.cli.main dashboard run
```

Open `http://127.0.0.1:8000`. Fit views: Recommended, Stretch, Other roles, etc.

Operator loop
-------------
1. **Refresh** — dashboard **Refresh jobs** or `jobs sync-simplifyjobs` / opt-in `scripts/ganesha-scheduled-refresh.sh` (default OFF; see [docs/scheduled-refresh.md](docs/scheduled-refresh.md)).
2. **Role classify** — `jobs classify-role-families --apply --reclassify-all --limit N` (Layer-1). Mark false positives with **Not relevant** on job detail (stores `~/.ganesha/role_not_relevant.json`; job stays under Other roles).
3. **Score** — `jobs assess-fit-batch` (Layer-2 evidence fit).
4. **Ready-to-apply** — prepare/open/fill flows live on job detail; final submit stays manual (sibling PR may deepen autofill). Enrich ready candidates via scheduled refresh or `jobs enrich-descriptions`.
5. **Gmail** — readonly confirmation matching; preview first, persist only after review. See [docs/gmail-readonly-matching.md](docs/gmail-readonly-matching.md). Revoke at Google Account permissions + delete `~/.ganesha/gmail/token.json`.
6. **Git** — feature branches + PR; never force-push/merge from the agent ([docs/git-workflow.md](docs/git-workflow.md)).

Feasibility notes (no ingestion): [docs/source-feasibility-workday-amazon.md](docs/source-feasibility-workday-amazon.md).

If the database is unreachable, run `python -m apps.cli.main db doctor`.
See Makefile and docs/runbook.md for full commands.

Database migrations
-------------------
To run the database migrations locally (Postgres in Docker Compose):

make compose-up
make migrate
.venv/bin/python -m alembic current

(Ensure .venv is created with Python 3.12 and activated when running alembic directly.)

Local database recovery
-----------------------
If `db doctor` reports password authentication failure, the Postgres Docker volume was likely
initialized with different credentials than your current `.env`. Changing `.env` alone does not
update an existing volume.

Diagnose:

   .venv/bin/python -m apps.cli.main db doctor

When local data is disposable, reset the volume (this **deletes** the local database):

   docker compose down -v
   docker compose up -d

Then rebuild schema and reload data:

   .venv/bin/python -m apps.cli.main db-reset-local --yes
   .venv/bin/python -m apps.cli.main jobs import-sheets
   .venv/bin/python -m apps.cli.main jobs sync-simplifyjobs
   .venv/bin/python -m apps.cli.main jobs queue --limit 10

Happy path after a clean local setup:

   .venv/bin/python -m apps.cli.main db doctor
   .venv/bin/python -m apps.cli.main db-reset-local --yes
   .venv/bin/python -m apps.cli.main jobs import-sheets
   .venv/bin/python -m apps.cli.main jobs sync-simplifyjobs
   .venv/bin/python -m apps.cli.main jobs queue --limit 10
   .venv/bin/python -m apps.cli.main telegram run
