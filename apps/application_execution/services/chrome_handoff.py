"""Mode A: open the employer application in the user's Google Chrome.

This is a handoff, not a Simplify integration. Ganesha does not control the
extension, observe form completion, or click submit.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass

from apps.application_execution.services.url_safety import (
    is_safe_http_url,
    same_application_target,
)


@dataclass
class OpenResult:
    ok: bool
    detail: str
    url: str | None = None


SIMPLIFY_HANDOFF_INSTRUCTIONS = [
    "When you choose Open, Ganesha launches the employer page in Google Chrome "
    "(your normal profile).",
    "If Simplify Copilot is installed, invoke its autofill yourself — Ganesha cannot "
    "detect or control it.",
    "Attach the job-specific resume (and optional cover letter) selected in Ganesha; "
    "do not use a generic Desktop PDF.",
    "Review every sensitive answer yourself before submitting.",
    "Ganesha will not click Submit. After you submit, return here and confirm.",
]


def open_application_in_chrome(*, intended_url: str, requested_url: str | None = None) -> OpenResult:
    target = (requested_url or intended_url).strip()
    if not is_safe_http_url(target):
        return OpenResult(ok=False, detail="Application URL is missing or not a safe http(s) URL.")
    if not same_application_target(intended_url, target):
        return OpenResult(
            ok=False,
            detail="Refusing to open a URL that differs from the prepared application destination.",
        )
    system = platform.system()
    try:
        if system == "Darwin":
            chrome = shutil.which("open")
            if not chrome:
                return OpenResult(ok=False, detail="macOS open command unavailable.")
            result = subprocess.run(
                ["open", "-a", "Google Chrome", target],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if result.returncode != 0:
                return OpenResult(
                    ok=False,
                    detail=(result.stderr or result.stdout or "Chrome open failed").strip(),
                    url=target,
                )
            return OpenResult(ok=True, detail="Opened in Google Chrome.", url=target)
        if system == "Linux":
            chrome_bin = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which(
                "chromium-browser"
            )
            if not chrome_bin:
                return OpenResult(ok=False, detail="Google Chrome/Chromium not found on PATH.")
            subprocess.Popen([chrome_bin, target], start_new_session=True)  # noqa: S603
            return OpenResult(ok=True, detail="Opened in Chrome/Chromium.", url=target)
        return OpenResult(
            ok=False,
            detail=f"Chrome handoff is not implemented for platform {system}.",
            url=target,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return OpenResult(ok=False, detail=f"Failed to open Chrome: {exc}", url=target)


def reveal_local_path(path: str) -> OpenResult:
    """Reveal a local document in Finder/file manager (local execution layer only)."""
    from pathlib import Path

    target = Path(path)
    if not target.exists():
        return OpenResult(ok=False, detail="Document path does not exist on this machine.")
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(
                ["open", "-R", str(target)],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            return OpenResult(ok=True, detail="Revealed in Finder.", url=str(target))
        if system == "Linux":
            folder = str(target.parent)
            opener = shutil.which("xdg-open")
            if not opener:
                return OpenResult(ok=False, detail="xdg-open unavailable.")
            subprocess.Popen([opener, folder], start_new_session=True)  # noqa: S603
            return OpenResult(ok=True, detail="Opened containing folder.", url=folder)
        return OpenResult(ok=False, detail=f"Reveal not implemented for {system}.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return OpenResult(ok=False, detail=str(exc))
