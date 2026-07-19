from __future__ import annotations

import typer
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.services import decision_service
from packages.core.domain.models import DecisionRequest


decisions_app = typer.Typer(help="Decision request commands")


@decisions_app.command("list")
def list_decisions(all: bool = typer.Option(False, "--all", help="Include non-pending decisions"), decision_type: str | None = None) -> None:
    session: Session = SessionLocal()
    try:
        if all:
            rows = session.query(DecisionRequest).order_by(DecisionRequest.created_at.desc()).limit(200).all()
        else:
            rows = decision_service.list_pending_decisions(session, decision_type=decision_type)
        for r in rows:
            expires = r.expires_at.isoformat() if r.expires_at else "-"
            typer.echo(f"{r.id} | {r.decision_type} | {r.entity_type}:{r.entity_id} | expires={expires} | default={r.default_action} | {r.summary}")
    finally:
        session.close()


@decisions_app.command("show")
def show_decision(decision_id: str) -> None:
    session: Session = SessionLocal()
    try:
        d = decision_service.get_decision_request(session, decision_id)
        typer.echo(f"ID: {d.id}")
        typer.echo(f"Type: {d.decision_type}")
        typer.echo(f"Entity: {d.entity_type}:{d.entity_id}")
        typer.echo(f"Status: {d.status}")
        typer.echo(f"Summary: {d.summary}")
        typer.echo(f"Options: {d.options_json}")
        typer.echo(f"Default: {d.default_action}")
        typer.echo(f"Expires at: {d.expires_at}")
        if d.status != "pending":
            typer.echo(f"Resolved at: {d.resolved_at} by {d.resolved_by}")
            typer.echo(f"Selected action: {d.selected_action}")
            typer.echo(f"Resolution note: {d.resolution_note}")
    except Exception:
        typer.echo("Not found")
        raise typer.Exit(code=1)
    finally:
        session.close()


@decisions_app.command("resolve")
def resolve_decision(decision_id: str, action: str, actor: str = typer.Option("cli", "--actor"), note: str | None = typer.Option(None, "--note")) -> None:
    session: Session = SessionLocal()
    try:
        try:
            d = decision_service.resolve_decision_request(session, decision_id=decision_id, actor=actor, selected_action=action, note=note)
            session.commit()
            typer.echo(f"Resolved: {d.id} -> {d.selected_action} by {d.resolved_by}")
        except decision_service.DecisionNotFoundError:
            typer.echo("Decision not found")
            raise typer.Exit(code=1)
        except decision_service.InvalidActionError as exc:
            typer.echo(f"Invalid action: {exc}")
            raise typer.Exit(code=2)
        except decision_service.InvalidTransitionError as exc:
            typer.echo(f"Invalid transition: {exc}")
            raise typer.Exit(code=3)
    finally:
        session.close()


@decisions_app.command("expire-due")
def expire_due() -> None:
    session: Session = SessionLocal()
    try:
        count = decision_service.expire_due_decision_requests(session)
        session.commit()
        typer.echo(f"Expired: {count}")
    finally:
        session.close()
