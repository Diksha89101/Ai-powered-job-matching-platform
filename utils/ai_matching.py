import os
import re
import zipfile
import math
from typing import Dict, Iterable, List
from xml.etree import ElementTree

try:
    from PyPDF2 import PdfReader
except Exception:  # pragma: no cover - fallback when dependency is unavailable
    PdfReader = None

try:
    import spacy
except Exception:  # pragma: no cover - spaCy is optional at runtime
    spacy = None

try:
    import fitz
except Exception:  # pragma: no cover - OCR PDF rendering is optional at runtime
    fitz = None

try:
    from rapidocr_onnxruntime import RapidOCR
except Exception:  # pragma: no cover - OCR is optional at runtime
    RapidOCR = None

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - semantic matching is optional until dependencies are installed
    SentenceTransformer = None


SKILL_ALIASES: Dict[str, List[str]] = {
    "python": ["python", "py"],
    "django": ["django"],
    "flask": ["flask"],
    "fastapi": ["fastapi", "fast api"],
    "java": ["java"],
    "spring boot": ["spring boot", "springboot"],
    "javascript": ["javascript", "js"],
    "typescript": ["typescript", "ts"],
    "react": ["react", "react.js", "reactjs"],
    "angular": ["angular", "angular.js", "angularjs"],
    "vue": ["vue", "vue.js", "vuejs"],
    "node.js": ["node", "node js", "node.js", "nodejs"],
    "express.js": ["express", "express js", "express.js", "expressjs"],
    "html": ["html", "html5"],
    "css": ["css", "css3"],
    "sass": ["sass", "scss"],
    "tailwind css": ["tailwind", "tailwind css"],
    "bootstrap": ["bootstrap"],
    "mysql": ["mysql"],
    "postgresql": ["postgresql", "postgres", "postgre sql"],
    "sql": ["sql", "structured query language"],
    "mongodb": ["mongodb", "mongo db", "mongo"],
    "redis": ["redis"],
    "oracle": ["oracle", "oracle db"],
    "sqlite": ["sqlite", "sqlite3"],
    "firebase": ["firebase"],
    "rest api": ["rest api", "restapi", "restful api", "restful services", "api development"],
    "graphql": ["graphql", "graph ql"],
    "microservices": ["microservices", "micro services"],
    "git": ["git"],
    "github": ["github", "git hub"],
    "docker": ["docker", "containerization"],
    "kubernetes": ["kubernetes", "k8s"],
    "ci/cd": ["ci/cd", "ci cd", "continuous integration", "continuous delivery"],
    "aws": ["aws", "amazon web services"],
    "azure": ["azure", "microsoft azure"],
    "gcp": ["gcp", "google cloud", "google cloud platform"],
    "linux": ["linux"],
    "c": [" c "],
    "c++": ["c++", "cpp"],
    "c#": ["c#", "c sharp"],
    ".net": [".net", "dotnet", "asp.net", "asp net"],
    "php": ["php"],
    "laravel": ["laravel"],
    "ruby": ["ruby"],
    "ruby on rails": ["ruby on rails", "rails"],
    "pandas": ["pandas"],
    "numpy": ["numpy"],
    "scikit-learn": ["scikit-learn", "scikit learn", "sklearn"],
    "tensorflow": ["tensorflow", "tensor flow"],
    "pytorch": ["pytorch", "py torch"],
    "machine learning": ["machine learning", "ml"],
    "deep learning": ["deep learning", "dl"],
    "data analysis": ["data analysis", "data analytics"],
    "power bi": ["power bi", "powerbi"],
    "tableau": ["tableau"],
    "excel": ["excel", "microsoft excel"],
    "spark": ["spark", "apache spark"],
    "hadoop": ["hadoop"],
    "kafka": ["kafka", "apache kafka"],
    "selenium": ["selenium"],
    "pytest": ["pytest", "py test"],
    "junit": ["junit", "j unit"],
    "testing": ["testing", "unit testing", "integration testing"],
    "devops": ["devops", "dev ops"],
    "agile": ["agile"],
    "scrum": ["scrum"],
    "jira": ["jira"],
    "figma": ["figma"],
    "ui/ux": ["ui/ux", "ui ux", "user interface", "user experience"],
    "communication": ["communication", "verbal communication", "written communication"],
    "leadership": ["leadership"],
    "problem solving": ["problem solving", "problem-solving"],
    "android": ["android"],
    "ios": ["ios", "i os"],
    "kotlin": ["kotlin"],
    "swift": ["swift"],
}


