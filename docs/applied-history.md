# Applied-history reconciliation

## Status precedence

When the same posting appears with multiple Simplify statuses, **the most advanced status wins**.

Order (low → high): `unknown` < `saved_only` < `applied` < `ghosted`/`rejected`/`withdrawn` < `screen` < `interview` < `offer` < `accepted`.

**APPLIED is never downgraded by a later SAVED row.** Re-import is idempotent via `source_import_key`.

Shared Greenhouse embed URLs (`boards.greenhouse.io/embed/...`) are **ignored**: not merged, not counted as applied, dropped from reports.

## Likely applied (needs confirmation)

Saved-only rows enter the review list when any evidence is present:

1. **Applied Date** field populated
2. **Matching APPLIED record** (same canonical URL)
3. **Notes phrase** (conservative regexes documented in `NOTES_APPLIED_PHRASES`)
4. **Gmail confirmation** match (opt-in, readonly)

Never auto-promoted. Confirm → `status_mapped=applied` with provenance `user_confirmation_of_simplify_evidence`. Reject → stays saved-only and is not re-suggested.

Dashboard: `/likely-applied`

## Gmail readonly matching

See `docs/gmail-readonly-matching.md`.
