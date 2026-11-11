"""Opt-in Gmail readonly matching for application-confirmation emails.

Scope: gmail.readonly only. Never send, label, or modify mail.
Tokens live under ~/.ganesha/gmail/ (outside the repo). Never log tokens.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from packages.core.domain.models import SimplifyHistoryRecord
from packages.core.services.importers.simplify_tracker_csv import (
    is_ambiguous_greenhouse_embed,
)

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
TOKEN_DIR = Path.home() / ".ganesha" / "gmail"
CLIENT_SECRETS_PATH = TOKEN_DIR / "client_secret.json"
TOKEN_PATH = TOKEN_DIR / "token.json"

# Bounded search — confirmation-style only.
DEFAULT_QUERY = (
    '(subject:"thank you for applying" OR subject:"application received" OR '
    'subject:"we received your application" OR subject:"thanks for applying")'
)
DEFAULT_DAYS = 365
RATE_LIMIT_SLEEP_S = 0.25


@dataclass
class GmailMessageMeta:
    message_id: str
    received_at: datetime | None
    sender_domain: str | None
    # Minimal subject snippet used only for matching; not persisted beyond match.
    subject_hint: str = ""


@dataclass
class GmailMatchPreview:
    scanned: int = 0
    matched: int = 0
    low_confidence: int = 0
    unmatched_aggregate: int = 0
    matches: list[dict[str, Any]] = field(default_factory=list)
    setup_required: bool = False
    setup_message: str | None = None
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gmail_paths() -> dict[str, str]:
    return {
        "token_dir": str(TOKEN_DIR),
        "client_secrets": str(CLIENT_SECRETS_PATH),
        "token": str(TOKEN_PATH),
        "scope": GMAIL_SCOPE,
    }


def oauth_setup_instructions() -> str:
    return (
        "Gmail readonly matching needs a local OAuth client (Desktop app) from "
        "Google Cloud Console with scope gmail.readonly only.\n"
        f"1. Create OAuth client credentials and download JSON to:\n   {CLIENT_SECRETS_PATH}\n"
        f"2. Ensure directory exists: mkdir -p {TOKEN_DIR} && chmod 700 {TOKEN_DIR}\n"
        "3. Trigger matching from the dashboard; a browser consent window will open once.\n"
        f"4. Tokens are stored at {TOKEN_PATH} (gitignored location outside the repo).\n"
        "5. Revoke access anytime at https://myaccount.google.com/permissions "
        f"and delete {TOKEN_PATH}."
    )


def gmail_ready() -> bool:
    return CLIENT_SECRETS_PATH.is_file()


def _sender_domain(from_header: str | None) -> str | None:
    if not from_header:
        return None
    m = re.search(r"@([A-Za-z0-9.-]+)", from_header)
    if not m:
        return None
    return m.group(1).lower().rstrip(".")


def _normalize_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _company_aliases(company: str) -> set[str]:
    base = _normalize_company(company)
    aliases = {base}
    for suffix in ("inc", "llc", "corp", "ltd", "co", "company", "technologies", "labs"):
        if base.endswith(suffix) and len(base) > len(suffix) + 2:
            aliases.add(base[: -len(suffix)])
    return {a for a in aliases if a}


def _role_tokens(role: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "for", "of", "to", "in", "at", "-"}
    parts = re.split(r"[^a-z0-9]+", (role or "").lower())
    return {p for p in parts if p and p not in stop and len(p) > 1}


def match_meta_to_saved_only(
    meta: GmailMessageMeta,
    saved_rows: list[SimplifyHistoryRecord],
) -> tuple[SimplifyHistoryRecord | None, str]:
    """Return (row, confidence) where confidence is matched|low|none.

    Requires both company and role signals. Company via sender domain / alias;
    role via title token overlap with subject_hint.
    """
    domain = (meta.sender_domain or "").lower()
    subject = (meta.subject_hint or "").lower()
    role_hint_tokens = _role_tokens(subject)
    best: tuple[SimplifyHistoryRecord, str, int] | None = None

    for row in saved_rows:
        if is_ambiguous_greenhouse_embed(row.job_url or row.job_url_raw):
            continue
        aliases = _company_aliases(row.company_name)
        company_ok = False
        if domain:
            dom_norm = domain.replace(".", "")
            for alias in aliases:
                if alias and (alias in domain or alias in dom_norm):
                    company_ok = True
                    break
        role_ok = False
        row_tokens = _role_tokens(row.role_title)
        overlap = row_tokens & role_hint_tokens
        if len(overlap) >= 2 or (
            len(overlap) == 1 and next(iter(overlap)) not in {"engineer", "software"}
        ):
            role_ok = True
        if company_ok and role_ok:
            score = len(overlap) + (2 if company_ok else 0)
            conf = "matched"
            cand = (row, conf, score)
            if best is None or score > best[2]:
                best = cand
        elif company_ok or role_ok:
            # Single-side match → low confidence only if company matched.
            if company_ok and best is None:
                best = (row, "low", 1)

    if best is None:
        return None, "none"
    return best[0], best[1]


def preview_gmail_matches(
    session: Session,
    *,
    messages: list[GmailMessageMeta] | None = None,
    days: int = DEFAULT_DAYS,
    persist: bool = False,
) -> GmailMatchPreview:
    """Preview (and optionally persist) Gmail confirmation matches.

    When messages is None, attempts live Gmail API fetch. If OAuth client is
    missing, returns setup_required with exact steps and writes nothing.
    """
    preview = GmailMatchPreview(dry_run=not persist)
    if messages is None:
        if not gmail_ready():
            preview.setup_required = True
            preview.setup_message = oauth_setup_instructions()
            return preview
        try:
            messages = fetch_confirmation_messages(days=days)
        except Exception as exc:
            preview.setup_required = True
            preview.setup_message = (
                f"Gmail fetch failed: {exc.__class__.__name__}. "
                + oauth_setup_instructions()
            )
            return preview

    saved = (
        session.query(SimplifyHistoryRecord)
        .filter(SimplifyHistoryRecord.status_mapped == "saved_only")
        .all()
    )
    preview.scanned = len(messages)
    for meta in messages:
        time.sleep(0)  # placeholder for rate limit in live path
        row, conf = match_meta_to_saved_only(meta, saved)
        if row is None or conf == "none":
            preview.unmatched_aggregate += 1
            continue
        entry = {
            "message_id": meta.message_id,
            "received_at": meta.received_at.isoformat() if meta.received_at else None,
            "sender_domain": meta.sender_domain,
            "matched_company": row.company_name,
            "matched_role": row.role_title,
            "record_id": row.id,
            "confidence": conf,
        }
        # Intentionally omit subject body / full subject from stored payload.
        preview.matches.append(entry)
        if conf == "low":
            preview.low_confidence += 1
        else:
            preview.matched += 1
        if persist:
            from sqlalchemy.orm.attributes import flag_modified

            payload = dict(row.payload_json or {})
            evidence = list(payload.get("gmail_match_evidence") or [])
            evidence.append(
                {
                    "message_id": meta.message_id,
                    "received_at": entry["received_at"],
                    "sender_domain": meta.sender_domain,
                    "matched_company": row.company_name,
                    "matched_role": row.role_title,
                    "confidence": conf,
                }
            )
            payload["gmail_match_evidence"] = evidence
            row.payload_json = payload
            flag_modified(row, "payload_json")
            session.add(row)
    if persist:
        session.flush()
    return preview


def fetch_confirmation_messages(*, days: int = DEFAULT_DAYS) -> list[GmailMessageMeta]:
    """Live Gmail API fetch. Requires google-auth / google-api-python-client."""
    try:
        from google.auth.transport.requests import Request  # type: ignore[import-not-found]
        from google.oauth2.credentials import Credentials  # type: ignore[import-not-found]
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-not-found]
        from googleapiclient.discovery import build  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Install google-api-python-client google-auth-httplib2 "
            "google-auth-oauthlib to enable Gmail matching."
        ) from exc

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    creds = None
    if TOKEN_PATH.is_file():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), [GMAIL_SCOPE])
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                # Testing-mode OAuth apps often refuse refresh after ~7 days.
                raise RuntimeError(
                    f"Gmail token refresh failed ({exc.__class__.__name__}). "
                    "If the OAuth client is in Google Cloud 'Testing' status, "
                    "re-approve consent (tokens expire quickly for test apps). "
                    + revoke_instructions()
                ) from exc
        else:
            if not CLIENT_SECRETS_PATH.is_file():
                raise RuntimeError(oauth_setup_instructions())
            # Avoid macOS reverse-DNS hang inside wsgiref during local OAuth callback.
            import socket

            socket.getfqdn = lambda name="": (name or "localhost")  # type: ignore[assignment,misc]
            socket.gethostbyaddr = lambda addr: (addr, [], [addr])  # type: ignore[assignment,misc]
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_PATH), [GMAIL_SCOPE]
            )
            creds = flow.run_local_server(
                host="127.0.0.1",
                port=8765,
                open_browser=True,
            )
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        TOKEN_PATH.chmod(0o600)

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    after = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    query = f"{DEFAULT_QUERY} after:{after}"
    messages: list[GmailMessageMeta] = []
    request = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=100)
    )
    while request is not None:
        time.sleep(RATE_LIMIT_SLEEP_S)
        result = request.execute()
        for item in result.get("messages") or []:
            time.sleep(RATE_LIMIT_SLEEP_S)
            full = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=item["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = {
                h["name"].lower(): h["value"]
                for h in (full.get("payload") or {}).get("headers") or []
            }
            internal = full.get("internalDate")
            received = None
            if internal:
                received = datetime.fromtimestamp(int(internal) / 1000.0, tz=UTC)
            messages.append(
                GmailMessageMeta(
                    message_id=str(item["id"]),
                    received_at=received,
                    sender_domain=_sender_domain(headers.get("from")),
                    subject_hint=(headers.get("subject") or "")[:180],
                )
            )
        request = (
            service.users()
            .messages()
            .list_next(previous_request=request, previous_response=result)
        )
    return messages


def revoke_instructions() -> str:
    return (
        "Revoke Gmail access: Google Account → Security → Third-party access "
        f"(https://myaccount.google.com/permissions), then delete {TOKEN_PATH}."
    )
