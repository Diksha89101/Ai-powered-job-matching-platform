from utils import ai_matching
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


def test_keyword_match_is_case_insensitive_and_normalizes_duplicate_values():
    result = calculate_keyword_match(
        ["PYTHON", "JavaScript", "python"],
        ["python", "JAVASCRIPT", "Docker"],
    )
    assert result["matchPercentage"] == 67
    assert result["matchedSkills"] == ["python", "JAVASCRIPT"]
    assert result["missingSkills"] == ["Docker"]


def test_keyword_match_empty_requirements_has_zero_score_without_division_error():
    result = calculate_keyword_match(["python"], [])
    assert result["matchPercentage"] == 0
    assert result["keywordMatchPercentage"] == 0
    assert result["totalKeywords"] == 0


def test_hybrid_score_is_clamped_and_uses_normalized_config(monkeypatch):
    class IdenticalEmbeddingModel:
        def encode(self, texts, normalize_embeddings=True):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(ai_matching, "get_embedding_model", lambda: IdenticalEmbeddingModel())
    monkeypatch.setenv("SEMANTIC_WEIGHT", "10")
    monkeypatch.setenv("KEYWORD_WEIGHT", "0")
    result = calculate_keyword_match(["python"], ["python"], candidate_text="Python", job_text="Python")
    assert result["matchPercentage"] == 100

    monkeypatch.setenv("SEMANTIC_WEIGHT", "nan")
    monkeypatch.setenv("KEYWORD_WEIGHT", "nan")
    result = calculate_keyword_match([], ["python"], candidate_text="Python", job_text="Python")
    assert 0 <= result["matchPercentage"] <= 100


def test_corrupt_resume_documents_return_empty_text(tmp_path):
    for suffix in (".pdf", ".docx", ".doc"):
        path = tmp_path / f"corrupt{suffix}"
        path.write_bytes(b"not a readable resume document")
        assert ai_matching.extract_resume_text(str(path)) == ""


def test_candidate_and_job_profiles_build_semantic_text():
    candidate = build_candidate_profile("Python, Django", "Backend developer building APIs")
    job = build_job_profile("Python, FastAPI", "Backend Engineer", "Build scalable APIs")
    assert "Backend developer" in candidate["semantic_text"]
    assert "Backend Engineer" in job["semantic_text"]
