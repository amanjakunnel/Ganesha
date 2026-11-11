"""Local Playwright executor using a dedicated gitignored browser profile."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apps.application_execution.adapters.greenhouse import (
    PLATFORM,
    detect_blocker_text,
    fill_greenhouse_form,
    is_greenhouse_url,
)
from apps.application_execution.services.approved_profile import ApprovedProfile
from apps.application_execution.services.browser_executor import (
    AutofillRequest,
    AutofillResult,
)
from apps.application_execution.services.submit_guard import SubmitGuardError
from apps.application_execution.services.url_safety import is_safe_http_url

REPO_ROOT = Path(__file__).resolve().parents[3]
BROWSER_PROFILE_DIR = REPO_ROOT / "data" / "browser_profiles" / "controlled_chromium"

# Keep Chromium open for manual review after Mode B fills (local prototype only).
_OPEN_SESSIONS: list[Any] = []


class PlaywrightGreenhouseExecutor:
    """Mode B executor for Greenhouse only."""

    def __init__(self, *, profile_dir: Path | None = None) -> None:
        self.profile_dir = profile_dir or BROWSER_PROFILE_DIR

    def fill_application(self, request: AutofillRequest) -> AutofillResult:
        url = request.application_url
        if not is_safe_http_url(url):
            return AutofillResult(
                ok=False,
                platform=PLATFORM,
                application_url=url,
                safe_error="Unsafe or missing application URL",
                submitted=False,
            )
        if not is_greenhouse_url(url):
            return AutofillResult(
                ok=False,
                platform="unsupported",
                application_url=url,
                safe_error="Controlled autofill currently supports Greenhouse only",
                submitted=False,
            )
        if not isinstance(request.profile, ApprovedProfile):
            return AutofillResult(
                ok=False,
                platform=PLATFORM,
                application_url=url,
                safe_error="Approved profile missing",
                submitted=False,
            )

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return AutofillResult(
                ok=False,
                platform=PLATFORM,
                application_url=url,
                safe_error="Playwright is not installed",
                submitted=False,
            )

        filled = []
        unresolved: list[dict[str, str]] = []
        blockers: list[str] = []
        resume_uploaded = False
        cover_uploaded = False
        paused = False

        playwright = sync_playwright().start()
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=request.headless,
                accept_downloads=False,
                args=["--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(800)
            # Newer Greenhouse boards sometimes hydrate the application form late.
            try:
                page.wait_for_selector(
                    'input[name*="first_name"], input[name*="email"], '
                    'label:has-text("First Name"), label:has-text("Email")',
                    timeout=8000,
                )
            except Exception:
                pass
            # Optional Apply control that reveals the form (never Submit).
            for label in ("Apply for this job", "Apply"):
                btn = page.get_by_role("button", name=label)
                link = page.get_by_role("link", name=label)
                target = btn if btn.count() else link
                if target.count():
                    text = (target.first.inner_text() or label).strip()
                    from apps.application_execution.services.submit_guard import (
                        looks_like_submit_action,
                    )

                    if looks_like_submit_action(text) and "apply for this job" not in text.lower():
                        continue
                    try:
                        target.first.click(timeout=3000)
                        page.wait_for_timeout(800)
                    except Exception:
                        pass
                    break
            body_text = ""
            try:
                body_text = page.inner_text("body")
            except Exception:
                body_text = ""
            blockers = detect_blocker_text(body_text)
            hard = {"captcha", "mfa", "anti_bot"}
            if hard.intersection(blockers):
                paused = True
                unresolved.append(
                    {
                        "kind": "browser_blocker",
                        "action": "user_intervention_required",
                        "detail": ",".join(sorted(hard.intersection(blockers))),
                    }
                )
            else:
                filled, unresolved, resume_uploaded, cover_uploaded = fill_greenhouse_form(
                    page,
                    profile=request.profile,
                    resume_path=request.resume_path,
                    cover_letter_path=request.cover_letter_path,
                    include_cover_letter=request.include_cover_letter,
                )
                paused = True

            if request.keep_open and not request.headless:
                _OPEN_SESSIONS.append((playwright, context))
            else:
                context.close()
                playwright.stop()

            return AutofillResult(
                ok=True,
                platform=PLATFORM,
                application_url=url,
                filled=filled,
                unresolved=unresolved,
                blockers=blockers,
                resume_uploaded=resume_uploaded,
                cover_uploaded=cover_uploaded,
                paused_for_review=paused,
                submitted=False,
            )
        except SubmitGuardError as exc:
            try:
                playwright.stop()
            except Exception:
                pass
            return AutofillResult(
                ok=False,
                platform=PLATFORM,
                application_url=url,
                filled=filled,
                unresolved=unresolved,
                blockers=blockers,
                safe_error=str(exc),
                submitted=False,
            )
        except Exception as exc:
            try:
                playwright.stop()
            except Exception:
                pass
            return AutofillResult(
                ok=False,
                platform=PLATFORM,
                application_url=url,
                filled=filled,
                unresolved=unresolved,
                blockers=blockers,
                safe_error=f"Controlled browser failed: {type(exc).__name__}",
                submitted=False,
            )


def close_open_sessions() -> None:
    """Close any review browsers kept open by this process."""
    while _OPEN_SESSIONS:
        playwright, context = _OPEN_SESSIONS.pop()
        try:
            context.close()
        except Exception:
            pass
        try:
            playwright.stop()
        except Exception:
            pass
