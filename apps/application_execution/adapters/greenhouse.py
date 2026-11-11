"""Greenhouse job-application form adapter (narrow, label/name based)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from apps.application_execution.services.approved_profile import ApprovedProfile
from apps.application_execution.services.browser_executor import FillFieldResult
from apps.application_execution.services.submit_guard import (
    SubmitGuardError,
    assert_not_submit_action,
    looks_like_submit_action,
)

PLATFORM = "greenhouse"

SENSITIVE_LABEL_PATTERNS = (
    re.compile(r"work\s+authori[sz]ation|legally\s+authori[sz]ed", re.I),
    re.compile(r"sponsor(ship)?|visa\b|h-?1b|require.*sponsor", re.I),
    re.compile(r"citizen(ship)?", re.I),
    re.compile(r"clearance|security\s+clearance", re.I),
    re.compile(r"salary|compensation|pay\s+expect", re.I),
    re.compile(r"relocat", re.I),
    re.compile(r"start\s+date|available\s+to\s+start|availability", re.I),
    re.compile(r"gender|race|ethnicity|veteran|disability|demographic", re.I),
    re.compile(r"\bconsent\b|terms\s+and\s+conditions|privacy\s+policy", re.I),
    re.compile(r"certify|acknowledge|legal\s+declaration|i\s+agree", re.I),
)


@dataclass(frozen=True)
class FieldMapping:
    key: str
    labels: tuple[str, ...]
    name_attrs: tuple[str, ...]
    profile_attr: str
    kind: str = "text"  # text | file_resume | file_cover | textarea


FIELD_MAPPINGS: tuple[FieldMapping, ...] = (
    FieldMapping(
        "first_name",
        ("first name", "given name"),
        ("job_application[first_name]", "first_name", "firstName"),
        "first_name",
    ),
    FieldMapping(
        "last_name",
        ("last name", "family name", "surname"),
        ("job_application[last_name]", "last_name", "lastName"),
        "last_name",
    ),
    FieldMapping(
        "email",
        ("email", "e-mail", "email address"),
        ("job_application[email]", "email", "email_address"),
        "email",
    ),
    FieldMapping(
        "phone",
        ("phone", "phone number", "mobile", "telephone"),
        ("job_application[phone]", "phone", "phone_number"),
        "phone",
    ),
    FieldMapping(
        "location",
        ("current location", "location", "city", "where are you based"),
        ("job_application[location]", "location", "city"),
        "location",
    ),
    FieldMapping(
        "linkedin",
        ("linkedin", "linkedin url", "linkedin profile"),
        ("job_application[urls][linkedin]", "linkedin_url", "linkedin"),
        "linkedin_url",
    ),
    FieldMapping(
        "github",
        ("github", "github url", "github profile"),
        ("job_application[urls][github]", "github_url", "github"),
        "github_url",
    ),
    FieldMapping(
        "portfolio",
        ("portfolio", "website", "personal website", "portfolio url"),
        ("job_application[urls][portfolio]", "portfolio_url", "website"),
        "portfolio_url",
    ),
    FieldMapping(
        "resume",
        ("resume/cv", "attach resume", "resume", "cv"),
        ("job_application[resume]", "resume", "resume_file"),
        "",
        "file_resume",
    ),
    FieldMapping(
        "cover_letter",
        ("cover letter", "cover-letter", "coverletter"),
        ("job_application[cover_letter]", "cover_letter", "coverLetter"),
        "",
        "file_cover",
    ),
)


def is_greenhouse_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "greenhouse.io" in host or "greenhouse.com" in host


def detect_blocker_text(page_text: str) -> list[str]:
    text = (page_text or "").lower()
    blockers: list[str] = []
    if "captcha" in text or "recaptcha" in text:
        blockers.append("captcha")
    if "verify you are human" in text:
        blockers.append("anti_bot")
    if "sign in" in text or "log in" in text or "create an account" in text:
        # Only treat as blocker when login appears primary; still report.
        blockers.append("login_or_account_wall")
    if "multi-factor" in text or "two-factor" in text or "enter the code" in text:
        blockers.append("mfa")
    return blockers


def label_is_sensitive(label: str) -> bool:
    return any(p.search(label or "") for p in SENSITIVE_LABEL_PATTERNS)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _locator_for_mapping(page: Any, mapping: FieldMapping) -> Any | None:
    # Modern Greenhouse job-boards use stable element ids with empty name attrs.
    for name in mapping.name_attrs:
        by_id = page.locator(f"#{name}")
        if by_id.count() > 0:
            if mapping.kind.startswith("file"):
                file_input = page.locator(f"#{name}").locator(
                    "xpath=self::input[@type='file'] | descendant::input[@type='file']"
                )
                if file_input.count() == 0 and name == "resume":
                    file_input = page.locator("input#resume[type='file'], input[type='file']")
                if file_input.count() > 0:
                    return file_input.first
            return by_id.first
        loc = page.locator(
            f'input[name="{name}"], textarea[name="{name}"], select[name="{name}"]'
        )
        if loc.count() > 0:
            return loc.first
    if mapping.kind.startswith("file"):
        # Prefer concrete file inputs; avoid aria-labelled group containers.
        file_inputs = page.locator("input[type='file']")
        if file_inputs.count() > 0:
            return file_inputs.first
    for label in mapping.labels:
        label_el = page.locator("label").filter(has_text=re.compile(label, re.I))
        if label_el.count() > 0:
            for_attr = label_el.first.get_attribute("for")
            if for_attr:
                named = page.locator(f"#{for_attr}")
                if named.count() > 0:
                    if mapping.kind.startswith("file"):
                        file_input = page.locator(
                            f"#{for_attr}[type='file'], #{for_attr} input[type='file'], input#{for_attr}"
                        )
                        if file_input.count() > 0:
                            return file_input.first
                    return named.first
            nested = label_el.first.locator("input, textarea, select")
            if nested.count() > 0:
                return nested.first
        # get_by_label last: on Greenhouse boards it can resolve to non-input groups.
        if not mapping.kind.startswith("file"):
            by_label = page.get_by_label(re.compile(label, re.I), exact=False)
            if by_label.count() > 0:
                tag = ""
                try:
                    tag = (by_label.first.evaluate("el => el.tagName") or "").lower()
                except Exception:
                    tag = ""
                if tag in {"input", "textarea", "select"}:
                    return by_label.first
    return None


def _current_value(locator: Any) -> str:
    try:
        tag = (locator.evaluate("el => el.tagName") or "").lower()
        if tag == "input":
            input_type = (locator.get_attribute("type") or "text").lower()
            if input_type == "file":
                files = locator.evaluate("el => el.files && el.files.length")
                return "file" if files else ""
            return locator.input_value() or ""
        if tag == "textarea":
            return locator.input_value() or ""
        if tag == "select":
            return locator.input_value() or ""
    except Exception:
        return ""
    return ""


def fill_greenhouse_form(
    page: Any,
    *,
    profile: ApprovedProfile,
    resume_path: Path,
    cover_letter_path: Path | None,
    include_cover_letter: bool,
) -> tuple[list[FillFieldResult], list[dict[str, str]], bool, bool]:
    """Fill supported Greenhouse fields. Never clicks Submit."""
    results: list[FillFieldResult] = []
    unresolved: list[dict[str, str]] = []
    resume_uploaded = False
    cover_uploaded = False

    # Scan unlabeled/sensitive-looking questions for reporting.
    labels = page.locator("label")
    for i in range(min(labels.count(), 80)):
        text = (labels.nth(i).inner_text() or "").strip()
        if not text:
            continue
        if label_is_sensitive(text):
            unresolved.append(
                {
                    "kind": "sensitive_question",
                    "label": text[:120],
                    "action": "answer_manually_on_employer_form",
                }
            )

    for mapping in FIELD_MAPPINGS:
        locator = _locator_for_mapping(page, mapping)
        label = mapping.labels[0]
        if locator is None:
            if mapping.kind.startswith("file"):
                continue
            results.append(
                FillFieldResult(
                    mapping.key, label, "unsupported", "No confident selector"
                )
            )
            continue

        if mapping.kind == "file_resume":
            if not resume_path.is_file():
                results.append(
                    FillFieldResult(
                        mapping.key, label, "missing_value", "Resume path missing"
                    )
                )
                continue
            # Never attach Desktop-only generic paths as a fallback (caller must pass job-specific).
            if resume_path.parent.name == "Desktop":
                results.append(
                    FillFieldResult(
                        mapping.key,
                        label,
                        "unsupported",
                        "Refused Desktop generic PDF",
                    )
                )
                continue
            existing = _current_value(locator)
            if existing:
                results.append(
                    FillFieldResult(
                        mapping.key, label, "skipped_existing", "File already attached"
                    )
                )
                continue
            locator.set_input_files(str(resume_path))
            resume_uploaded = True
            results.append(FillFieldResult(mapping.key, label, "filled", resume_path.name))
            continue

        if mapping.kind == "file_cover":
            if not include_cover_letter or cover_letter_path is None:
                results.append(
                    FillFieldResult(
                        mapping.key, label, "unsupported", "Cover letter not selected"
                    )
                )
                continue
            if not cover_letter_path.is_file():
                results.append(
                    FillFieldResult(
                        mapping.key, label, "missing_value", "Cover path missing"
                    )
                )
                continue
            existing = _current_value(locator)
            if existing:
                results.append(
                    FillFieldResult(
                        mapping.key, label, "skipped_existing", "File already attached"
                    )
                )
                continue
            locator.set_input_files(str(cover_letter_path))
            cover_uploaded = True
            results.append(
                FillFieldResult(mapping.key, label, "filled", cover_letter_path.name)
            )
            continue

        value = getattr(profile, mapping.profile_attr, None)
        if not value:
            results.append(
                FillFieldResult(
                    mapping.key, label, "missing_value", "No approved profile value"
                )
            )
            continue
        existing = (_current_value(locator) or "").strip()
        if existing:
            results.append(
                FillFieldResult(
                    mapping.key,
                    label,
                    "skipped_existing",
                    "Left user/existing value untouched",
                )
            )
            continue
        locator.fill(str(value))
        verified = (_current_value(locator) or "").strip()
        if verified != str(value).strip():
            results.append(
                FillFieldResult(
                    mapping.key,
                    label,
                    "unsupported",
                    "Fill verification mismatch; left for review",
                )
            )
            continue
        results.append(FillFieldResult(mapping.key, label, "filled", None))

    unresolved.append(
        {
            "kind": "final_submission",
            "action": "user_reviews_and_submits_manually",
        }
    )
    return results, unresolved, resume_uploaded, cover_uploaded


def guard_page_against_submit_clicks(page: Any) -> None:
    """Attach a click interceptor that blocks submit-like controls."""

    def _on_click(payload: dict[str, Any]) -> None:
        label = payload.get("label") or payload.get("text") or ""
        assert_not_submit_action(str(label), context="page click")

    # Best-effort: evaluate click denylist in page for native buttons.
    page.expose_binding(
        "_ganeshaAssertNotSubmit",
        lambda source, label: assert_not_submit_action(str(label)),
    )
    page.add_init_script(
        """
        (() => {
          const suspicious = (t) => {
            const s = (t || '').trim().toLowerCase();
            return (
              s === 'submit' ||
              s.includes('submit application') ||
              s.includes('apply now') ||
              s === 'apply'
            );
          };
          document.addEventListener('click', (ev) => {
            const el = ev.target;
            if (!el) return;
            const text = (el.innerText || el.value || el.getAttribute('aria-label') || '');
            if (suspicious(text)) {
              ev.preventDefault();
              ev.stopPropagation();
              if (window._ganeshaAssertNotSubmit) {
                window._ganeshaAssertNotSubmit(text);
              }
            }
          }, true);
        })();
        """
    )
    # Silence unused in sync paths; keep callable for tests.
    _ = _on_click
    _ = looks_like_submit_action
    _ = SubmitGuardError
