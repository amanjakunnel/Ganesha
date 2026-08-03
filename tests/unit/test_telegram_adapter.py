from __future__ import annotations

from datetime import datetime, timedelta

from packages.core.db import SessionLocal, get_engine
from packages.core.domain.models import AuditEvent, Base
from packages.core.services import decision_service
from packages.core.services.telegram import (
    RecordingTelegramTransport,
    TelegramBotClient,
    is_authorized,
)
from apps.cli.telegram_cli import (
    handle_telegram_message,
    handle_telegram_callback,
    format_decision_message,
)


def _mock_client() -> TelegramBotClient:
    return TelegramBotClient(RecordingTelegramTransport())


def _sent_texts(client: TelegramBotClient) -> list[str]:
    transport = client._transport
    assert isinstance(transport, RecordingTelegramTransport)
    return [
        str((call[1] or {}).get("text", ""))
        for call in transport.calls
        if call[0] == "sendMessage"
    ]


def setup_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def teardown_module(module: object) -> None:
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)


def test_telegram_authorization() -> None:
    assert is_authorized(123, 456, None, None) is True
    assert is_authorized(123, 456, 123, None) is True
    assert is_authorized(999, 456, 123, None) is False
    assert is_authorized(123, 456, None, 456) is True
    assert is_authorized(123, 999, None, 456) is False
    assert is_authorized(123, 456, 123, 456) is True
    assert is_authorized(999, 456, 123, 456) is False
    assert is_authorized(123, 999, 123, 456) is False


def test_handle_telegram_message_start_includes_ids() -> None:
    client = _mock_client()
    session = SessionLocal()
    try:
        handle_telegram_message(
            client,
            {"from": {"id": 12345}, "chat": {"id": 67890}, "text": "/start"},
            session,
            12345,
            67890,
        )
        texts = _sent_texts(client)
        assert len(texts) == 1
        assert "67890" in texts[0]
        assert "12345" in texts[0]
        assert "TELEGRAM_ALLOWED_CHAT_ID" in texts[0]
    finally:
        session.close()


def test_handle_telegram_message_decisions_empty() -> None:
    client = _mock_client()
    session = SessionLocal()
    try:
        for d in decision_service.list_pending_decisions(session):
            session.delete(d)
        session.commit()

        handle_telegram_message(
            client,
            {"from": {"id": 12345}, "chat": {"id": 67890}, "text": "/decisions"},
            session,
            12345,
            67890,
        )
        assert "No pending decisions." in _sent_texts(client)[0]
    finally:
        session.close()


def test_handle_telegram_message_decisions_nonempty() -> None:
    client = _mock_client()
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

        handle_telegram_message(
            client,
            {"from": {"id": 12345}, "chat": {"id": 67890}, "text": "/decisions"},
            session,
            12345,
            67890,
        )
        transport = client._transport
        assert isinstance(transport, RecordingTelegramTransport)
        send_call = next(c for c in transport.calls if c[0] == "sendMessage")
        payload = send_call[1] or {}
        assert d.id in str(payload.get("text"))
        markup = payload.get("reply_markup")
        assert markup is not None
        buttons = markup["inline_keyboard"][0]
        assert buttons[0]["callback_data"] == f"resolve:{d.id}:yes"
    finally:
        session.close()


def test_handle_telegram_message_decision_detail() -> None:
    client = _mock_client()
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

        handle_telegram_message(
            client,
            {"from": {"id": 12345}, "chat": {"id": 67890}, "text": f"/decision {d.id}"},
            session,
            12345,
            67890,
        )
        assert d.id in _sent_texts(client)[0]

        handle_telegram_message(
            client,
            {"from": {"id": 12345}, "chat": {"id": 67890}, "text": "/decision non_existent_id"},
            session,
            12345,
            67890,
        )
        assert "Decision not found." in _sent_texts(client)[-1]
    finally:
        session.close()


def test_handle_telegram_callback_resolution_and_duplicate() -> None:
    client = _mock_client()
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

        handle_telegram_callback(client, cb, session, 12345, 67890)

        session.expire(d)
        d_db = decision_service.get_decision_request(session, d.id)
        assert d_db.status == "resolved"
        assert d_db.selected_action == "approve"

        transport = client._transport
        assert isinstance(transport, RecordingTelegramTransport)
        answer_calls = [c for c in transport.calls if c[0] == "answerCallbackQuery"]
        assert answer_calls
        assert "Resolved: approve" in str((answer_calls[0][1] or {}).get("text"))

        audit_count_before = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_type == "decision_request", AuditEvent.entity_id == d.id)
            .count()
        )

        handle_telegram_callback(client, cb, session, 12345, 67890)

        answer = next(c for c in transport.calls if c[0] == "answerCallbackQuery")
        payload = answer[1] or {}
        assert payload.get("text") == "Already handled."
        audit_count_after = (
            session.query(AuditEvent)
            .filter(AuditEvent.entity_type == "decision_request", AuditEvent.entity_id == d.id)
            .count()
        )
        assert audit_count_before == audit_count_after
    finally:
        session.close()


def test_handle_unauthorized_user() -> None:
    client = _mock_client()
    session = SessionLocal()
    try:
        handle_telegram_message(
            client,
            {"from": {"id": 99999}, "chat": {"id": 67890}, "text": "/decisions"},
            session,
            12345,
            67890,
        )
        assert "Unauthorized user or chat." in _sent_texts(client)[0]

        handle_telegram_callback(
            client,
            {
                "id": "query_unauth",
                "from": {"id": 99999},
                "message": {"message_id": 555, "chat": {"id": 67890}, "text": "Any text"},
                "data": "resolve:some_id:approve",
            },
            session,
            12345,
            67890,
        )
        transport = client._transport
        assert isinstance(transport, RecordingTelegramTransport)
        answer = next(c for c in transport.calls if c[0] == "answerCallbackQuery")
        payload = answer[1] or {}
        assert payload.get("text") == "Unauthorized."
    finally:
        session.close()


def test_extract_identity_from_update() -> None:
    from packages.core.services.telegram import extract_identity_from_update

    user_id, chat_id = extract_identity_from_update(
        {"message": {"from": {"id": 1}, "chat": {"id": 2}}}
    )
    assert user_id == 1
    assert chat_id == 2


def test_recording_transport_send_message() -> None:
    transport = RecordingTelegramTransport()
    client = TelegramBotClient(transport)
    client.send_message(1, "hello")
    assert transport.calls == [("sendMessage", {"chat_id": 1, "text": "hello", "parse_mode": "HTML"})]
