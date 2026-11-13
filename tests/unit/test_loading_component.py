"""Lightweight checks for shared loading CSS/JS component."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "apps" / "dashboard" / "static" / "dashboard.css"
JS = ROOT / "apps" / "dashboard" / "static" / "dashboard.js"


def test_loading_css_has_ellipsis_and_reduced_motion() -> None:
    text = CSS.read_text(encoding="utf-8")
    assert ".loading-status" in text
    assert ".loading-ellipsis" in text
    assert "prefers-reduced-motion" in text
    assert "reduced-motion" in text
    assert "ganesha-ellipsis-steps" in text


def test_loading_js_exports_ganesha_loading() -> None:
    text = JS.read_text(encoding="utf-8")
    assert "window.GaneshaLoading" in text
    assert "aria-live" in text
    assert "reduced-motion" in text
    assert "function start(" in text or "start(el, label" in text
    assert "succeed(" in text
    assert "fail(" in text
