from utils.ai_matching import build_candidate_profile, build_job_profile, calculate_keyword_match


def test_skill_aliases_are_normalized():
    candidate = build_candidate_profile("Python, JS, Fast API, Mongo DB", "")
    assert "python" in candidate["manual_skills"]
    assert "javascript" in candidate["manual_skills"]
    assert "fastapi" in candidate["manual_skills"]
    assert "mongodb" in candidate["manual_skills"]


def test_keyword_match_reports_missing_skills():
    result = calculate_keyword_match(
        ["python", "django"],
        ["python", "django", "redis"],
    )
    assert result["matchPercentage"] == 67
    assert result["matchedSkills"] == ["python", "django"]
    assert result["missingSkills"] == ["redis"]


def test_candidate_and_job_profiles_build_semantic_text():
    candidate = build_candidate_profile("Python, Django", "Backend developer building APIs")
    job = build_job_profile("Python, FastAPI", "Backend Engineer", "Build scalable APIs")
    assert "Backend developer" in candidate["semantic_text"]
    assert "Backend Engineer" in job["semantic_text"]
