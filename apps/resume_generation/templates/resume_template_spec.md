# Ganesha ATS Resume Template Spec

**Template version:** `ganesha-ats-v1`

## Purpose

Stable, Ganesha-owned, single-column ATS-safe layout. Generated from code via
`python-docx`. Source DOCX resumes are evidence only — their formatting is not
copied.

## Fonts

| Role | Family | Size |
|------|--------|------|
| Candidate name | Calibri | 16 pt bold |
| Contact line | Calibri | 9 pt |
| Section headings | Calibri | 11 pt bold, uppercase |
| Body / bullets | Calibri | 10 pt |

## Margins

- Top / bottom: 0.6 in
- Left / right: 0.7 in

## Section order

1. Name (centered)
2. Contact line (centered; only if present in verified corpus)
3. Summary (optional; conservative track+role phrasing only)
4. Skills
5. Experience
6. Projects (omitted or shortened under page budget)
7. Education

## Spacing

- Name → contact: ~2 pt after name
- Contact → first section: ~8 pt
- Section heading space-before: 8 pt; space-after: 2 pt
- Bullet space-after: 1 pt

## Bullet style

- Standard Word `List Bullet` (simple disc)
- No icons, emoji, or decorative Unicode

## One-page constraints

- Target exactly one PDF page after conversion
- Prefer experience over projects when content is long
- Cap approximate counts: skills ≤ 8, experience ≤ 10, projects ≤ 5, education ≤ 6
- Do not shrink fonts below 9 pt for contact or 10 pt for body to force fit

## ATS decisions

- Single column only
- No tables, text boxes, headers/footers with essential info
- No images, skill meters, multi-column layouts
- No keyword stuffing or hidden text
- Essential contact details only in body contact line from verified sources
- Template version recorded in document comments metadata and generation audit row
- Never invent contact details, credentials, dates, metrics, or employers
