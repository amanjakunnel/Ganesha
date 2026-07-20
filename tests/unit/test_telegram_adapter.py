from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import AuditEvent, Base
from packages.core.services import decision_service
from packages.core.services.telegram import TelegramBotClient, is_authorized
from apps.cli.telegram_cli import (
    handle_telegram_message,
    handle_telegram_callback,
    format_decision_message,
)


class MockTelegramBotClient(TelegramBotClient):
    """Mock telegram bot client that stores calls instead of hitting network."""

    def __init__(self) -> None:
        super().__init__("mock_token")
        self.sent_messages: List[Dict[str, Any]] = []
        self.answered_callbacks: List[Dict[str, Any]] = []
        self.edited_messages: List[Dict[str, Any]] = []

    def send_message(
        self, chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        self.sent_messages.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"message_id": 999, "chat": {"id": chat_id}, "text": text}

    def answer_callback_query(
        self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False
    ) -> Optional[bool]:
        self.answered_callbacks.append(
            {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}
        )
        return True

    def edit_message_text(
        self, chat_id: int, message_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        self.edited_messages.append(
            {"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": reply_markup}
        )
        return {"message_id": message_id, "chat": {"id": chat_id}, "text": text}


def setup_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def test_telegram_authorization() -> None:
    # 1. No constraints
    assert is_authorized(123, 456, None, None) is True

    # 2. User constraint matched
    assert is_authorized(123, 456, 123, None) is True
    # User constraint failed
    assert is_authorized(999, 456, 123, None) is False

    # 3. Chat constraint matched
    assert is_authorized(123, 456, None, 456) is True
    # Chat constraint failed
    assert is_authorized(123, 999, None, 456) is False

    # 4. Both constraints matched
    assert is_authorized(123, 456, 123, 456) is True
    # User failed, chat matched
    assert is_authorized(999, 456, 123, 456) is False
    # User matched, chat failed
    assert is_authorized(123, 999, 123, 456) is False


def test_handle_telegram_message_start() -> None:
    client = MockTelegramBotClient()
    session = SessionLocal()
    try:
        msg = {
            "from": {"id": 12345},
            "chat": {"id": 67890},
            "text": "/start",
        }
        handle_telegram_message(client, msg, session, 12345, 67890)
        assert len(client.sent_messages) == 1
        assert "Welcome to the Ganesha Decision Agent Bot" in client.sent_messages[0]["text"]
    finally:
        session.close()


def test_handle_telegram_message_decisions_empty() -> None:
    client = MockTelegramBotClient()
    session = SessionLocal()
    try:
        # Ensure database is clean of pending decisions
        for d in decision_service.list_pending_decisions(session):
            session.delete(d)
        session.commit()

        msg = {
            "from": {"id": 12345},
            "chat": {"id": 67890},
            "text": "/decisions",
        }
        handle_telegram_message(client, msg, session, 12345, 67890)
        assert len(client.sent_messages) == 1
        assert "No pending decisions." in client.sent_messages[0]["text"]
    finally:
        session.close()


def test_handle_telegram_message_decisions_nonempty() -> None:
    client = MockTelegramBotClient()
    session = SessionLocal()
    try:
        d = decision_service.create_decision_request(
            session,
            entity_type="job_posting",
            entity_id="job_abc",
            decision_type="referral_window",
            reason_code="need_input",
            summary="Should we request referral?",
            options=["yes", "no"],
            default_action="yes",
            expires_at=datetime.utcnow() + timedelta(hours=2),
        )
        session.commit()

        msg = {
            "from": {"id": 12345},
            "chat": {"id": 67890},
            "text": "/decisions",
        }
        handle_telegram_message(client, msg, session, 12345, 67890)
        assert len(client.sent_messages) == 1
        sent = client.sent_messages[0]
        assert d.id in sent["text"]

        # Verify inline keyboard rendering
        markup = sent["reply_markup"]
        assert markup is not None
        kb = markup["inline_keyboard"]
        assert len(kb) == 1
        buttons = kb[0]
        assert len(buttons) == 2
        assert buttons[0]["text"] == "yes"
        assert buttons[0]["callback_data"] == f"resolve:{d.id}:yes"
        assert buttons[1]["text"] == "no"
        assert buttons[1]["callback_data"] == f"resolve:{d.id}:no"
    finally:
        session.close()


def test_handle_telegram_message_decision_detail() -> None:
    client = MockTelegramBotClient()
    session = SessionLocal()
    try:
        d = decision_service.create_decision_request(
            session,
            entity_type="job_posting",
            entity_id="job_def",
            decision_type="track_selection",
            reason_code="select_track",
            summary="Choose track",
            options=["swe", "qa"],
            default_action="swe",
        )
        session.commit()

        # 1. Valid pending decision
        msg = {
            "from": {"id": 12345},
            "chat": {"id": 67890},
            "text": f"/decision {d.id}",
        }
        handle_telegram_message(client, msg, session, 12345, 67890)
        assert len(client.sent_messages) == 1
        assert d.id in client.sent_messages[0]["text"]
        assert client.sent_messages[0]["reply_markup"] is not None

        # 2. Invalid decision ID
        msg2 = {
            "from": {"id": 12345},
            "chat": {"id": 67890},
            "text": "/decision non_existent_id",
        }
        handle_telegram_message(client, msg2, session, 12345, 67890)
        assert len(client.sent_messages) == 2
        assert "Decision not found." in client.sent_messages[1]["text"]
    finally:
        session.close()


def test_handle_telegram_callback_resolution_and_duplicate() -> None:
    client = MockTelegramBotClient()
    session = SessionLocal()
    try:
        d = decision_service.create_decision_request(
            session,
            entity_type="job_posting",
            entity_id="job_xyz",
            decision_type="referral_window",
            reason_code="need_input",
            summary="Decide referral window",
            options=["approve", "reject"],
            default_action="approve",
        )
        session.commit()

        # Callback query payload
        cb = {
            "id": "query_123",
            "from": {"id": 12345},
            "message": {
                "message_id": 555,
                "chat": {"id": 67890},
                "text": format_decision_message(d),
            },
            "data": f"resolve:{d.id}:approve",
        }

        # 1. Resolve pending decision
        handle_telegram_callback(client, cb, session, 12345, 67890)

        # Refresh decision from DB
        session.expire(d)
        d_db = decision_service.get_decision_request(session, d.id)
        assert d_db.status == "resolved"
        assert d_db.selected_action == "approve"
        assert d_db.resolved_by == "telegram:12345"

        # Verify callback was answered and message edited (removing buttons)
        assert len(client.answered_callbacks) == 1
        assert "Resolved: approve" in client.answered_callbacks[0]["text"]
        assert len(client.edited_messages) == 1
        assert "Resolved: <b>approve</b> by telegram:12345" in client.edited_messages[0]["text"]
        assert client.edited_messages[0]["reply_markup"] is None

        # Verify audit event exists
        audit = (
            session.query(AuditEvent)
            .filter(
                AuditEvent.entity_type == "decision_request",
                AuditEvent.entity_id == d.id,
                AuditEvent.event_type == "decision_request_resolved",
            )
            .one()
        )
        assert audit.payload is not None
        assert audit.payload["actor"] == "telegram:12345"

        # 2. Test duplicate callback (should show "Already handled." pop-up)
        # Clear mock history
        client.answered_callbacks.clear()
        client.edited_messages.clear()
        audit_count_before = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_type == "decision_request", AuditEvent.entity_id == d.id)
            .count()
        )

        handle_telegram_callback(client, cb, session, 12345, 67890)

        assert len(client.answered_callbacks) == 1
        assert client.answered_callbacks[0]["text"] == "Already handled."
        assert client.answered_callbacks[0]["show_alert"] is True

        # Verify no duplicate audit events were created
        audit_count_after = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_type == "decision_request", AuditEvent.entity_id == d.id)
            .count()
        )
        assert audit_count_before == audit_count_after
    finally:
        session.close()


def test_handle_unauthorized_user() -> None:
    client = MockTelegramBotClient()
    session = SessionLocal()
    try:
        # Message test
        msg = {
            "from": {"id": 99999},  # Unauthorized user ID
            "chat": {"id": 67890},
            "text": "/decisions",
        }
        handle_telegram_message(client, msg, session, 12345, 67890)
        assert len(client.sent_messages) == 1
        assert "Unauthorized user or chat." in client.sent_messages[0]["text"]

        # Callback test
        cb = {
            "id": "query_unauth",
            "from": {"id": 99999},  # Unauthorized user ID
            "message": {
                "message_id": 555,
                "chat": {"id": 67890},
                "text": "Any text",
            },
            "data": "resolve:some_id:approve",
        }
        handle_telegram_callback(client, cb, session, 12345, 67890)
        assert len(client.answered_callbacks) == 1
        assert client.answered_callbacks[0]["text"] == "Unauthorized."
        assert client.answered_callbacks[0]["show_alert"] is True
    finally:
        session.close()
