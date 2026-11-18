# AI-Powered Job Matching Platform

An AI-assisted recruitment platform built with Flask and MongoDB. It supports job seekers, recruiters, and administrators, with resume parsing and a hybrid matching engine that combines explicit skill matching with semantic vector similarity.

## Problem Statement

Traditional keyword-only matching can miss relevant candidates when a resume and job description use different wording for similar experience. This project addresses that gap by combining normalized skill extraction with semantic embeddings.

## Key Features

- Hybrid AI matching: semantic similarity plus explicit skill overlap.
- Resume parsing for PDF, DOCX, TXT/MD and legacy DOC files, with OCR fallback when optional OCR dependencies are available.
- Skill normalization for aliases such as JS to JavaScript, Fast API to FastAPI, and Mongo DB to MongoDB.
- Candidate job recommendations ranked by hybrid match percentage.
- Recruiter applicant ranking using the same matching signals.
- Role-based job seeker, recruiter and admin workflows.
- Job application management backed by MongoDB.
- JWT-based authentication and Flask JSON APIs.

## Matching Workflow

Resume -> text extraction/OCR -> skill extraction and normalization -> candidate semantic profile
Job title + description + skills -> job semantic profile
Candidate and job profiles -> Sentence Transformer embeddings -> cosine similarity
Explicit normalized skill overlap + semantic similarity -> hybrid match percentage -> ranked results

## Semantic Matching

The application uses sentence-transformers with the default all-MiniLM-L6-v2 model. Candidate and job profile text are converted into vector embeddings and compared using cosine similarity.

The default hybrid score is:
- 60% semantic similarity
- 40% normalized skill match

The weights and model can be changed through environment variables:
SEMANTIC_MODEL_NAME=all-MiniLM-L6-v2
SEMANTIC_WEIGHT=0.60
KEYWORD_WEIGHT=0.40

The API exposes the component scores as well as the final matchPercentage, which makes the ranking easier to inspect during development and interviews.

Important: a match percentage is a relevance score, not an accuracy measurement. An accuracy claim requires a labeled evaluation dataset and documented evaluation methodology.

## Architecture

Browser
  |
  v
Flask API + Jinja templates
  |
  +-- Authentication / JWT
  +-- Job and application APIs
  +-- Resume upload and parsing
  |
  v
Matching domain logic
  +-- Skill normalization
  +-- Semantic embeddings
  +-- Hybrid scoring
  |
  v
MongoDB

## Project Structure

- app.py — Flask routes and application orchestration
- app/config.py — environment-backed configuration
- app/security.py — password and upload validation
- app/services/matching.py — matching service facade
- app/services/resume_parser.py — resume parsing service facade
- requirements.txt — Python dependencies
- utils/ai_matching.py — matching implementation and semantic embeddings
- templates/ — Jinja2 pages
- static/ — CSS, JavaScript and frontend assets
- uploads/ — local upload directory
- .env.example — environment configuration template

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| Database | MongoDB, PyMongo |
| Authentication | JWT |
| Resume processing | PyPDF2, PyMuPDF |
| NLP / skill extraction | spaCy + rule-based normalization |
| Semantic matching | Sentence Transformers, all-MiniLM-L6-v2 |
| Frontend | HTML, CSS, JavaScript, Bootstrap |
| API | Flask JSON endpoints |

## Setup

### Prerequisites

- Python 3.9+
- MongoDB
- Git

### 1. Clone

git clone https://github.com/Diksha89101/Ai-powered-job-matching-platform.git
cd Ai-powered-job-matching-platform

### 2. Create a virtual environment

Windows PowerShell:
python -m venv venv
venv\Scripts\Activate.ps1

macOS/Linux:
python3 -m venv venv
source venv/bin/activate

### 3. Install dependencies

python -m pip install --upgrade pip
pip install -r requirements.txt

The first semantic-matching run may download the configured Sentence Transformer model into the local model cache.

### 4. Configure environment variables

Create a `.env` file based on `.env.example` and configure MongoDB, a generated
Flask secret, mail settings, and optional semantic-matching settings. Generate
`SECRET_KEY` with `python -c "import secrets; print(secrets.token_urlsafe(48))"`;
the application rejects placeholder, short, and low-variation secrets. Existing
process environment values take precedence over `.env`.

For local testing without SMTP, set `ALLOW_DEV_RESET_LINK=True` only when
`FLASK_ENV=development`; reset links are never returned in production.

