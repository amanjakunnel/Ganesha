# Resume generation (local ATS)

## Resume source corpus

```bash
python -m apps.cli.main resumes scan
```

Optional flags: `--path`, `--dry-run`, `--rebuild`, `--verbose`.

Source directory:

```text
/Users/amanjakunnel/Documents/Resumés
```

Rules:

- Only DOCX files are read.
- PDFs and Microsoft Office temporary `~$` files are ignored.
- Source files are never modified or renamed.
- Company-specific resumes under `Company Specific/` are supporting evidence and
  prior wording examples — not automatic canonical truth.
- Facts are stored in the local Ganesha database with provenance.

## Resume diagnostics

```bash
python -m apps.cli.main resumes doctor
```

Reports source folder access, `python-docx`, Microsoft Word / LibreOffice
availability, selected PDF engine, PDF validator, output paths, and semantic
provider configuration state (never prints secret values).

## Generate from CLI

```bash
python -m apps.cli.main resumes generate <job-id>
```

Safe flags:

- `--track dev|cloud|ml|csharp|general`
- `--deterministic-only`
- `--dry-run`
- `--no-desktop-copy`

The dashboard **Actions → Generate tailored resume** control uses the same
generation service. Resume generation is never triggered by feed sync or job
scoring.

## Optional semantic provider

```env
SEMANTIC_FIT_PROVIDER=anthropic
ANTHROPIC_MODEL=claude-haiku-5-5
ANTHROPIC_API_KEY=...
```

Notes:

- The API key is optional. Deterministic strategy works without it.
- ChatGPT Plus, Perplexity Pro, and Claude web subscriptions are not API credentials.
- Never commit `.env`.
- Do not paste API keys into source code, Cursor prompts, issues, or chat.
- Default is `SEMANTIC_FIT_PROVIDER=none`.

## Outputs

Internal artifacts (timestamped; prior runs preserved):

```text
<data/generated_resumes>/<job-id>/<timestamp>/Alex_Michael_Manjakunnel_Resume.docx
<data/generated_resumes>/<job-id>/<timestamp>/Alex_Michael_Manjakunnel_Resume.pdf
```

Desktop copy (generic name; overwritten only after one-page validation):

```text
/Users/amanjakunnel/Desktop/Alex_Michael_Manjakunnel_Resume.pdf
```

Failed validations are kept under `data/generated_resumes/_failed/` and are not
copied to Desktop.

## Truthfulness

Generated bullets must come from scanned DOCX evidence or verified profile facts.
The generator may reorder and rephrase conservatively; it must not invent
credentials, employers, dates, metrics, clearances, or tools absent from sources.

## Quality gates

- Jobs excluded by eligibility policy (e.g. clearance/citizenship requirements)
  cannot generate resumes. The dashboard Actions menu still opens, but generation
  is blocked with a neutral policy message (no sensitive wording in the feed).
- Conflicting GPA values are detected during `resumes scan`, stored as conflicts,
  and omitted from generated output until resolved.
- Output is a structured document: Skills (one section), Experience roles with
  company/title/dates, named Projects, and canonical Education entries. Orphan
  bullets without parent context are rejected.
