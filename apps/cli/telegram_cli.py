from __future__ import annotations

import logging
import time
from typing import Any
import typer
from sqlalchemy.orm import Session

from packages.core.db import SessionLocal
from packages.core.domain.models import DecisionRequest
from packages.core.services import decision_service, workflow_service
from packages.core.services.job_service import JobNotFoundError
from packages.core.services.telegram import (
    TelegramBotClient,
    extract_identity_from_update,
    is_authorized,
)
from packages.core.settings import settings

logger = logging.getLogger(__name__)

telegram_app = typer.Typer(help="Telegram decision adapter commands")


def format_decision_message(d: DecisionRequest) -> str:
    expires = d.expires_at.isoformat() if d.expires_at else "Never"
    return (
        f"<b>Decision Request</b>\n"
        f"<b>ID:</b> {d.id}\n"
        f"<b>Type:</b> {d.decision_type}\n"
        f"<b>Entity:</b> {d.entity_type}:{d.entity_id}\n"
        f"<b>Summary:</b> {d.summary or 'No summary'}\n"
        f"<b>Default Action:</b> {d.default_action}\n"
        f"<b>Expires At:</b> {expires}"
    )


def handle_telegram_message(
    client: TelegramBotClient,
    message: dict[str, Any],
    session: Session,
    allowed_user: int | None,
    allowed_chat: int | None,
) -> None:
    from_user = message.get("from")
    if not from_user:
        return
    user_id = from_user.get("id")
    chat = message.get("chat")
    if not chat:
        return
    chat_id = chat.get("id")

    if not is_authorized(user_id, chat_id, allowed_user, allowed_chat):
        client.send_message(chat_id, "Unauthorized user or chat.")
        return

    text = message.get("text", "").strip()
    if text.startswith("/start"):
        client.send_message(
            chat_id,
            "Welcome to the Ganesha operator bot.\n\n"
            f"<b>Your user ID:</b> <code>{user_id}</code>\n"
            f"<b>Your chat ID:</b> <code>{chat_id}</code>\n\n"
            "Add these to your local <code>.env</code> if you want to restrict access:\n"
            f"<code>TELEGRAM_ALLOWED_USER_ID={user_id}</code>\n"
            f"<code>TELEGRAM_ALLOWED_CHAT_ID={chat_id}</code>\n\n"
            "Commands:\n"
            "/decisions — pending decisions\n"
            "/decision &lt;id&gt; — show one decision\n"
            "/queue — actionable job queue\n"
            "/job &lt;id&gt; — job summary\n"
            "/application &lt;id&gt; — application summary",
        )
    elif text.startswith("/decisions"):
        pending = decision_service.list_pending_decisions(session)
        if not pending:
            client.send_message(chat_id, "No pending decisions.")
            return

        for d in pending:
            buttons = [
                {"text": opt, "callback_data": f"resolve:{d.id}:{opt}"}
                for opt in (d.options_json or [])
            ]
            markup = {"inline_keyboard": [buttons]} if buttons else None
            client.send_message(chat_id, format_decision_message(d), reply_markup=markup)
    elif text.startswith("/queue"):
        items = workflow_service.list_actionable_queue(session, limit=10)
        if not items:
            client.send_message(chat_id, "Actionable queue is empty.")
            return
        lines = ["<b>Actionable queue</b>"]
        for item in items:
            lines.append(
                f"• <code>{item.job_id}</code> [{item.lifecycle}] {item.company}: {item.title[:40]}"
            )
        client.send_message(chat_id, "\n".join(lines))
    elif text.startswith("/job"):
        parts = text.split(None, 1)
        if len(parts) < 2:
            client.send_message(chat_id, "Usage: /job <id>")
            return
        job_id = parts[1].strip()
        try:
            summary = workflow_service.format_job_summary(session, job_id)
            client.send_message(chat_id, f"<pre>{summary}</pre>")
        except JobNotFoundError:
            client.send_message(chat_id, "Job not found.")
    elif text.startswith("/application"):
        parts = text.split(None, 1)
        if len(parts) < 2:
            client.send_message(chat_id, "Usage: /application <id>")
            return
        app_id = parts[1].strip()
        try:
            summary = workflow_service.format_application_summary(session, app_id)
            client.send_message(chat_id, f"<pre>{summary}</pre>")
        except Exception:
            client.send_message(chat_id, "Application not found.")
    elif text.startswith("/decision"):
        parts = text.split(None, 1)
        if len(parts) < 2:
            client.send_message(chat_id, "Usage: /decision &lt;id&gt;")
            return
        decision_id = parts[1].strip()
        try:
            d = decision_service.get_decision_request(session, decision_id)
            if d.status != "pending":
                resolved_info = (
                    f"\n\nResolved: <b>{d.selected_action}</b> by {d.resolved_by}"
                )
                client.send_message(chat_id, format_decision_message(d) + resolved_info)
            else:
                buttons = [
                    {"text": opt, "callback_data": f"resolve:{d.id}:{opt}"}
                    for opt in (d.options_json or [])
                ]
                markup = {"inline_keyboard": [buttons]} if buttons else None
                client.send_message(chat_id, format_decision_message(d), reply_markup=markup)
        except decision_service.DecisionNotFoundError:
            client.send_message(chat_id, "Decision not found.")


