"""Deterministic, explainable career and job intelligence helpers."""
from __future__ import annotations

import re
import math
import os
from typing import Any, Iterable

from utils.ai_matching import (
    build_candidate_profile,
    build_job_profile,
    calculate_keyword_match,
    dedupe_preserve_order,
)
from utils import ai_matching


def _unique(values: Iterable[str]) -> list[str]:
    return dedupe_preserve_order(value.strip() for value in values if isinstance(value, str) and value.strip())


def hybrid_match(
    candidate: dict[str, Any],
    job: dict[str, Any],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Return a normalized match result with evidence-backed explanations."""
    candidate_profile = candidate if "matching_keywords" in candidate else build_candidate_profile(
        candidate.get("skills", ""), candidate.get("resume_text", "")
    )
    job_profile = job if "match_keywords" in job else build_job_profile(
        job.get("skills", ""), job.get("title", ""), job.get("description", "")
    )
    candidate_skills = _unique(
        candidate_profile.get("matching_keywords", [])
        or candidate_profile.get("manual_skills", []) + candidate_profile.get("resume_skills", [])
    )
    required = _unique(job_profile.get("match_keywords", []))
    inferred = _unique(job_profile.get("inferred_keywords", []))
    result = calculate_keyword_match(
        candidate_skills,
        required,
        inferred,
        str(candidate_profile.get("semantic_text", "")),
        str(job_profile.get("semantic_text", "")),
    )
    semantic = max(0.0, min(1.0, float(result.get("semanticSimilarity", 0))))
    keyword = max(0.0, min(1.0, float(result.get("keywordMatchPercentage", 0)) / 100))
    candidate_text = str(candidate_profile.get("semantic_text", "")).lower()
    job_text = str(job_profile.get("semantic_text", "")).lower()
    title_words = re.findall(r"[a-z0-9+#.]+", str(job.get("title", "")).lower())
    title_relevance = sum(
        bool(re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", candidate_text))
        for word in title_words
    ) / len(title_words) if title_words else 0.0
    experience_present = bool(candidate.get("experience") or re.search(r"\b\d+\+?\s+years?\b", candidate_text))
    experience_relevance = float(experience_present and bool(re.search(r"\bexperience\b|\byears?\b", job_text)))
    configured = weights or {}
    parts = {
        "semantic": semantic,
        "skills": keyword,
        "title": title_relevance,
        "experience": experience_relevance,
    }
    defaults = {"semantic": 0.45, "skills": 0.40, "title": 0.10, "experience": 0.05}
    selected = {}
    for key, default in defaults.items():
        try:
            raw_weight = configured.get(key, os.getenv(f"HYBRID_{key.upper()}_WEIGHT", default))
            weight = float(raw_weight)
        except (TypeError, ValueError):
            weight = default
        selected[key] = weight if math.isfinite(weight) and weight >= 0 else default
    # The matching facade intentionally falls back to keyword scoring when the
    # optional transformer is unavailable. Keep that behavior here too instead
    # of diluting an exact keyword match with a semantic score of zero.
    semantic_available = bool(candidate_text.strip() and job_text.strip() and ai_matching.get_embedding_model() is not None)
    if not semantic_available:
        score = float(result.get("keywordMatchPercentage", 0)) / 100
        selected = {"semantic": 0.0, "skills": 1.0, "title": 0.0, "experience": 0.0}
    else:
        total_weight = sum(selected.values())
        selected = (
            {key: value / total_weight for key, value in selected.items()}
            if math.isfinite(total_weight) and total_weight > 0 else defaults
        )
        score = sum(parts[key] * selected[key] for key in parts)
    if not candidate_skills and not candidate_text.strip():
        score = 0.0
    result["matchPercentage"] = round(max(0.0, min(100.0, score * 100)))
    result["matchedSkills"] = [s for s in required if s.casefold() in {x.casefold() for x in candidate_skills}]
    result["missingSkills"] = [s for s in required if s.casefold() not in {x.casefold() for x in candidate_skills}]
    result["scoreBreakdown"] = {
        key: {"score": round(parts[key] * 100, 1), "weight": round(selected[key], 3)}
        for key in parts
    }
    result["explanations"] = [
        f"{len(result['matchedSkills'])} of {len(required)} listed required skills match your profile."
        if required else "The job listing does not provide a structured required-skill list.",
    ]
    result["explanations"].append(
        f"Semantic profile similarity is {round(semantic * 100)}% using the configured matcher."
        if semantic_available else
        "Semantic matching is unavailable; the score uses explicit skill overlap."
    )
    if title_relevance:
        result["explanations"].append("Your profile contains wording related to the job title.")
    if experience_present:
        result["relevantExperience"] = str(candidate.get("experience", ""))[:500]
    result["matchingKeywords"] = [word for word in inferred if word.casefold() in {x.casefold() for x in candidate_skills}]
    result["educationRelevance"] = None
    return result


SKILL_CATEGORIES = {
    "programming_languages": {"python", "java", "javascript", "typescript", "c", "c++", "c#", "php", "ruby"},
    "frameworks_libraries": {"django", "flask", "fastapi", "react", "angular", "vue", "pandas", "numpy", "pytorch", "tensorflow"},
    "databases": {"sql", "mysql", "postgresql", "mongodb", "redis", "oracle", "sqlite"},
    "cloud_devops": {"aws", "azure", "gcp", "docker", "kubernetes", "ci/cd", "devops", "linux"},
    "ai_machine_learning": {"machine learning", "deep learning", "scikit-learn", "spark"},
    "software_engineering": {"git", "github", "rest api", "microservices", "testing", "pytest", "agile", "scrum"},
}


def analyze_skill_gaps(candidate_skills: Iterable[str], required_skills: Iterable[str]) -> dict[str, Any]:
    present = _unique(candidate_skills)
    required = _unique(required_skills)
    present_keys = {skill.casefold() for skill in present}
    items = []
    for skill in required:
        category = next((name for name, values in SKILL_CATEGORIES.items() if skill.casefold() in values), "other")
        matched = skill.casefold() in present_keys
        items.append({
            "skill": skill,
            "status": "present" if matched else "missing",
            "priority": "high" if not matched else "maintain",
            "category": category,
            "learningTopics": [] if matched else [f"{skill} fundamentals", f"{skill} applied project"],
            "reason": "Listed by the job description." if matched else "Required by the job description and absent from the supplied profile.",
        })
    return {"existingSkills": present, "missingSkills": [item["skill"] for item in items if item["status"] == "missing"], "skills": items}


def career_insights(candidate: dict[str, Any], jobs: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Summarize profile-specific opportunities without external generation."""
    ranked = []
    profile = build_candidate_profile(candidate.get("skills", ""), candidate.get("resume_text", ""))
    for job in jobs:
        match = hybrid_match(candidate, job)
        identifier = job.get("id", job.get("_id"))
        ranked.append({"title": job.get("title", ""), "jobId": str(identifier) if identifier is not None else None, "matchPercentage": match["matchPercentage"], "missingSkills": match["missingSkills"]})
    ranked.sort(key=lambda row: (-row["matchPercentage"], str(row["title"]).casefold()))
    resume_text = str(candidate.get("resume_text", ""))
    if not resume_text.strip():
        resume_suggestions = ["Upload a resume to receive profile-specific feedback."]
    else:
        resume_suggestions = []
        if not re.search(r"\b(experience|employment|work history|projects?)\b", resume_text, re.I):
            resume_suggestions.append("Add a clearly labeled experience or projects section if it applies to your background.")
        if not re.search(r"\b\d+(?:\.\d+)?\s*(?:%|x\b|users?\b|customers?\b|years?\b|months?\b|hours?\b)", resume_text, re.I):
            resume_suggestions.append("Where accurate, add measurable outcomes to the experience or project details already in your resume.")
        if not resume_suggestions:
            resume_suggestions.append("Your resume includes experience/project details and measurable figures; check that each result is clear and relevant to the roles shown.")
    return {
        "resumeSuggestions": resume_suggestions,
        "profileSkills": _unique(profile.get("manual_skills", []) + profile.get("resume_skills", [])),
        "suitableRoles": ranked[:5],
        "learningPriorities": list(dict.fromkeys(skill for row in ranked[:3] for skill in row["missingSkills"]))[:10],
        "interviewTopics": list(dict.fromkeys(skill for row in ranked[:3] for skill in row.get("missingSkills", [])))[:8],
        "externalProviderUsed": False,
    }
