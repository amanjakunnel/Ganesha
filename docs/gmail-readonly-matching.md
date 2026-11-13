# Gmail readonly application-confirmation matching

## Scope

- OAuth scope: **`gmail.readonly` only**
- Read confirmation-style messages (thank-you / application-received / your-application-to, plus Greenhouse / Lever / Ashby / Workday / Amazon Jobs `from:` filters)
- **Employer is not the ATS domain.** Transiently parse company (and role when present) from From display name + Subject (`Thank you for applying to X`, `Your application to X`, `Application received - Role at X`, etc.)
- Match against `saved_only` by extracted company (+ role tokens when present)
- Bounded date range (auto-widens to cover `saved_only` span, capped), rate-limited, `maxResults=200` with `pageToken` paging
- **Never** send, label, draft, or modify mail
- Preview/diagnose default; persist only after operator review

## CLI

```bash
python -m apps.cli.main jobs gmail-diagnose
python -m apps.cli.main jobs gmail-preview --days 400
# Persist only after preview, and only when matches>0:
python -m apps.cli.main jobs gmail-preview --persist
```

Diagnose/preview print aggregates only (sender-domain histogram, counts). No bodies/subjects.

## Matching note (ATS vs employer)

Confirmation mail often comes from **ATS domains** (Greenhouse, Lever, Ashby,
Workday, Amazon Jobs), not the employer domain. Ganesha parses the employer
**transiently** from the sender **display name** and **subject** patterns such as:

- "Thank you for applying to X"
- "Your application to X"
- "Application received - Role at X"

Only the match result is stored (below). Display names and subjects are not persisted.

## What is stored

Only: message ID, received date, sender domain, matched company, matched role, confidence.
**Not stored:** email bodies, full subjects beyond a transient match hint, display names,
attachments, personal content.

Dashboard shows a notice when the Gmail token is expired or invalid (re-consent via
Likely applied → Gmail preview, or revoke + delete `token.json`).

## Paths (outside the repo)

- Client secrets: `~/.ganesha/gmail/client_secret.json`
- Token: `~/.ganesha/gmail/token.json`

Never commit these. Never log tokens.

## Setup (required before first live run)

1. Google Cloud Console → create OAuth client (Desktop app) with Gmail API enabled.
2. Download JSON to `~/.ganesha/gmail/client_secret.json` (`mkdir -p ~/.ganesha/gmail && chmod 700 ~/.ganesha/gmail`).
3. From the dashboard Likely applied page, run **Gmail preview**. A local consent window opens once.
4. Preview shows scanned / matched / low-confidence counts **before** anything is written.
5. Persist evidence only via the apply endpoint; promotions still require your confirmation on the review list.

If the client secret is missing, Ganesha stops and shows these steps — it does not work around OAuth.

## Revoke

1. https://myaccount.google.com/permissions — remove the app
2. Delete `~/.ganesha/gmail/token.json`
