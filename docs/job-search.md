## Job search imports

Place local source files under `sheets/` (gitignored):

- `JobsScraperForLinkedIn_13_2026-07-29.csv` — LinkedIn scraper export
- `Symplicity Example.xlsx` — manual Symplicity rows
- `Referral Contacts.xlsx` — referral network contacts (enrichment, not jobs)

Commands:

```bash
.venv/bin/python -m apps.cli.main jobs import-sheets
.venv/bin/python -m apps.cli.main jobs queue --limit 20
.venv/bin/python -m apps.cli.main jobs queue --referral-only
.venv/bin/python -m apps.cli.main jobs queue --target-only
.venv/bin/python -m apps.cli.main jobs show <job_id>
```

Ranking is deterministic: source priority, freshness, track heuristics (ml/cloud/dev), early-career vs senior/clearance penalties, target-company boost, referral-contact boost.

Limitations: Symplicity remains manual XLSX import; no browser automation or authenticated Symplicity sync yet.
