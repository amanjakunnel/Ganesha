from __future__ import annotations

import os

import typer

dashboard_app = typer.Typer(help="Local read-only job dashboard")


@dashboard_app.command("run")
def dashboard_run(
    port: int = typer.Option(
        None,
        "--port",
        help="Port (default 8000 or DASHBOARD_PORT)",
    ),
) -> None:
    """Start the local job dashboard on 127.0.0.1."""
    if port is not None:
        os.environ["DASHBOARD_PORT"] = str(port)
    from run_dashboard import main

    main()
