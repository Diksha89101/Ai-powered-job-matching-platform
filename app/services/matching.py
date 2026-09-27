"""Matching service facade.

Keeps the Flask application independent from the lower-level matching implementation.
The implementation remains in utils.ai_matching during the incremental refactor.
"""
from utils.ai_matching import (
    build_candidate_profile,
    build_job_profile,
    calculate_keyword_match,
)

__all__ = [
    "build_candidate_profile",
    "build_job_profile",
    "calculate_keyword_match",
]