def handle_telegram_callback(
    client: TelegramBotClient,
    callback_query: dict[str, Any],
    session: Session,
    allowed_user: int | None,
    allowed_chat: int | None,
) -> None:
    callback_query_id = callback_query.get("id")
    if not callback_query_id:
        return
    from_user = callback_query.get("from")
    if not from_user:
        return
    user_id = from_user.get("id")

    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")

    if not is_authorized(user_id, chat_id, allowed_user, allowed_chat):
        client.answer_callback_query(callback_query_id, text="Unauthorized.", show_alert=True)
        return

    data = callback_query.get("data", "")
    if data.startswith("resolve:"):
        parts = data.split(":", 2)
        if len(parts) < 3:
            return
        _, decision_id, action = parts
        try:
            d = decision_service.get_decision_request(session, decision_id)
            if d.status != "pending":
                # Already handled - answer callback with a pop-up and no duplicate audit events
                client.answer_callback_query(callback_query_id, text="Already handled.", show_alert=True)
                # Also remove buttons from the message if possible
                if message and chat_id:
                    msg_id = message.get("message_id")
                    if msg_id:
                        orig_text = message.get("text", "")
                        resolved_info = (
                            f"\n\nResolved: <b>{d.selected_action}</b> by {d.resolved_by}"
                        )
                        if resolved_info not in orig_text:
                            new_text = orig_text + resolved_info
                        else:
                            new_text = orig_text
                        client.edit_message_text(chat_id, msg_id, new_text, reply_markup=None)
                return

            # Resolve using service
            workflow_service.resolve_decision_with_effects(
                session,
                decision_id=decision_id,
                actor=f"telegram:{user_id}",
                selected_action=action,
            )
            session.commit()

            client.answer_callback_query(callback_query_id, text=f"Resolved: {action}")

            # Update the original message to remove buttons and show resolution
            if message and chat_id:
                msg_id = message.get("message_id")
                if msg_id:
                    orig_text = message.get("text", "")
                    new_text = orig_text + f"\n\nResolved: <b>{action}</b> by telegram:{user_id}"
                    client.edit_message_text(chat_id, msg_id, new_text, reply_markup=None)

        except Exception as e:
            logger.exception("Error processing callback query")
            client.answer_callback_query(callback_query_id, text=f"Error: {e}", show_alert=True)


