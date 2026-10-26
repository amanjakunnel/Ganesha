from __future__ import annotations

import re
import shutil
from collections import Counter
from pathlib import Path

from pypdf import PdfReader

from apps.resume_generation.schemas import ResumeDocument, ValidationReport
from apps.resume_generation.services.docx_render_service import extract_docx_text
from apps.resume_generation.services.resume_corpus_service import normalize_text

FORBIDDEN_SNIPPETS = (
    "prompt version",
    "semantic provider",
    "anthropic",
    "api_key",
    "work authorization",
    "cannot generate",
    "internal score",
    "source_document",
    "fact_id",
    "ganesha debug",
    "eligibility policy",
    "f-1",
    "ts/sci",
)


def pdf_page_count(path: Path) -> int:
    reader = PdfReader(str(path))
    return len(reader.pages)


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def is_valid_pdf(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 64:
        return False
    try:
        head = path.read_bytes()[:5]
        if head != b"%PDF-":
            return False
        PdfReader(str(path))
        return True
    except Exception:
        return False


def _count_heading(text: str, heading: str) -> int:
    pattern = re.compile(rf"^{re.escape(heading)}\s*$", re.IGNORECASE | re.MULTILINE)
    return len(pattern.findall(text))


def _structural_checks(docx_text: str, document: ResumeDocument | None) -> list[str]:
    messages: list[str] = []
    for heading in ("SKILLS", "EXPERIENCE", "EDUCATION"):
        count = _count_heading(docx_text, heading)
        if count != 1:
            messages.append(f"Expected exactly one {heading} heading, found {count}")
    if document and document.projects:
        count = _count_heading(docx_text, "PROJECTS")
        if count != 1:
            messages.append(f"Expected exactly one PROJECTS heading, found {count}")

    lines = [ln.strip() for ln in docx_text.splitlines() if ln.strip()]
    # Detect orphan bullets: bullet-like line before any experience header content
    # Simple duplicate line detection
    norms = [normalize_text(ln) for ln in lines if len(ln) > 20]
    dupes = [n for n, c in Counter(norms).items() if c > 1]
    if dupes:
        messages.append(f"Duplicate normalized lines detected ({len(dupes)})")

    # One GPA line per school. Extra or mismatched values are still a conflict.
    rendered = re.findall(r"GPA[:\s]*([0-9]\.[0-9])", docx_text, flags=re.I)
    if document is not None:
        expected = [ed.gpa for ed in document.education if ed.gpa]
        if sorted(rendered) != sorted(expected):
            shown = ", ".join(sorted(set(rendered))) or "none"
            wanted = ", ".join(sorted(set(expected))) or "none"
            messages.append(f"GPA lines do not match education entries: {shown} vs {wanted}")
    elif len(set(rendered)) > 1:
        messages.append(f"Multiple GPA values rendered: {', '.join(sorted(set(rendered)))}")

    if document:
        for exp in document.experience:
            if exp.company and exp.company not in docx_text:
                messages.append(f"Missing experience company in DOCX: {exp.company}")
        for proj in document.projects:
            if proj.name and proj.name not in docx_text:
                messages.append(f"Missing project name in DOCX: {proj.name}")

    # Skill duplication across skill lines
    if document:
        skill_tokens: list[str] = []
        for line in document.skill_lines:
            if ":" in line:
                for tok in line.split(":", 1)[1].split(","):
                    skill_tokens.append(normalize_text(tok))
        if len(skill_tokens) != len(set(skill_tokens)):
            messages.append("Duplicate skills in structured document")

    return messages


def validate_artifacts(
    *,
    docx_path: Path,
    pdf_path: Path,
    expected_name: str = "Alex Michael Manjakunnel",
    document: ResumeDocument | None = None,
) -> ValidationReport:
    messages: list[str] = []
    docx_ok = False
    pdf_ok = False
    text_ok = False
    structural_ok = False
    page_count: int | None = None
    docx_text = ""

    try:
        docx_text = extract_docx_text(docx_path)
        docx_ok = bool(docx_text.strip())
        if not docx_ok:
            messages.append("DOCX text extraction is empty")
    except Exception as exc:
        messages.append(f"DOCX parse failed: {exc.__class__.__name__}")

    structural_msgs = _structural_checks(docx_text, document) if docx_text else []
    messages.extend(structural_msgs)
    structural_ok = docx_ok and not structural_msgs

    if not pdf_path.exists():
        messages.append("PDF file missing")
    elif pdf_path.stat().st_size == 0:
        messages.append("PDF file is empty")
    elif not is_valid_pdf(pdf_path):
        messages.append("PDF is invalid or unreadable")
    else:
        try:
            page_count = pdf_page_count(pdf_path)
            if page_count != 1:
                messages.append(f"PDF has {page_count} pages; exactly 1 required")
            pdf_text = extract_pdf_text(pdf_path)
            if len(pdf_text.strip()) < 40:
                messages.append("PDF text extraction nearly empty")
            else:
                pdf_ok = page_count == 1
                combined = f"{docx_text}\n{pdf_text}".lower()
                if expected_name.lower() not in combined:
                    messages.append("Candidate name missing from extracted text")
                for marker in ("experience", "education", "skills"):
                    if marker not in combined:
                        messages.append(f"Missing section in extracted text: {marker}")
                for snippet in FORBIDDEN_SNIPPETS:
                    if snippet in combined:
                        messages.append(f"Forbidden internal content leaked: {snippet}")
                if re.search(r"[\u2600-\u27BF]", pdf_text):
                    messages.append("Unusual Unicode decorative characters detected")
                text_ok = (
                    expected_name.lower() in combined
                    and "experience" in combined
                    and "education" in combined
                    and "skills" in combined
                    and not any(s in combined for s in FORBIDDEN_SNIPPETS)
                )
        except Exception as exc:
            messages.append(f"PDF validation failed: {exc.__class__.__name__}")

    # Exclude success note from failure condition
    failure_msgs = [m for m in messages if not m.startswith("Validation passed")]
    ok = docx_ok and pdf_ok and text_ok and structural_ok and not failure_msgs
    if ok:
        messages.append("Validation passed: one-page ATS-readable structured PDF")
    return ValidationReport(
        ok=ok,
        page_count=page_count,
        docx_ok=docx_ok,
        pdf_ok=pdf_ok,
        text_ok=text_ok,
        structural_ok=structural_ok,
        messages=messages,
    )


def atomic_copy_to_desktop(src_pdf: Path, desktop_path: Path) -> Path:
    if not is_valid_pdf(src_pdf):
        raise RuntimeError("Refusing Desktop copy: source PDF invalid")
    if pdf_page_count(src_pdf) != 1:
        raise RuntimeError("Refusing Desktop copy: PDF is not exactly one page")
    if src_pdf.stat().st_size == 0:
        raise RuntimeError("Refusing Desktop copy: empty PDF")

    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = desktop_path.parent / f".{desktop_path.name}.tmp"
    try:
        shutil.copy2(src_pdf, tmp)
        if not is_valid_pdf(tmp) or tmp.stat().st_size == 0:
            raise RuntimeError("Temp Desktop PDF failed validation")
        if pdf_page_count(tmp) != 1:
            raise RuntimeError("Temp Desktop PDF is not one page")
        tmp.replace(desktop_path)
    finally:
        if tmp.exists() and tmp != desktop_path:
            try:
                tmp.unlink()
            except OSError:
                pass
    return desktop_path
