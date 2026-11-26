import math
from zipfile import ZipFile

from app.services.agent_operations import AgentInputError, identify_skill_gaps, match_jobs, recommend_jobs, search_jobs
from app.services.intelligence import analyze_skill_gaps, hybrid_match
from app.services.job_discovery import AgensiProvider, MockJobProvider, ProviderUnavailable, normalize_job
from app.services.resume_parser import parse_resume, parse_resume_text


def test_resume_fields_are_extracted_only_from_supplied_text():
    result = parse_resume_text("Asha Rao\nasha@example.com\n+91 98765 43210\n\nEDUCATION\nBSc Computer Science\n\nEXPERIENCE\n3 years Python engineering\n\nPROJECTS\nRecruitment portal")
    assert result["name"] == "Asha Rao"
    assert result["emails"] == ["asha@example.com"]
    assert "BSc Computer Science" in result["education"]
    assert result["relevantExperienceYears"] == 3


def test_docx_resume_parser_preserves_sections_and_deduplicates_skills(tmp_path):
    path = tmp_path / "candidate.docx"
    document_xml = '''<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>Asha Rao</w:t></w:r></w:p>
      <w:p><w:r><w:t>asha@example.com</w:t></w:r></w:p>
      <w:p><w:r><w:t>EDUCATION</w:t></w:r></w:p>
      <w:p><w:r><w:t>BSc Computer Science</w:t></w:r></w:p>
      <w:p><w:r><w:t>EXPERIENCE</w:t></w:r></w:p>
      <w:p><w:r><w:t>3 years Python engineering</w:t></w:r></w:p>
      <w:p><w:r><w:t>PROJECTS</w:t></w:r></w:p>
      <w:p><w:r><w:t>Python recruitment portal</w:t></w:r></w:p>
    </w:body></w:document>'''
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    result = parse_resume(path)
    assert result["name"] == "Asha Rao"
    assert result["education"] == ["BSc Computer Science"]
    assert result["experience"] == ["3 years Python engineering"]
    assert result["technicalSkills"].count("python") == 1
    assert result["relevantExperienceYears"] == 3


def test_pdf_resume_parser_extracts_readable_text_and_fields(tmp_path):
    import fitz

    path = tmp_path / "candidate.pdf"
    document = fitz.open()
    page = document.new_page()
    for line_number, line in enumerate((
        "Asha Rao", "asha@example.com", "EDUCATION", "BSc Computer Science",
        "EXPERIENCE", "4 years Python engineering",
    )):
        page.insert_text((50, 50 + line_number * 18), line)
    path.write_bytes(document.tobytes())
    document.close()

    result = parse_resume(path)
    assert result["name"] == "Asha Rao"
    assert result["emails"] == ["asha@example.com"]
    assert result["education"] == ["BSc Computer Science"]
    assert result["relevantExperienceYears"] == 4


def test_malformed_resume_text_returns_empty_profile_without_fabrication():
    result = parse_resume_text("")
    assert result["name"] == ""
    assert result["emails"] == []
    assert result["technicalSkills"] == []
    assert result["relevantExperienceYears"] is None


def test_hybrid_match_is_bounded_explainable_and_deduplicates_skills(monkeypatch):
    from utils import ai_matching

    class IdenticalEmbeddingModel:
        def encode(self, texts, normalize_embeddings=True):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(ai_matching, "get_embedding_model", lambda: IdenticalEmbeddingModel())
    result = hybrid_match(
        {"skills": "Python, PY, SQL", "resume_text": "Python engineer"},
        {"title": "Python Developer", "skills": "Python, Python, Docker", "description": "Build Python services"},
        {"semantic": 0.5, "skills": 0.5, "title": 0, "experience": 0},
    )
    assert 0 <= result["matchPercentage"] <= 100
    assert len(result["matchedSkills"]) == 1
    assert result["missingSkills"] == ["docker"]
    assert result["scoreBreakdown"]["skills"]["weight"] == 0.5
    assert result["explanations"]


def test_empty_profile_gets_no_inflated_match():
    result = hybrid_match({}, {"title": "Engineer", "skills": "Python", "description": ""})
    assert result["matchPercentage"] == 0


def test_keyword_only_fallback_preserves_exact_skill_match(monkeypatch):
    from utils import ai_matching

    monkeypatch.setattr(ai_matching, "get_embedding_model", lambda: None)
    result = hybrid_match(
        {"skills": "Python, Django", "resume_text": "Python Django engineer"},
        {"title": "Backend Developer", "skills": "Python, Django", "description": "Build Python Django services"},
    )
    assert result["matchPercentage"] == 100
    assert result["scoreBreakdown"]["skills"]["weight"] == 1.0
    assert result["scoreBreakdown"]["semantic"]["weight"] == 0.0


