from __future__ import annotations

from apps.cover_letter_generation.services.cover_letter_composer_service import (
    resume_bullet_overlap,
)


def test_overlap_detector_flags_verbatim_bullets() -> None:
    bullet = "Built REST APIs in Python serving internal tooling for the platform team."
    assert resume_bullet_overlap(["A different explanation of API work."], [bullet]) == []
    copied = [f"The resume says: {bullet}"]
    assert resume_bullet_overlap(copied, [bullet]) == [bullet]