def _build_alias_index() -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    for canonical, values in SKILL_ALIASES.items():
        aliases[_normalize_keyword(canonical)] = canonical
        for value in values:
            aliases[_normalize_keyword(value)] = canonical
    return aliases


def _normalize_keyword(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip().lower())
    return cleaned.strip(".,;:|")


ALIAS_TO_CANONICAL = _build_alias_index()
SORTED_ALIASES = sorted(ALIAS_TO_CANONICAL.keys(), key=len, reverse=True)


def dedupe_preserve_order(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_skill_list(skill_text: str) -> List[str]:
    if not skill_text:
        return []

    raw_items = re.split(r"[,;/\n\r]+", skill_text)
    normalized: List[str] = []
    for item in raw_items:
        cleaned = _normalize_keyword(item)
        if not cleaned:
            continue
        normalized.append(ALIAS_TO_CANONICAL.get(cleaned, cleaned))
    return dedupe_preserve_order(normalized)


def _pattern_for_phrase(phrase: str) -> re.Pattern:
    escaped = re.escape(phrase)
    escaped = escaped.replace(r"\ ", r"[\s\-_/]+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


ALIAS_PATTERNS = [(_pattern_for_phrase(alias), canonical) for alias, canonical in ALIAS_TO_CANONICAL.items()]
_NLP = None
_OCR_ENGINE = None
_EMBEDDING_MODEL = None
_EMBEDDING_MODEL_NAME = os.getenv("SEMANTIC_MODEL_NAME", "all-MiniLM-L6-v2")
SEMANTIC_WEIGHT = float(os.getenv("SEMANTIC_WEIGHT", "0.60"))
KEYWORD_WEIGHT = float(os.getenv("KEYWORD_WEIGHT", "0.40"))


def get_nlp():
    global _NLP
    if _NLP is not None:
        return _NLP

    if spacy is None:
        return None

    try:
        _NLP = spacy.load("en_core_web_sm")
    except Exception:
        try:
            _NLP = spacy.blank("en")
        except Exception:
            _NLP = None
    return _NLP


def get_embedding_model():
    """Load the sentence-transformer model once and reuse it for matching."""
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is not None:
        return _EMBEDDING_MODEL
    if SentenceTransformer is None:
        return None
    try:
        _EMBEDDING_MODEL = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    except Exception:
        _EMBEDDING_MODEL = None
    return _EMBEDDING_MODEL


def semantic_similarity(candidate_text: str, job_text: str) -> float:
    """Return cosine similarity between candidate and job embeddings as 0..1."""
    if not candidate_text or not job_text:
        return 0.0
    model = get_embedding_model()
    if model is None:
        return 0.0
    try:
        embeddings = model.encode([candidate_text[:12000], job_text[:12000]], normalize_embeddings=True)
        similarity = float(sum(a * b for a, b in zip(embeddings[0], embeddings[1])))
        return max(0.0, min(1.0, similarity))
    except Exception:
        return 0.0


def get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE

    if RapidOCR is None:
        return None

    try:
        _OCR_ENGINE = RapidOCR()
    except Exception:
        _OCR_ENGINE = None
    return _OCR_ENGINE


def tokenize_text(text: str) -> List[str]:
    if not text:
        return []
    return re.findall(r"[a-z0-9][a-z0-9+#.\-]*", text.lower())


def extract_resume_skills_nlp(text: str) -> List[str]:
    if not text:
        return []

    normalized_text = f" {re.sub(r'\s+', ' ', text.lower())} "
    matches: List[str] = []

    # Phrase matching over the whole resume catches multi-word skills reliably.
    for pattern, canonical in ALIAS_PATTERNS:
        if pattern.search(normalized_text):
            matches.append(canonical)

    # Prefer spaCy tokenization/phrases when available, then fall back to regex tokens.
    nlp = get_nlp()
    phrase_candidates: List[str] = []
    tokens: List[str] = []
    if nlp is not None:
        doc = nlp(text)
        tokens = [
            (token.lemma_ or token.text).lower()
            for token in doc
            if not token.is_space and not token.is_punct
        ]
        phrase_candidates.extend(ent.text for ent in getattr(doc, "ents", []))
        try:
            phrase_candidates.extend(chunk.text for chunk in doc.noun_chunks)
        except Exception:
            pass
    else:
        tokens = tokenize_text(text)

    for phrase in phrase_candidates:
        canonical = ALIAS_TO_CANONICAL.get(_normalize_keyword(phrase))
        if canonical:
            matches.append(canonical)

    # Token n-grams provide the final skill normalization pass.
    max_ngram = 4
    for size in range(max_ngram, 0, -1):
        for index in range(len(tokens) - size + 1):
            phrase = _normalize_keyword(" ".join(tokens[index:index + size]))
            canonical = ALIAS_TO_CANONICAL.get(phrase)
            if canonical:
                matches.append(canonical)

    return dedupe_preserve_order(matches)


def extract_keywords_from_text(text: str) -> List[str]:
    return extract_resume_skills_nlp(text)


def build_candidate_profile(skills_text: str = "", resume_text: str = "") -> Dict[str, List[str]]:
    manual_skills = normalize_skill_list(skills_text)
    resume_skills = extract_resume_skills_nlp(resume_text)
    candidate_text = "\n".join(part for part in [skills_text, resume_text] if part).strip()
    return {
        "manual_skills": manual_skills,
        "resume_skills": resume_skills,
        "profile_keywords": resume_skills,
        "semantic_text": candidate_text,
    }


def build_job_profile(skills_text: str = "", title: str = "", description: str = "") -> Dict[str, List[str]]:
    manual_skills = normalize_skill_list(skills_text)
    inferred_keywords = extract_keywords_from_text(" ".join([title or "", description or ""]))
    match_keywords = manual_skills or inferred_keywords
    searchable_keywords = dedupe_preserve_order(manual_skills + inferred_keywords)
    job_text = "\n".join(part for part in [title, description, skills_text] if part).strip()
    return {
        "normalized_skills": manual_skills,
        "inferred_keywords": inferred_keywords,
        "match_keywords": match_keywords,
        "searchable_keywords": searchable_keywords,
        "semantic_text": job_text,
    }


def calculate_keyword_match(
    candidate_keywords: Iterable[str],
    match_keywords: Iterable[str],
    inferred_keywords: Iterable[str] = (),
    candidate_text: str = "",
    job_text: str = "",
) -> Dict[str, object]:
    candidate_list = dedupe_preserve_order(candidate_keywords)
    scoring_keywords = dedupe_preserve_order(match_keywords)
    context_keywords = dedupe_preserve_order(inferred_keywords)
    candidate_set = set(candidate_list)

    matched_skills = [keyword for keyword in scoring_keywords if keyword in candidate_set]
    missing_skills = [keyword for keyword in scoring_keywords if keyword not in candidate_set]
    bonus_keywords = [
        keyword for keyword in context_keywords
        if keyword in candidate_set and keyword not in matched_skills
    ]

    total = len(scoring_keywords)
    match_percentage = int(round((len(matched_skills) / total) * 100)) if total else 0

    semantic_similarity_score = semantic_similarity(candidate_text, job_text)
    semantic_percentage = int(round(semantic_similarity_score * 100))

    # Hybrid scoring keeps explicit skill overlap while adding semantic understanding.
    # If embeddings are unavailable, the existing keyword score remains the fallback.
    if candidate_text and job_text and get_embedding_model() is not None:
        match_percentage = int(round(
            (semantic_percentage * SEMANTIC_WEIGHT) +
            (match_percentage * KEYWORD_WEIGHT)
        ))
        match_percentage = max(0, min(100, match_percentage))

    return {
        "matchPercentage": match_percentage,
        "keywordMatchPercentage": int(round((len(matched_skills) / total) * 100)) if total else 0,
        "semanticSimilarity": round(semantic_similarity_score, 4),
        "semanticMatchPercentage": semantic_percentage,
        "matchedSkills": matched_skills,
        "missingSkills": missing_skills,
        "bonusKeywords": bonus_keywords,
        "candidateKeywords": candidate_list,
        "totalKeywords": total,
    }


def extract_resume_text(file_path: str) -> str:
    extension = os.path.splitext(file_path)[1].lower()
    if extension == ".pdf":
        return _extract_pdf_text(file_path)
    if extension == ".docx":
        return _extract_docx_text(file_path)
    if extension in {".txt", ".md"}:
        return _extract_plain_text(file_path)
    if extension == ".doc":
        return _extract_legacy_doc_text(file_path)
    return ""


def _extract_pdf_text(file_path: str) -> str:
    if PdfReader is None:
        return _extract_pdf_text_with_ocr(file_path)

    try:
        reader = PdfReader(file_path)
        text_parts = []
        for page in reader.pages:
            text_parts.append(page.extract_text() or "")
        extracted_text = _clean_extracted_text("\n".join(text_parts))
        if extracted_text:
            return extracted_text
        return _extract_pdf_text_with_ocr(file_path)
    except Exception:
        return _extract_pdf_text_with_ocr(file_path)


def _extract_pdf_text_with_ocr(file_path: str) -> str:
    if fitz is None:
        return ""

    ocr_engine = get_ocr_engine()
    if ocr_engine is None:
        return ""

    text_parts: List[str] = []
    try:
        with fitz.open(file_path) as document:
            for page in document:
                # 3x rendering gives OCR better text clarity for scanned resumes.
                pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                ocr_result = ocr_engine(pixmap.tobytes("png"))
                page_text = _extract_ocr_text(ocr_result)
                if page_text:
                    text_parts.append(page_text)
        return _clean_extracted_text("\n".join(text_parts))
    except Exception:
        return ""


def _extract_ocr_text(ocr_result) -> str:
    if not ocr_result:
        return ""

    if isinstance(ocr_result, tuple):
        ocr_result = ocr_result[0]

    if not ocr_result:
        return ""

    text_chunks: List[str] = []
    for item in ocr_result:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            text_value = item[1]
            if isinstance(text_value, str):
                text_chunks.append(text_value)
    return " ".join(text_chunks)


def _extract_docx_text(file_path: str) -> str:
    try:
        with zipfile.ZipFile(file_path) as docx_file:
            xml_bytes = docx_file.read("word/document.xml")
        root = ElementTree.fromstring(xml_bytes)
        text_nodes = [node.text for node in root.iter() if node.text]
        return _clean_extracted_text(" ".join(text_nodes))
    except Exception:
        return ""


def _extract_plain_text(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as file_obj:
            return _clean_extracted_text(file_obj.read())
    except Exception:
        return ""


def _extract_legacy_doc_text(file_path: str) -> str:
    try:
        with open(file_path, "rb") as file_obj:
            raw_text = file_obj.read().decode("latin-1", errors="ignore")
        return _clean_extracted_text(raw_text)
    except Exception:
        return ""


def _clean_extracted_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[^\S\r\n]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()
