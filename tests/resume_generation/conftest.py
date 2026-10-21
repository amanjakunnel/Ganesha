"""Helpers to build sanitized DOCX resume fixtures for tests."""

from __future__ import annotations

from pathlib import Path

from docx import Document


def write_resume_docx(path: Path, *, sections: dict[str, list[str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_paragraph("Alex Michael Manjakunnel")
    doc.add_paragraph("alex@example.com | linkedin.com/in/example | github.com/example")
    for heading, bullets in sections.items():
        doc.add_paragraph(heading.upper())
        for bullet in bullets:
            doc.add_paragraph(bullet)
    doc.save(str(path))
    return path


def build_standard_corpus(root: Path) -> Path:
    """Create a small multi-track corpus under root."""
    write_resume_docx(
        root / "Current_Resume_Dev.docx",
        sections={
            "Skills": ["Python, Java, SQL, Git, Docker"],
            "Experience": [
                "Software Engineer, Acme Corp  Jan 2022 – Present",
                "Built REST APIs in Python serving internal tooling.",
                "Improved CI/CD pipelines with Jenkins and Git.",
            ],
            "Projects": [
                "TacoDB — C++ disk-based relational DBMS with buffer management "
                "and persistent B+-Tree indexing."
            ],
            "Education": ["B.S. Computer Science, Example University  May 2024"],
        },
    )
    write_resume_docx(
        root / "Current_Resume_Cloud.docx",
        sections={
            "Skills": ["AWS, Kubernetes, Linux, Terraform, Docker"],
            "Experience": [
                "Cloud Engineer Intern, CloudCo  Jun 2023 – Aug 2023",
                "Operated Linux services on AWS with Kubernetes deployments.",
                "Automated release engineering scripts for containerized apps.",
            ],
            "Education": ["B.S. Computer Science, Example University  May 2024"],
        },
    )
    write_resume_docx(
        root / "Current_Resume_ML.docx",
        sections={
            "Skills": ["Python, PyTorch, scikit-learn, SQL"],
            "Experience": [
                "ML Intern, ModelLabs  May 2022 – Aug 2022",
                "Trained classification models in PyTorch for research prototypes.",
            ],
            "Education": ["B.S. Computer Science, Example University  May 2024"],
        },
    )
    write_resume_docx(
        root / "Current_Resume.docx",
        sections={
            "Skills": ["Python, Java, SQL"],
            "Experience": [
                "Software Engineer, Acme Corp  Jan 2022 – Present",
                "Built REST APIs in Python serving internal tooling.",
            ],
            "Education": ["B.S. Computer Science, Example University  May 2024"],
        },
    )
    write_resume_docx(
        root / "Company Specific" / "Acme_Tailored.docx",
        sections={
            "Skills": ["Python, Java"],
            "Experience": [
                "Software Engineer, Acme Corp  Jan 2022 – Present",
                "Delivered features exclusively for Acme secret product X (unverified claim).",
            ],
            "Education": ["B.S. Computer Science, Example University  May 2024"],
        },
    )
    # Noise files that must be ignored
    (root / "ignore_me.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "~$Current_Resume_Dev.docx").write_bytes(b"lock")
    (root / "notes.txt").write_text("not a resume", encoding="utf-8")
    return root
