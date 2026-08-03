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

4. Initialize DB and run migrations:
   make migrate

5. Run tests:
   make test

Product loop (local)
--------------------
After `make migrate`, run the end-to-end demo workflow:

   make seed-demo
   .venv/bin/python -m apps.cli.main workflow dashboard
   .venv/bin/python -m apps.cli.main workflow queue
   .venv/bin/python -m apps.cli.main decisions list

Job search imports (place source files under `sheets/` locally):

   .venv/bin/python -m apps.cli.main jobs import-sheets
   .venv/bin/python -m apps.cli.main jobs queue --target-only
   .venv/bin/python -m apps.cli.main jobs queue --referral-only
   .venv/bin/python -m apps.cli.main jobs show <job_id>

Supported inputs: LinkedIn scraper CSV, Symplicity manual XLSX, referral contacts XLSX.
Ranking is deterministic (source priority, freshness, track fit, early-career vs senior/clearance signals, target companies, referral matches).

Optional Telegram operator console (requires local `.env`; see [docs/telegram-setup.md](docs/telegram-setup.md)):

   .venv/bin/python -m apps.cli.main telegram doctor
   .venv/bin/python -m apps.cli.main telegram run

See Makefile and docs/runbook.md for full commands and notes.

Database migrations
-------------------
To run the database migrations locally (Postgres in Docker Compose):

make compose-up
make migrate
.venv/bin/python -m alembic current

(Ensure .venv is created with Python 3.12 and activated when running alembic directly.)