def test_hybrid_weights_are_finite_and_title_terms_use_word_boundaries(monkeypatch):
    from utils import ai_matching

    class IdenticalEmbeddingModel:
        def encode(self, texts, normalize_embeddings=True):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(ai_matching, "get_embedding_model", lambda: IdenticalEmbeddingModel())
    result = hybrid_match(
        {"skills": "React", "resume_text": "React developer"},
        {"title": "C architect", "skills": "React", "description": "React work"},
        {"semantic": math.inf, "skills": float("nan"), "title": "bad", "experience": -1},
    )
    assert 0 <= result["matchPercentage"] <= 100
    assert result["scoreBreakdown"]["title"]["score"] == 0
    assert sum(part["weight"] for part in result["scoreBreakdown"].values()) == 1.0


def test_hybrid_weights_can_be_configured_from_environment(monkeypatch):
    from utils import ai_matching

    class IdenticalEmbeddingModel:
        def encode(self, texts, normalize_embeddings=True):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(ai_matching, "get_embedding_model", lambda: IdenticalEmbeddingModel())
    monkeypatch.setenv("HYBRID_SEMANTIC_WEIGHT", "0")
    monkeypatch.setenv("HYBRID_SKILLS_WEIGHT", "1")
    monkeypatch.setenv("HYBRID_TITLE_WEIGHT", "0")
    monkeypatch.setenv("HYBRID_EXPERIENCE_WEIGHT", "0")
    result = hybrid_match(
        {"skills": "Python", "resume_text": "Python"},
        {"skills": "Python", "title": "Engineer", "description": "Python"},
    )
    assert result["scoreBreakdown"]["skills"]["weight"] == 1.0


def test_skill_gaps_include_categories_and_priorities():
    result = analyze_skill_gaps(["Python"], ["Python", "Docker"])
    assert result["missingSkills"] == ["Docker"]
    assert result["skills"][1]["priority"] == "high"
    assert result["skills"][1]["category"] == "cloud_devops"


def test_agent_validation_and_personalized_filters(monkeypatch):
    from utils import ai_matching

    monkeypatch.setattr(ai_matching, "get_embedding_model", lambda: None)
    jobs = [
        {"id": "1", "title": "Python Engineer", "location": "Remote", "job_type": "Full-time", "skills": "Python", "description": "Python"},
        {"id": "1", "title": "Python Engineer duplicate", "location": "Remote", "job_type": "Full-time", "skills": "Python", "description": "Python"},
        {"id": "2", "title": "SQL Analyst", "location": "On-site", "job_type": "Contract", "skills": "SQL", "description": "SQL"},
    ]
    ranked = match_jobs({"skills": "Python"}, jobs)
    assert ranked[0]["id"] == "1"
    filtered = recommend_jobs({"skills": "Python"}, jobs, {"location": "Remote", "job_type": "Full-time"})
    assert len(filtered) == 1
    try:
        match_jobs({"resume_text": "x" * 50001}, jobs)
    except AgentInputError:
        pass
    else:
        raise AssertionError("oversized profile input was accepted")


def test_agent_job_input_is_validated_and_resume_text_is_not_echoed():
    from app.services.agent_operations import analyze_resume

    parsed = analyze_resume({"resume_text": "Asha Rao\nasha@example.com\nPython engineer"})
    assert "text" not in parsed["resume"]
    try:
        match_jobs({"skills": "Python"}, [{"description": ["unexpected list"]}])
    except AgentInputError:
        pass
    else:
        raise AssertionError("invalid job field was accepted")
    try:
        match_jobs({"skills": "Python"}, [{"application_url": "javascript:alert(1)"}])
    except AgentInputError:
        pass
    else:
        raise AssertionError("unsafe application URL was accepted")


def test_job_provider_normalization_and_missing_credential_behavior():
    mock = MockJobProvider([{"title": "Engineer", "company": "Acme", "location": "Remote", "description": "Python", "application_url": "https://example.com/apply"}])
    assert len(mock.search({"query": "engineer"})) == 1
    normalized = normalize_job(mock.jobs[0], "mock")
    assert normalized["source"] == "mock"
    try:
        normalize_job({**mock.jobs[0], "application_url": "javascript:alert(1)"}, "mock")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe provider application URL was accepted")
    try:
        AgensiProvider().search({})
    except ProviderUnavailable:
        pass
    else:
        raise AssertionError("unconfigured provider appeared operational")


def test_agent_job_search_deduplicates_and_maps_provider_failures():
    job = {
        "id": "role-1", "title": "Engineer", "company": "Acme",
        "location": "Remote", "description": "Python", "application_url": "https://example.com/apply",
    }
    assert len(search_jobs(MockJobProvider([job, job]), {"role": "engineer"})) == 1

    class BrokenProvider:
        def search(self, _query):
            raise TimeoutError("provider timeout")

    try:
        search_jobs(BrokenProvider(), {"role": "engineer"})
    except ProviderUnavailable:
        pass
    else:
        raise AssertionError("provider timeout was not mapped")

    try:
        search_jobs(MockJobProvider(), {"salary": float("nan")})
    except AgentInputError:
        pass
    else:
        raise AssertionError("non-finite query value was accepted")
