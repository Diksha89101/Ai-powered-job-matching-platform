"""Resume extraction and conservative profile-field parsing."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from utils.ai_matching import extract_resume_text, extract_resume_skills_nlp


SOFT_SKILLS = {"communication", "leadership", "problem solving", "agile", "scrum"}


def parse_resume(file_path: str | Path) -> dict[str, Any]:
    """Extract supported resume content and fields explicitly present in it."""
    path = Path(file_path)
    text = extract_resume_text(str(path))
    return parse_resume_text(text)


def parse_resume_text(text: str) -> dict[str, Any]:
    """Parse fields from already-extracted resume text without persisting it."""
    emails = list(dict.fromkeys(re.findall(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", text, re.I)))
    phone_matches = re.findall(r"(?:\+?\d[\d\s().-]{7,}\d)", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    name = ""
    if lines:
        possible_name = lines[0]
        if len(possible_name) <= 100 and not re.search(r"@|\d|resume|curriculum vitae", possible_name, re.I):
            name = possible_name
    sections: dict[str, list[str]] = {key: [] for key in ("education", "experience", "projects", "certifications")}
    headings = {
        "education": re.compile(r"^(education|qualifications?)\b", re.I),
        "experience": re.compile(r"^(work |professional )?experience|internships?\b", re.I),
        "projects": re.compile(r"^projects?\b", re.I),
        "certifications": re.compile(r"^(certifications?|licenses?)\b", re.I),
    }
    current = None
    for line in lines:
        matched = next((key for key, pattern in headings.items() if pattern.search(line)), None)
        if matched:
            current = matched
            continue
        if current:
            if any(pattern.search(line) for pattern in headings.values()):
                current = next(key for key, pattern in headings.items() if pattern.search(line))
            else:
                sections[current].append(line)
    years = [int(value) for value in re.findall(r"\b(\d{1,2})\+?\s+years?\b", text, re.I) if 0 < int(value) < 60]
    detected_skills = extract_resume_skills_nlp(text)
    return {
        "text": text,
        "name": name,
        "emails": emails,
        "contactNumbers": list(dict.fromkeys(re.sub(r"\s+", " ", number).strip() for number in phone_matches)),
        "technicalSkills": [skill for skill in detected_skills if skill not in SOFT_SKILLS],
        "softSkills": [skill for skill in detected_skills if skill in SOFT_SKILLS],
        "education": sections["education"],
        "experience": sections["experience"],
        "projects": sections["projects"],
        "certifications": sections["certifications"],
        "relevantExperienceYears": max(years) if years else None,
        "limitations": ["Name, sections, and duration are inferred only from explicit text patterns; verify before relying on them."] if text else ["No readable text was extracted."],
    }

__all__ = ["extract_resume_text", "parse_resume", "parse_resume_text"]
