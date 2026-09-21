#!/usr/bin/env python3
"""Start the local Ganesha job dashboard (read-only, localhost only)."""

from __future__ import annotations

import os
import sys


def main() -> None:
    host = "127.0.0.1"
    port = int(os.environ.get("DASHBOARD_PORT", "8000"))
    reload = os.environ.get("DASHBOARD_RELOAD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )

    try:
        from packages.core.db import SessionLocal
        from apps.dashboard.services.jobs_dashboard_service import check_database

        session = SessionLocal()
        try:
            check_database(session)
        finally:
            session.close()
    except Exception:
        print(
            "Ganesha dashboard cannot reach the local database.\n"
            "Run: python -m apps.cli.main db doctor\n"
            "Fix the database, then retry.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    import uvicorn

    print(f"Ganesha dashboard running at http://{host}:{port}")
    uvicorn.run(
        "apps.dashboard.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
