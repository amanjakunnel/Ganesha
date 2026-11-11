# Assisted application execution

Local-first prototype for preparing an application package and handing off to the
user's Chrome (with optional Simplify Copilot). Ganesha does **not** submit forms.

## Modes

### A. Chrome + Simplify assisted handoff (implemented)

1. Prepare an attempt with job-specific resume (optional cover letter).
2. Open the prepared application URL in the user's Google Chrome.
3. User may invoke Simplify autofill manually.
4. User reviews, attaches the selected documents, and submits on the employer site.
5. User returns to Ganesha and confirms submission (or marks needs action / cancel).

Ganesha cannot observe Simplify, cannot control the extension, and never clicks
Submit. Opening Chrome is not submission.

### B. Ganesha-controlled browser (not implemented in this milestone)

Would use a dedicated browser profile (never the user's everyday Chrome profile),
fill a narrow supported form family, and pause before submit. Requires Playwright
(or similar) as an explicit dependency — intentionally not added for architectural
completeness alone.

## Cloud migration boundaries (not deployed)

### Cloud backend + local user browser

- Cloud hosts jobs, dashboard, documents, and attempt tracking.
- Artifact storage stays behind path/ID references; local execution resolves files.
- User continues applying via local Chrome/Simplify handoff.
- Word PDF conversion remains a **local** renderer until replaced deliberately.

### Cloud backend + remote controlled browser

Needs a separate execution environment, secure sessions, artifact transfer into
that environment, and an explicit review UI. Installing Simplify on a laptop does
**not** make it available inside a remote browser.

Keep browser execution out of dashboard request handlers and out of business-logic
services that prepare packages.

## Sensitive answers

Work authorization, sponsorship, citizenship, clearance, salary, relocation,
start date, demographic/disability disclosures, legal declarations, and consent
are never inferred from eligibility decisions and are never stored as global
defaults by this workflow.
