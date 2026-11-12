"""Opt-in Gmail readonly matching for application-confirmation emails.

Scope: gmail.readonly only. Never send, label, or modify mail.
Tokens live under ~/.ganesha/gmail/ (outside the repo). Never log tokens.

Matching notes (why an earlier preview can scan>0 and match=0):
- DEFAULT_QUERY previously covered only a few thank-you subjects; many ATS
  confirmations use "application was received" / "thanks for your interest" /
  noreply ATS domains instead.
- Role match required ≥2 subject token overlaps (or a non-generic single token).
  Company-only hits stayed low-confidence and were easy to miss when subjects
  were generic ("thanks for applying to Engineer").
- Date window defaulted to 365 days even when saved_only rows spanned more.
"""

from __future__ import annotations

import re
import time
from collections import Counter
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

# Bounded search — confirmation-style subjects + common ATS noreply senders.
DEFAULT_QUERY = (
    "("
    'subject:"thank you for applying" OR '
    'subject:"thanks for applying" OR '
    'subject:"application received" OR '
    'subject:"we received your application" OR '
    'subject:"application was received" OR '
    'subject:"thanks for your interest" OR '
    'subject:"we have received your application" OR '
    'subject:"your application has been received" OR '
    "from:noreply@greenhouse.io OR "
    "from:no-reply@greenhouse.io OR "
    "from:noreply@lever.co OR "
    "from:no-reply@lever.co OR "
    "from:jobs@ashbyhq.com OR "
    "from:noreply@ashbyhq.com"
    ")"
)
DEFAULT_DAYS = 365
DEFAULT_MAX_RESULTS = 100
RATE_LIMIT_SLEEP_S = 0.25
_GENERIC_ROLE_TOKENS = frozenset(
    {"engineer", "software", "engineering", "developer", "intern", "new", "grad"}
)


@dataclass
class GmailMessageMeta:
    message_id: str
    received_at: datetime | None
    sender_domain: str | None
    # Minimal subject snippet used only for matching; not persisted beyond match.
    subject_hint: str = ""


@dataclass
class GmailSearchParams:
    """Exact live-search knobs (exposed for diagnose / operators)."""

    query: str
    days: int
    after_unix: int
    max_results: int = DEFAULT_MAX_RESULTS
    page_token_used: bool = True  # list_next pageToken paging is always enabled


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
    search: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GmailDiagnoseResult:
    scanned: int = 0
    matched: int = 0
    low_confidence: int = 0
    unmatched_aggregate: int = 0
    sender_domain_histogram: dict[str, int] = field(default_factory=dict)
    confirmation_looking: int = 0
    confirmation_looking_by_domain: dict[str, int] = field(default_factory=dict)
    search: dict[str, Any] = field(default_factory=dict)
    saved_only_count: int = 0
    saved_only_date_span: dict[str, str | None] = field(default_factory=dict)
    explanation: list[str] = field(default_factory=list)
    setup_required: bool = False
    setup_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        # Domains + counts only — never email bodies/subjects.
        return asdict(self)


_CONFIRMATION_SUBJECT_RE = re.compile(
    r"(thank you for applying|thanks for applying|application received|"
    r"we received your application|application was received|"
    r"thanks for your interest|we have received your application|"
    r"your application has been received)",
    re.IGNORECASE,
)


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


def saved_only_date_span(session: Session) -> tuple[datetime | None, datetime | None, int]:
    """Return (min_receivedish, max_receivedish, count) from saved_only rows.

    Uses applied_date_text when ISO-like; otherwise created_at. Never returns PII.
    """
    rows = (
        session.query(SimplifyHistoryRecord)
        .filter(SimplifyHistoryRecord.status_mapped == "saved_only")
        .all()
    )
    dates: list[datetime] = []
    for row in rows:
        text = (row.applied_date_text or "").strip()
        parsed: datetime | None = None
        if text and re.match(r"^\d{4}-\d{2}-\d{2}", text):
            try:
                parsed = datetime.fromisoformat(text[:10]).replace(tzinfo=UTC)
            except ValueError:
                parsed = None
        if parsed is None and row.created_at is not None:
            parsed = row.created_at
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        if parsed is not None:
            dates.append(parsed)
    if not dates:
        return None, None, len(rows)
    return min(dates), max(dates), len(rows)


