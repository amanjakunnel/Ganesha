"""Curated target employers for job-search prioritization."""

from __future__ import annotations

# Canonical display names; matching uses normalized keys (see company_normalize).
TARGET_COMPANY_NAMES: tuple[str, ...] = (
    "Meta",
    "Apple",
    "Amazon",
    "Netflix",
    "Google",
    "Microsoft",
    "Nvidia",
    "Tesla",
    "OpenAI",
    "Anthropic",
    "Databricks",
    "Snowflake",
    "Stripe",
    "Uber",
    "Airbnb",
    "LinkedIn",
    "TikTok",
    "Pinterest",
    "Reddit",
    "Figma",
    "Dropbox",
    "GitLab",
    "Notion",
    "Canva",
    "Discord",
    "Twitch",
    "Booking.com",
    "Cloudflare",
    "Palantir",
    "DoorDash",
)

# Aliases map normalized keys -> canonical target name for explanations.
TARGET_COMPANY_ALIASES: dict[str, str] = {
    "amazon web services": "Amazon",
    "aws": "Amazon",
    "alphabet": "Google",
    "google llc": "Google",
    "meta platforms": "Meta",
    "facebook": "Meta",
    "microsoft corporation": "Microsoft",
    "nvidia corporation": "Nvidia",
    "bytedance": "TikTok",
}