@telegram_app.command("doctor")
def telegram_doctor() -> None:
    """Check Telegram configuration without contacting the Telegram API."""
    configured = settings.telegram_token_configured()
    typer.echo(f"TELEGRAM_BOT_TOKEN configured: {'yes' if configured else 'no'}")
    restrictions = settings.telegram_operator_restrictions()
    typer.echo(f"TELEGRAM_ALLOWED_USER_ID: {restrictions['allowed_user_id']}")
    typer.echo(f"TELEGRAM_ALLOWED_CHAT_ID: {restrictions['allowed_chat_id']}")
    if not configured:
        typer.echo("")
        typer.echo("Telegram is optional. Other CLI commands and tests do not require it.")
        typer.echo("Setup: copy .env.example to .env, set TELEGRAM_BOT_TOKEN from @BotFather.")
        typer.echo("Then run: python -m apps.cli.main telegram discover-ids")
        raise typer.Exit(code=1)
    typer.secho("Telegram credentials look configured.", fg=typer.colors.GREEN)


@telegram_app.command("discover-ids")
def telegram_discover_ids() -> None:
    """Fetch recent updates and print user/chat IDs (run after sending /start to the bot)."""
    if not settings.telegram_token_configured():
        typer.secho("TELEGRAM_BOT_TOKEN is not configured.", fg=typer.colors.RED)
        typer.echo("Set it in your local .env (see .env.example).")
        raise typer.Exit(code=1)

    token = settings.telegram_bot_token
    assert token is not None
    client = TelegramBotClient.from_token(token)
    updates = client.get_updates(timeout=0)
    if not updates:
        typer.echo("No updates received.")
        typer.echo("1. Start the bot: python -m apps.cli.main telegram run")
        typer.echo("2. In Telegram, open your bot and send /start")
        typer.echo("3. Re-run: python -m apps.cli.main telegram discover-ids")
        raise typer.Exit(code=2)

    seen: set[tuple[int | None, int | None]] = set()
    for update in updates:
        user_id, chat_id = extract_identity_from_update(update)
        key = (user_id, chat_id)
        if key in seen:
            continue
        seen.add(key)
        typer.echo(f"user_id={user_id} chat_id={chat_id}")
    typer.echo("")
    typer.echo("Copy the values into your local .env if you want restrictions:")
    typer.echo("  TELEGRAM_ALLOWED_USER_ID=<user_id>")
    typer.echo("  TELEGRAM_ALLOWED_CHAT_ID=<chat_id>")


@telegram_app.command("run")
def run_telegram_bot() -> None:
    """Start the Telegram decision adapter using long polling."""
    if not settings.telegram_token_configured():
        typer.secho("Error: TELEGRAM_BOT_TOKEN is not set or is still a placeholder.", fg=typer.colors.RED)
        typer.echo("Configure your local .env (see .env.example) and run: telegram doctor")
        raise typer.Exit(code=1)

    token = settings.telegram_bot_token
    assert token is not None
    allowed_user = settings.telegram_allowed_user_id
    allowed_chat = settings.telegram_allowed_chat_id

    typer.echo("Starting Ganesha Telegram long-polling adapter...")
    if allowed_user is not None:
        typer.echo(f"Allowed User ID: {allowed_user}")
    else:
        typer.echo("Allowed User ID: any (set TELEGRAM_ALLOWED_USER_ID to restrict)")
    if allowed_chat is not None:
        typer.echo(f"Allowed Chat ID: {allowed_chat}")
    else:
        typer.echo("Allowed Chat ID: any (set TELEGRAM_ALLOWED_CHAT_ID to restrict)")

    client = TelegramBotClient.from_token(token)
    offset: int | None = None

    session = SessionLocal()
    try:
        while True:
            try:
                updates = client.get_updates(offset=offset, timeout=10)
                for update in updates:
                    update_id = update.get("update_id")
                    if update_id is not None:
                        offset = update_id + 1

                    if "message" in update:
                        handle_telegram_message(
                            client, update["message"], session, allowed_user, allowed_chat
                        )
                    elif "callback_query" in update:
                        handle_telegram_callback(
                            client, update["callback_query"], session, allowed_user, allowed_chat
                        )
            except KeyboardInterrupt:
                typer.echo("\nStopping Telegram bot...")
                break
            except Exception:
                logger.exception("Error in long-polling loop")
                time.sleep(5)
    finally:
        session.close()