def days_covering_saved_only(session: Session, *, fallback: int = DEFAULT_DAYS) -> int:
    """Widen search days to cover saved_only date span (bounded)."""
    lo, hi, _count = saved_only_date_span(session)
    if lo is None:
        return fallback
    now = datetime.now(UTC)
    span_days = max(1, int((now - lo).total_seconds() // 86400) + 14)
    # Bound: at least fallback, at most 5 years.
    return max(fallback, min(span_days, 365 * 5))


def build_search_params(
    *,
    days: int = DEFAULT_DAYS,
    query: str = DEFAULT_QUERY,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> GmailSearchParams:
    after = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    return GmailSearchParams(
        query=f"{query} after:{after}",
        days=days,
        after_unix=after,
        max_results=max_results,
        page_token_used=True,
    )


def match_meta_to_saved_only(
    meta: GmailMessageMeta,
    saved_rows: list[SimplifyHistoryRecord],
) -> tuple[SimplifyHistoryRecord | None, str]:
    """Return (row, confidence) where confidence is matched|low|none.

    Requires company signal. Role: strong token overlap → matched; when
    company_ok, allow company + generic engineer subject as low confidence.
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
        # Also allow company alias appearing in subject (ATS noreply domains).
        if not company_ok and subject:
            subj_norm = re.sub(r"[^a-z0-9]+", "", subject)
            for alias in aliases:
                if alias and len(alias) >= 4 and alias in subj_norm:
                    company_ok = True
                    break

        row_tokens = _role_tokens(row.role_title)
        overlap = row_tokens & role_hint_tokens
        role_ok = False
        if len(overlap) >= 2 or (
            len(overlap) == 1 and next(iter(overlap)) not in _GENERIC_ROLE_TOKENS
        ):
            role_ok = True

        if company_ok and role_ok:
            score = len(overlap) + 2
            cand = (row, "matched", score)
            if best is None or score > best[2]:
                best = cand
        elif company_ok:
            # Low confidence: company matched; optional generic engineer subject.
            generic_hit = bool(role_hint_tokens & _GENERIC_ROLE_TOKENS) or not role_hint_tokens
            score = 1 + (1 if generic_hit else 0)
            if best is None or (best[1] == "low" and score > best[2]):
                best = (row, "low", score)

    if best is None:
        return None, "none"
    return best[0], best[1]


def preview_gmail_matches(
    session: Session,
    *,
    messages: list[GmailMessageMeta] | None = None,
    days: int | None = None,
    persist: bool = False,
    query: str = DEFAULT_QUERY,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> GmailMatchPreview:
    """Preview (and optionally persist) Gmail confirmation matches.

    Default persist=False. Callers must print a preview before opting into
    persist; never persist when matches==0.
    """
    effective_days = days if days is not None else days_covering_saved_only(session)
    params = build_search_params(
        days=effective_days, query=query, max_results=max_results
    )
    preview = GmailMatchPreview(
        dry_run=not persist,
        search={
            "query": params.query,
            "days": params.days,
            "after_unix": params.after_unix,
            "max_results": params.max_results,
            "page_token_paging": params.page_token_used,
        },
    )
    if messages is None:
        if not gmail_ready():
            preview.setup_required = True
            preview.setup_message = oauth_setup_instructions()
            return preview
        try:
            messages = fetch_confirmation_messages(
                days=effective_days,
                query=query,
                max_results=max_results,
            )
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


def diagnose_gmail_preview(
    session: Session,
    *,
    messages: list[GmailMessageMeta] | None = None,
    days: int | None = None,
    query: str = DEFAULT_QUERY,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> GmailDiagnoseResult:
    """Aggregate-only Gmail diagnosis (domains + counts; no bodies/subjects)."""
    effective_days = days if days is not None else days_covering_saved_only(session)
    params = build_search_params(
        days=effective_days, query=query, max_results=max_results
    )
    lo, hi, saved_count = saved_only_date_span(session)
    result = GmailDiagnoseResult(
        search={
            "query": params.query,
            "days": params.days,
            "after_unix": params.after_unix,
            "max_results": params.max_results,
            "page_token_paging": params.page_token_used,
        },
        saved_only_count=saved_count,
        saved_only_date_span={
            "min": lo.isoformat() if lo else None,
            "max": hi.isoformat() if hi else None,
        },
        explanation=[
            "Prior 0 matches: narrow subject query + strict role overlap; "
            "company-only / generic-engineer subjects did not count as matched.",
            "Widened query adds common confirmation phrases and ATS noreply from: filters.",
            "Days auto-widen to cover saved_only date span when --days omitted.",
            "Paging uses Gmail list pageToken via list_next (maxResults per page).",
            "Output never includes email bodies or subjects — domains and counts only.",
        ],
    )
    if messages is None:
        if not gmail_ready():
            result.setup_required = True
            result.setup_message = oauth_setup_instructions()
            return result
        try:
            messages = fetch_confirmation_messages(
                days=effective_days,
                query=query,
                max_results=max_results,
            )
        except Exception as exc:
            result.setup_required = True
            result.setup_message = (
                f"Gmail fetch failed: {exc.__class__.__name__}. "
                + oauth_setup_instructions()
            )
            return result

    saved = (
        session.query(SimplifyHistoryRecord)
        .filter(SimplifyHistoryRecord.status_mapped == "saved_only")
        .all()
    )
    domain_counts: Counter[str] = Counter()
    confirm_by_domain: Counter[str] = Counter()
    result.scanned = len(messages)
    for meta in messages:
        domain = meta.sender_domain or "(unknown)"
        domain_counts[domain] += 1
        # subject_hint used internally only — never returned.
        if _CONFIRMATION_SUBJECT_RE.search(meta.subject_hint or ""):
            result.confirmation_looking += 1
            confirm_by_domain[domain] += 1
        row, conf = match_meta_to_saved_only(meta, saved)
        if row is None or conf == "none":
            result.unmatched_aggregate += 1
        elif conf == "low":
            result.low_confidence += 1
        else:
            result.matched += 1

    result.sender_domain_histogram = dict(domain_counts.most_common(40))
    result.confirmation_looking_by_domain = dict(confirm_by_domain.most_common(40))
    return result


def fetch_confirmation_messages(
    *,
    days: int = DEFAULT_DAYS,
    query: str = DEFAULT_QUERY,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[GmailMessageMeta]:
    """Live Gmail API fetch. Requires google-auth / google-api-python-client.

    Pages past the first ``max_results`` using pageToken via list_next.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]
        from googleapiclient.discovery import build  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "Install google-api-python-client google-auth-httplib2 "
            "google-auth-oauthlib to enable Gmail matching."
        ) from exc

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    creds = None
    if TOKEN_PATH.is_file():
        creds = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
            str(TOKEN_PATH), [GMAIL_SCOPE]
        )
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

            def _fqdn(name: str = "") -> str:
                return name or "localhost"

            def _hostbyaddr(addr: str) -> tuple[str, list[str], list[str]]:
                return addr, [], [addr]

            socket.getfqdn = _fqdn
            socket.gethostbyaddr = _hostbyaddr
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
    params = build_search_params(days=days, query=query, max_results=max_results)
    messages: list[GmailMessageMeta] = []
    request = (
        service.users()
        .messages()
        .list(userId="me", q=params.query, maxResults=params.max_results)
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
        # pageToken paging — list_next sets pageToken on the next request.
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
