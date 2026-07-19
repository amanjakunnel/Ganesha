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

Common tasks
------------
- Run unit tests:
  make test

- Linting:
  make lint

- Type checking:
  make typecheck

- Seed demo data (after migrations are implemented):
  make seed-demo

- Sheets initialization (creates tabs if missing):
  make sheets-init

Notes
-----
- Keep `.env` and credential files local and out of git.
- The default `DRY_RUN=true` ensures no accidental final submissions occur.
- Use the FrozenClock in tests to simulate timers and referral hold expirations.
