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
    r"(?:\s*[-–—]\s*(?:Present|Expected\s+\w+\s+\d{4}|\w+\s+\d{4}))?",
    re.IGNORECASE,
)
METRIC_RE = re.compile(
    r"\b\d+(\.\d+)?\s*(%|percent|hours?|x|ms|s|k|m|b)?\b",
    re.IGNORECASE,
)
SKILL_TOKENS = [
    "python",
    "java",
    "javascript",
    "typescript",
    "c++",
    "c#",
    "sql",
    "react",
    "node",
    "mongodb",
    "git",
    "docker",
    "kubernetes",
    "k8s",
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
    "grafana",
    "influxdb",
]


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
    # All-caps short headings
    if text.isupper() and 3 <= len(text) <= 40:
        return SECTION_ALIASES.get(key)
    return None


def _extract_skills(text: str) -> list[str]:
    lower = text.lower()
    return [tok for tok in SKILL_TOKENS if tok in lower]


def extract_facts_from_docx(path: Path, root: Path) -> list[ExtractedFact]:
    doc = Document(str(path))
    facts: list[ExtractedFact] = []
    section = "other"
    current_entity: str | None = None
    for para in doc.paragraphs:
        raw = (para.text or "").strip()
        if not raw:
            continue
        maybe_section = _detect_section(raw)
        if maybe_section:
            section = maybe_section
            current_entity = None
            continue
        # Likely employer/project header lines are short and not bullets
        if (
            section in {"experience", "projects"}
            and len(raw) < 120
            and not raw.endswith(".")
        ):
            if DATE_RE.search(raw) or "\t" in raw or len(raw.split()) <= 8:
                # Prefer keeping entity names without dates when possible
                entity = DATE_RE.sub("", raw).replace("\t", " ").strip(" -–—")
                if entity and not entity.lower().startswith(
                    (
                        "associate",
                        "devops",
                        "machine",
                        "software",
                        "intern",
                        "led ",
                        "built ",
                        "designed ",
                    )
                ):
                    current_entity = entity
                    # Still record role/date lines as facts for provenance
        dates = DATE_RE.search(raw)
        metrics = [m.group(0) for m in METRIC_RE.finditer(raw)]
        facts.append(
            ExtractedFact(
                section=section,
                source_text=raw,
                normalized_text=normalize_text(raw),
                content_hash=text_hash(normalize_text(raw)),
                employer_or_project=current_entity,
                skills=_extract_skills(raw),
                dates_text=dates.group(0) if dates else None,
                metrics=metrics,
            )
        )
    return facts


def classify_document_track(path: Path, root: Path) -> str:
    return classify_track(path, root)
