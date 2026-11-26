"""Authenticated-agent business operation boundary.

HTTP authentication remains the responsibility of the caller. These operations
accept already-authorized profile data and return JSON-serializable values.
"""
from __future__ import annotations

import math
from typing import Any

from app.security import valid_http_url
from app.services.intelligence import analyze_skill_gaps, career_insights, hybrid_match
from app.services.job_discovery import ProviderUnavailable
from app.services.resume_parser import parse_resume_text
from utils.ai_matching import build_candidate_profile, build_job_profile


class AgentInputError(ValueError):
    pass


def _validate_job(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentInputError("job must be an object")
    allowed_text = ("title", "company", "location", "skills", "description", "job_type", "experience_level", "application_url", "source")
    cleaned: dict[str, Any] = {}
    for key in allowed_text:
        item = value.get(key, "")
        if isinstance(item, list) and key == "skills":
            if len(item) > 200 or any(not isinstance(skill, str) or len(skill) > 100 for skill in item):
                raise AgentInputError("job skills must be strings of at most 100 characters")
            cleaned[key] = ", ".join(item)
        elif isinstance(item, str) and len(item) <= 20000:
            cleaned[key] = item
        elif item not in (None, ""):
            raise AgentInputError(f"job {key} must be a string of at most 20000 characters")
    if cleaned.get("application_url") and not valid_http_url(cleaned["application_url"]):
        raise AgentInputError("job application_url must be a valid HTTP or HTTPS URL")
    identifier = value.get("id", value.get("_id", ""))
    if identifier:
        cleaned["id"] = str(identifier)[:200]
    return cleaned


def _validate_profile(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AgentInputError("profile must be an object")
    for key in ("skills", "resume_text", "experience"):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 50000):
            raise AgentInputError(f"{key} must be a string of at most 50000 characters")
    return value


def _validate_jobs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 500:
        raise AgentInputError("jobs must be an array of at most 500 objects")
    return [_validate_job(job) for job in value]


def analyze_resume(profile: dict[str, Any]) -> dict[str, Any]:
    profile = _validate_profile(profile)
    resume_text = profile.get("resume_text", "")
    parsed = parse_resume_text(resume_text)
    parsed.pop("text", None)
    return {
        "profile": build_candidate_profile(profile.get("skills", ""), resume_text),
        "resume": parsed,
    }


def match_jobs(profile: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profile = _validate_profile(profile)
    results = []
    for cleaned in _validate_jobs(jobs):
        results.append({**cleaned, **hybrid_match(profile, cleaned)})
    return sorted(results, key=lambda row: (-row["matchPercentage"], str(row.get("id", ""))))


def identify_skill_gaps(profile: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    profile = _validate_profile(profile)
    job = _validate_job(job)
    candidate = build_candidate_profile(profile.get("skills", ""), profile.get("resume_text", ""))
    required = build_job_profile(job.get("skills", ""), job.get("title", ""), job.get("description", ""))
    return analyze_skill_gaps(candidate["manual_skills"] + candidate["resume_skills"], required["match_keywords"])


def compare_job_descriptions(profile: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return match_jobs(profile, jobs)


def recommend_jobs(profile: dict[str, Any], jobs: list[dict[str, Any]], preferences: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    preferences = preferences or {}
    if not isinstance(preferences, dict):
        raise AgentInputError("preferences must be an object")
    if len(preferences) > 20 or any(not isinstance(value, str) or len(value) > 200 for value in preferences.values()):
        raise AgentInputError("preference values must be strings of at most 200 characters")
    results = match_jobs(profile, jobs)
    seen = set()
    filtered = []
    for row in results:
        key = str(row.get("id") or "|".join(str(row.get(field, "")).casefold() for field in ("title", "company", "location")))
        if key and key in seen:
            continue
        seen.add(key)
        if preferences.get("location") and str(preferences["location"]).casefold() not in str(row.get("location", "")).casefold():
            continue
        if preferences.get("job_type") and str(preferences["job_type"]).casefold() != str(row.get("job_type", "")).casefold():
            continue
        if preferences.get("experience_level") and str(preferences["experience_level"]).casefold() != str(row.get("experience_level", "")).casefold():
            continue
        if preferences.get("skill") and str(preferences["skill"]).casefold() not in " ".join(row.get("matchedSkills", [])).casefold():
            continue
        filtered.append(row)
    return filtered


def analyze_career_options(profile: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    return career_insights(_validate_profile(profile), _validate_jobs(jobs))


def search_jobs(provider: Any, query: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(query, dict) or len(query) > 20 or any(
        not isinstance(key, str)
        or not isinstance(value, (str, int, float, bool))
        or (isinstance(value, float) and not math.isfinite(value))
        or len(str(value)) > 200
        for key, value in query.items()
    ):
        raise AgentInputError("query must contain at most 20 scalar filters of at most 200 characters")
    try:
        results = provider.search(query)
    except ProviderUnavailable:
        raise
    except TimeoutError as exc:
        raise ProviderUnavailable(str(exc)) from exc
    except Exception as exc:
        raise ProviderUnavailable("Job discovery provider failed.") from exc
    if not isinstance(results, list) or len(results) > 500 or any(not isinstance(job, dict) for job in results):
        raise AgentInputError("provider returned invalid results")
    normalized = [_validate_job(job) for job in results]
    unique = []
    seen = set()
    for job in normalized:
        key = str(job.get("id") or "|".join(
            str(job.get(field, "")).casefold()
            for field in ("title", "company", "location")
        ))
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique
