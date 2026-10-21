from __future__ import annotations

import hashlib
import re
from pathlib import Path

from docx import Document

from apps.resume_generation.paths import SECTION_ALIASES, classify_track
from apps.resume_generation.schemas import ExtractedFact, ScanSummary

SKIP_PREFIXES = ("~$", ".")
DATE_RE = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}"
    r"(?:\s*[-–—]\s*(?:Present|Expected(?:\s+\w+)?\s+\d{4}|\w+\s+\d{4}))?",
    re.IGNORECASE,
)
METRIC_RE = re.compile(
    r"\b\d+(\.\d+)?\s*(%|percent|hours?|x|ms|s|k|m|b)?\b",
    re.IGNORECASE,
)
GPA_RE = re.compile(
    r"(?:GPA|G\.P\.A\.?)[:\s]*([0-9]\.[0-9]{1,2})\s*(?:/\s*4(?:\.0)?)?",
    re.IGNORECASE,
)
GPA_ALT_RE = re.compile(r"\b([0-9]\.[0-9]{1,2})\s*/\s*4(?:\.0)?\b")

SKILL_ALIASES = {
    "k8s": "kubernetes",
    "js": "javascript",
    "ts": "typescript",
    "golang": "go",
    "postgres": "postgresql",
    "tf": "tensorflow",
}

SKILL_TOKENS = [
    "python",
    "java",
    "javascript",
    "typescript",
    "c++",
    "c/c++",
    "c#",
    "go",
    "sql",
    "react",
    "node",
    "mongodb",
    "postgresql",
    "git",
    "docker",
    "kubernetes",
    "jenkins",
    "aws",
    "gcp",
    "azure",
    "terraform",
    "ansible",
    "kafka",
    "pytorch",
    "tensorflow",
    "linux",
    "shell",
    "bash",
    "grafana",
    "influxdb",
    "rabbitmq",
]

ACTION_START = re.compile(
    r"^(led|built|designed|developed|implemented|created|improved|reduced|"
    r"optimized|automated|deployed|maintained|managed|owned|collaborated|"
    r"engineered|architected|migrated|configured|monitored|wrote|delivered)\b",
    re.IGNORECASE,
)


def file_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def normalize_skill_token(token: str) -> str:
    t = token.strip().lower()
    t = t.replace("c/c++", "c++")
    return SKILL_ALIASES.get(t, t)


def discover_docx_files(root: Path) -> tuple[list[Path], ScanSummary]:
    summary = ScanSummary()
    found: list[Path] = []
    if not root.exists():
        summary.errors.append(f"Source path not found: {root}")
        return found, summary
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if name.startswith(SKIP_PREFIXES):
            summary.skipped += 1
            continue
        if path.suffix.lower() != ".docx":
            summary.skipped += 1
            continue
        found.append(path)
        summary.discovered += 1
    return found, summary


def _detect_section(text: str) -> str | None:
    key = normalize_text(text)
    if key in SECTION_ALIASES:
        return SECTION_ALIASES[key]
    if text.isupper() and 3 <= len(text) <= 40:
        return SECTION_ALIASES.get(key)
    return None


def _extract_skills(text: str) -> list[str]:
    lower = text.lower()
    found: list[str] = []
    for tok in SKILL_TOKENS:
        if tok in lower:
            found.append(normalize_skill_token(tok))
    # Deduplicate preserving order
    out: list[str] = []
    seen: set[str] = set()
    for s in found:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _extract_gpa(text: str) -> str | None:
    m = GPA_RE.search(text)
    if m:
        return m.group(1)
    m = GPA_ALT_RE.search(text)
    if m and "gpa" in text.lower():
        return m.group(1)
    return None


def _split_header_parts(raw: str) -> tuple[str | None, str | None, str | None]:
    """Best-effort parse of company / title / dates from a header line."""
    dates = DATE_RE.search(raw)
    dates_text = dates.group(0) if dates else None
    cleaned = DATE_RE.sub("", raw)
    cleaned = cleaned.replace("\t", " | ").replace("•", "|")
    parts = [p.strip(" -–—|") for p in re.split(r"\s*\|\s*", cleaned) if p.strip()]
    if len(parts) >= 2:
        # Heuristic: company often first or last short proper name
        a, b = parts[0], parts[1]
        if len(a.split()) <= 5 and not ACTION_START.match(a):
            return a, b, dates_text
        return b, a, dates_text
    if parts:
        return parts[0], None, dates_text
    return None, None, dates_text


def _looks_like_bullet(raw: str) -> bool:
    if ACTION_START.match(raw):
        return True
    if raw.endswith(".") and len(raw) > 40:
        return True
    if raw.startswith(("•", "-", "–", "*")):
        return True
    return False


def _looks_like_header(raw: str, section: str) -> bool:
    if _looks_like_bullet(raw):
        return False
    if len(raw) > 160:
        return False
    if DATE_RE.search(raw):
        return True
    if "\t" in raw or "|" in raw:
        return True
    if section == "projects" and len(raw.split()) <= 12 and not raw.endswith("."):
        return True
    if section == "experience" and len(raw.split()) <= 14 and not raw.endswith("."):
        return True
    if section == "education" and any(
        tok in raw.lower()
        for tok in (
            "university",
            "college",
            "bachelor",
            "master",
            "b.s",
            "m.s",
            "b.tech",
            "btech",
        )
    ):
        return True
    return False


def _parent_key(
    entity_type: str,
    name: str | None,
    title: str | None,
    dates: str | None,
) -> str:
    return normalize_text(
        "|".join(x for x in (entity_type, name or "", title or "", dates or "") if x)
    )