SEMANTIC_MODEL_NAME=all-MiniLM-L6-v2
SEMANTIC_WEIGHT=0.60
KEYWORD_WEIGHT=0.40

### 5. Start MongoDB

Make sure your local MongoDB instance is running and the application's configured database is accessible.

### 6. Run

python app.py

Open the local address printed by Flask.

## API Matching Response

Recommendation and recruiter applicant endpoints expose component scores such as:

matchPercentage: final hybrid score
keywordMatchPercentage: explicit skill overlap
semanticSimilarity: cosine similarity from 0 to 1
semanticMatchPercentage: semantic score converted to 0 to 100
matchedSkills: normalized skills found in both profiles
missingSkills: required matching skills not found in the candidate profile

The numerical values above are response fields, not a benchmark result.

## Security and Engineering Notes

- Secrets are loaded from environment variables; there is no production fallback secret.
- Login and registration return the compatible `token` and `user` JSON fields.
  Access tokens expire after `ACCESS_TOKEN_TTL_MINUTES`; refresh tokens rotate
  through an HttpOnly, SameSite cookie and require a CSRF header. Existing
  seven-day bearer tokens continue to work until their original expiry.
- Login, registration, password-reset, refresh, and test-email endpoints are
  rate-limited. Use shared rate-limit storage (for example
  `RATELIMIT_STORAGE_URI=redis://...`) when deploying multiple web workers;
  `memory://` is intended for local, single-process development.
- CORS is restricted through the CORS_ORIGINS environment variable instead of allowing every origin.
- Login, password-reset requests, and password-reset submission are rate-limited.
- Passwords require at least 8 characters plus uppercase, lowercase, and a number.
- Resume files and profile images are stored outside the public static directory,
  assigned opaque filenames, checked against file signatures and size limits,
  and served through authenticated endpoints. Recruiters can download a resume
  only when the candidate applied to one of their jobs; profile images are
  owner/admin-only.
- Security response headers are enabled by default. CSP starts in report-only
  mode because some existing pages still contain inline scripts and styles.
- No admin account or demo recruiter/jobs are seeded at application startup.
  Provision the first administrator explicitly with
  `python -m flask --app app.py seed-admin` after setting `ADMIN_EMAIL` and a
  strong `ADMIN_PASSWORD`.
- Frontend API data is escaped before being inserted into HTML in the main recruiter/seeker views.
- Runtime logs and uploaded user data are excluded from Git.
- The application keeps the existing Flask entry point while shared configuration, security, matching, and resume parsing responsibilities are being moved into dedicated modules.

## Testing

Run the automated tests with:

```bash
pytest -q
```

The test suite covers password validation, upload validation, token and
authorization behavior, security headers, API input validation, skill alias
normalization, keyword matching, and profile construction.

GitHub Actions runs the test suite on pushes and pull requests.

### Phase 1 data migrations

Back up MongoDB and uploaded files before migrating production data. Set the
same production environment variables used by the app, then run these explicit,
repeatable commands from the repository root:

```bash
python -m migrations.migrate_private_profile_images
python -m migrations.create_refresh_session_ttl_index
```

The profile-image migration copies old files from `static/profile-images/` to
private storage and updates user records only after each copy succeeds. It
reports missing files and leaves originals in place for rollback; remove old
public copies only after verifying migrated records and backups. The TTL index
cleans up expired refresh-session records; token validity is still checked
against the stored expiration and revocation state even if the index is not
present.

After confirming no users still reference the old public paths and taking a
backup, remove the legacy static copies while retaining a private rollback copy:

```bash
python -m migrations.migrate_private_profile_images --remove-public-copies
```

## Screenshots

The repository includes existing UI assets under `static/images/`. Add current application screenshots here when publishing a final portfolio version, ideally covering:

1. Job seeker dashboard and match scores
2. Job browsing with matched/missing skills
3. Recruiter applicant ranking
4. Job posting workflow

## Development Notes

- The semantic model is loaded lazily and cached in-process instead of being initialized for every comparison.
- Existing keyword matching remains available as a fallback if the semantic dependency or model cannot be loaded.
- Profile text is bounded before embedding to avoid unnecessarily large inference inputs.
- The project uses a modular-monolith structure, with matching logic separated from Flask route definitions in utils/ai_matching.py.

## License

The repository currently describes the project as MIT-licensed. Add the corresponding LICENSE file before presenting the repository as formally licensed.
