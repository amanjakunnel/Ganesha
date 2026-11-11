from __future__ import annotations

from pathlib import Path

from packages.core.services.answer_bank import (
    add_entry,
    lookup_answer,
    resolve_for_autofill,
)


def test_sensitive_requires_approved_entry(tmp_path: Path) -> None:
    path = tmp_path / "answer_bank.json"
    unresolved = resolve_for_autofill(
        "Are you authorized to work in the US?", path=path
    )
    assert unresolved["status"] == "needs_input"
    assert unresolved["sensitive"] is True
    assert unresolved["offer_add_to_bank"] is True

    add_entry(
        question_pattern=r"authorized to work",
        answer="Yes",
        scope="global",
        path=path,
    )
    resolved = resolve_for_autofill(
        "Are you authorized to work in the US?", path=path
    )
    assert resolved["status"] == "filled_from_bank"
    assert resolved["answer"] == "Yes"


def test_company_scope_preferred(tmp_path: Path) -> None:
    path = tmp_path / "answer_bank.json"
    add_entry(
        question_pattern=r"sponsor",
        answer="No global",
        scope="global",
        path=path,
    )
    add_entry(
        question_pattern=r"sponsor",
        answer="No for Acme",
        scope="company",
        company="Acme",
        path=path,
    )
    hit = lookup_answer("Will you need sponsorship?", company="Acme", path=path)
    assert hit is not None
    assert hit.answer == "No for Acme"