def extract_facts_from_docx(path: Path, root: Path) -> list[ExtractedFact]:
    doc = Document(str(path))
    facts: list[ExtractedFact] = []
    section = "other"
    parent_name: str | None = None
    role_title: str | None = None
    dates_ctx: str | None = None
    parent_key: str | None = None
    parent_type: str | None = None
    pending_org: str | None = None  # company/institution on prior short line

    for para in doc.paragraphs:
        raw = (para.text or "").strip()
        if not raw:
            continue
        maybe_section = _detect_section(raw)
        if maybe_section:
            section = maybe_section
            parent_name = None
            role_title = None
            dates_ctx = None
            parent_key = None
            parent_type = None
            pending_org = None
            continue

        dates = DATE_RE.search(raw)
        dates_text = dates.group(0) if dates else None
        metrics = [m.group(0) for m in METRIC_RE.finditer(raw)]
        gpa = _extract_gpa(raw) if section == "education" else None
        skills = _extract_skills(raw)

        if section == "skills":
            facts.append(
                ExtractedFact(
                    section=section,
                    fact_kind="skill_line",
                    source_text=raw,
                    normalized_text=normalize_text(raw),
                    content_hash=text_hash(normalize_text(raw)),
                    parent_entity_type="skill",
                    skills=skills,
                    metrics=metrics,
                )
            )
            continue

        # Two-line org → title/date pattern common in ATS resumes
        if (
            section in {"experience", "education"}
            and not dates_text
            and not gpa
            and not _looks_like_bullet(raw)
            and "gpa" not in raw.lower()
            and len(raw) < 80
            and len(raw.split()) <= 8
        ):
            pending_org = raw.replace("\t", " ").strip()
            continue

        if section in {"experience", "projects", "education"} and _looks_like_header(
            raw, section
        ):
            company, title, d = _split_header_parts(raw)
            if section == "experience":
                if pending_org:
                    parent_name = pending_org
                    role_title = (
                        title or company or DATE_RE.sub("", raw).strip(" -–—|\t")
                    )
                    pending_org = None
                elif (
                    company
                    and title
                    and normalize_text(company) != normalize_text(title)
                ):
                    parent_name, role_title = company, title
                elif dates_text:
                    # Single line title + dates; company unknown → keep as title only
                    role_title = (
                        DATE_RE.sub("", raw).replace("\t", " ").strip(" -–—|")
                        or company
                        or raw
                    )
                    parent_name = company if company and company != role_title else None
                    if not parent_name:
                        # Unusable as complete role until company known — still record
                        parent_name = role_title
                else:
                    parent_name = company or raw
                    role_title = title or company or raw
                dates_ctx = d or dates_text
                parent_type = "experience"
                parent_key = _parent_key(
                    "experience", parent_name, role_title, dates_ctx
                )
                kind = "role_header"
            elif section == "projects":
                parent_name = (
                    pending_org or company or raw.split("|")[0].split("—")[0].strip()
                )
                pending_org = None
                role_title = None
                dates_ctx = d or dates_text
                parent_type = "project"
                parent_key = _parent_key("project", parent_name, None, dates_ctx)
                kind = "project_header"
            else:
                if pending_org:
                    parent_name = pending_org
                    role_title = (
                        title or company or DATE_RE.sub("", raw).strip(" -–—|\t")
                    )
                    pending_org = None
                else:
                    parent_name = company or raw
                    role_title = title
                dates_ctx = d or dates_text
                parent_type = "education"
                parent_key = _parent_key("education", parent_name, role_title, None)
                kind = "education_header"

            facts.append(
                ExtractedFact(
                    section=section,
                    fact_kind=kind,
                    source_text=raw,
                    normalized_text=normalize_text(raw),
                    content_hash=text_hash(normalize_text(raw)),
                    employer_or_project=parent_name,
                    parent_entity_type=parent_type,
                    parent_name=parent_name,
                    role_title=role_title,
                    parent_key=parent_key,
                    gpa_value=gpa,
                    skills=skills,
                    dates_text=dates_ctx,
                    metrics=metrics,
                )
            )
            continue

        # Bullet / body line under current parent
        if section in {"experience", "projects"} and not parent_key:
            # Orphan bullet — mark unusable later via missing parent
            facts.append(
                ExtractedFact(
                    section=section,
                    fact_kind="orphan_bullet",
                    source_text=raw,
                    normalized_text=normalize_text(raw),
                    content_hash=text_hash(normalize_text(raw)),
                    parent_entity_type=section.rstrip("s")
                    if section != "experience"
                    else "experience",
                    skills=skills,
                    dates_text=dates_text,
                    metrics=metrics,
                )
            )
            continue

        facts.append(
            ExtractedFact(
                section=section,
                fact_kind="bullet" if section in {"experience", "projects"} else "line",
                source_text=raw.lstrip("•-*–— ").strip(),
                normalized_text=normalize_text(raw.lstrip("•-*–— ")),
                content_hash=text_hash(normalize_text(raw.lstrip("•-*–— "))),
                employer_or_project=parent_name,
                parent_entity_type=parent_type or section,
                parent_name=parent_name,
                role_title=role_title,
                parent_key=parent_key,
                gpa_value=gpa,
                skills=skills,
                dates_text=dates_text or dates_ctx,
                metrics=metrics,
            )
        )
    return facts


def classify_document_track(path: Path, root: Path) -> str:
    return classify_track(path, root)
