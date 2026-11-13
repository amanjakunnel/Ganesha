# Gmail readonly application-confirmation matching

## Scope

- OAuth scope: **`gmail.readonly` only**
- Read confirmation-style messages (thank-you / application-received / thanks-for-your-interest, plus optional Greenhouse/Lever/Ashby noreply `from:` filters)
- Bounded date range (default covers `saved_only` span, at least 365 days), rate-limited, `maxResults=100` with `pageToken` paging
- **Never** send, label, draft, or modify mail

## CLI

```bash
python -m apps.cli.main jobs gmail-diagnose
python -m apps.cli.main jobs gmail-preview --days 400
# Persist only after preview, and only when matches>0:
python -m apps.cli.main jobs gmail-preview --persist
```

Diagnose/preview print aggregates only (sender-domain histogram, counts). No bodies/subjects.

## What is stored

Only: message ID, received date, sender domain, matched company, matched role, confidence.
**Not stored:** email bodies, full subjects beyond a transient match hint, attachments, personal content.

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
