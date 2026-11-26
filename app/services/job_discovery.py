"""Provider boundary for authorized external job discovery."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.security import valid_http_url


class JobProvider(ABC):
    @abstractmethod
    def search(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Return normalized job records or raise a provider-specific error."""


class ProviderUnavailable(RuntimeError):
    pass


class AgensiProvider(JobProvider):
    """Explicitly unavailable until official API access and credentials exist."""
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    def search(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        del query
        if not self.api_key:
            raise ProviderUnavailable("Agensi discovery is not configured; API access and credentials are required.")
        raise ProviderUnavailable("No verified Agensi API contract is configured.")


class MockJobProvider(JobProvider):
    def __init__(self, jobs: list[dict[str, Any]] | None = None):
        self.jobs = jobs or []

    def search(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        terms = " ".join(str(value).casefold() for value in query.values() if value)
        return [job for job in self.jobs if not terms or terms in " ".join(str(v) for v in job.values()).casefold()]


def normalize_job(record: dict[str, Any], source: str) -> dict[str, Any]:
    required = ("title", "company", "location", "description", "application_url")
    if not isinstance(record, dict) or not all(isinstance(record.get(key), str) for key in required):
        raise ValueError("Job provider returned an invalid job record.")
    if not valid_http_url(record["application_url"]):
        raise ValueError("Job provider returned an invalid application URL.")
    if any(len(record[key]) > limit for key, limit in {
        "title": 200,
        "company": 200,
        "location": 200,
        "description": 50000,
        "application_url": 2048,
    }.items()):
        raise ValueError("Job provider returned a field that exceeds the supported size.")
    skills = record.get("skills", [])
    if isinstance(skills, list):
        if len(skills) > 200 or any(not isinstance(skill, str) or len(skill) > 100 for skill in skills):
            raise ValueError("Job provider returned invalid skills.")
        skills = [skill.strip() for skill in skills if skill.strip()]
    elif isinstance(skills, str) and len(skills) <= 2000:
        skills = skills.strip()
    else:
        raise ValueError("Job provider returned invalid skills.")
    for field in ("job_type", "experience_level"):
        if not isinstance(record.get(field, ""), str) or len(record.get(field, "")) > 100:
            raise ValueError(f"Job provider returned an invalid {field}.")
    return {
        "title": record["title"].strip(),
        "company": record["company"].strip(),
        "location": record["location"].strip(),
        "description": record["description"].strip(),
        "skills": skills,
        "job_type": record.get("job_type", ""),
        "experience_level": record.get("experience_level", ""),
        "application_url": record["application_url"].strip(),
        "source": source,
        "source_id": str(record.get("source_id", "")),
    }
