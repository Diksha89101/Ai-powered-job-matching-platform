# AI-Powered Job Matching Platform

A Flask + MongoDB recruitment platform for job seekers, recruiters, and administrators. It preserves the existing server-rendered Jinja2 and vanilla JavaScript application while providing resume upload, job and application workflows, resume-derived skills, hybrid job relevance scoring, and role-protected APIs.

## Problem statement

Keyword-only matching misses candidates and roles that describe similar work with different wording. This platform combines normalized skill overlap with optional Sentence Transformer similarity and presents the evidence used in recommendations. A match score is a relevance signal; it is not a hiring probability or a measured accuracy score.

## Features and current status

### Existing application workflows

- Job seeker, recruiter, and administrator registration/login and dashboards.
- Job posting and management, job browsing, applications/withdrawal, recruiter applicant review, notifications, and administrator operations.
- JWT bearer authentication with short-lived access tokens, refresh token rotation/revocation, role checks, and rate limits.
- Private resume upload/download and protected profile images.

### Resume parsing

The existing parser extracts text from PDF, DOCX, TXT/MD, and legacy DOC files. PDF OCR is attempted when optional OCR dependencies are available. Supported uploads are signature-checked, size-limited, assigned random names, and stored outside the Flask static directory. The profile parser recognizes email, phone-like text, a conservative first-line name, common section headings, normalized skills, and explicitly stated year counts. The result is heuristic: it does not guarantee correct work-history dates, qualifications, or inferred experience. Check extracted data before relying on it.

### Matching and explanations

The lower-level matcher in `utils/ai_matching.py` uses normalized skill overlap and optional `all-MiniLM-L6-v2` sentence embeddings. The intelligence service adds semantic, skill, title-wording, and experience-signal components with normalized configurable weights. When the transformer is unavailable, the hybrid service uses explicit skill overlap as its fallback. The seeker recommendation and recruiter applicant flows use the same service. No representative labeled dataset or benchmark is included, so no accuracy claim is made.

Job detail cards display the relevance score, matched/missing skills, score factors and evidence-based reasons when available. Education relevance remains unknown unless a later parser can extract comparable evidence; the current service does not claim an education match.

### Skill gaps and career intelligence

The job-seeker dashboard displays deterministic profile-conditioned career suggestions, and protected APIs provide resume fields and per-job skill-gap comparisons. Skill-gap output includes present/missing skills, a small taxonomy, priorities, and learning topics. Career outputs rank actual database job records and derive learning/interview priorities from their missing skills. There is no course catalog or external LLM integration; suggestions are heuristics and do not promise employment.

### Job discovery

`app/services/job_discovery.py` defines a provider boundary, record normalizer, and deterministic mock provider. `AgensiProvider` is deliberately unavailable until its official API contract, permissions, authentication and credentials can be verified. There is no operational live job-board provider in this checkout. The mock provider is for tests only and must not be presented as live inventory.

### Agent operations

`app/services/agent_operations.py` exposes local Python operations for resume analysis, job search/matching, recommendations, skill gaps, job-description comparison, and career options. `docs/agent/SKILL.md` specifies inputs, outputs, privacy, errors and limitations. These functions are not a public API or MCP server: the caller must authenticate the person and authorize access to profile/resume data before calling them. Do not pass private resume content to external tools.

### Generative Icons

Generative Icons is not integrated in this checkout. The official documentation describes a vanilla JavaScript runtime and dotLottie assets, but the CLI/package installation could not be completed in this workspace, and no icons are shipped by the repository. This is an explicit limitation, not a working UI feature. Before integrating, follow the official [package guide](https://generativeicons.com/docs/using-packages/), [icon file guide](https://generativeicons.com/docs/using-icons/), and [third-party notices](https://generativeicons.com/third-party-notices/); select only needed assets, include required notices, support reduced motion, and keep static fallbacks.

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python, Flask |
| Database | MongoDB, PyMongo/Flask-PyMongo |
| Authentication | PyJWT, Werkzeug password hashing, refresh session records |
| Parsing | pypdf, PyMuPDF; optional OCR runtime |
| NLP and matching | spaCy, normalized skill aliases, optional Sentence Transformers |
| Frontend | Flask/Jinja2, HTML, CSS, vanilla JavaScript, Bootstrap where used |
| Tests | pytest with mock collections and synthetic documents |

## Architecture

```text
Browser (Jinja2 + CSS + vanilla JavaScript)
                    |
              Flask app.py
       auth / API validation / HTML routes
           /             |             \
  MongoDB collections  services      private files
  users, jobs,        matching,        resume and
  applications,      parser, career     profile media
  notifications       agent/provider
```

This is a modular monolith. `app.py` remains the application entry point; service modules hold reusable logic. MongoDB remains the source of truth. No React migration or separate agent application is used.

## Repository layout

- `app.py` — Flask routes, authentication boundaries, database orchestration, and page routes.
- `app/config.py`, `app/security.py` — environment configuration and validation helpers.
- `app/services/` — auth, storage, parsing, matching, intelligence, job discovery and agent operations.
- `utils/ai_matching.py` — current text extraction, skill normalization, embedding and low-level match logic.
- `templates/`, `static/` — Jinja pages, CSS, JavaScript and static images.
- `migrations/` — explicit MongoDB/file migration commands.
- `tests/` — matching, intelligence, auth, upload and API workflow tests.
- `docs/agent/SKILL.md` — local agent operation contract.
- `docs/UPGRADE_AUDIT.md` — audit, baseline result and current progress/gaps.

## Installation

### Requirements

- Python 3.10 or newer.
- MongoDB locally or a MongoDB Atlas connection string.
- Git.

### Create an environment and install dependencies

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

The first use of semantic matching may download the configured Sentence Transformer model. OCR dependencies are optional at runtime but included in the full requirements file.

## Environment configuration

Copy `.env.example` to `.env`. Set `MONGO_URI` and a strong random `SECRET_KEY`; leave mail fields unset unless password-reset email is configured. Generate the secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Process environment variables take precedence over `.env`. The app rejects missing, placeholder, short, or low-variation secrets. Never commit `.env` or real credentials. `.env.example` is the configuration template.

Key settings include:

- `MONGO_URI` — local MongoDB URI or Atlas connection URI.
- `SECRET_KEY` — Flask/JWT signing secret.
- `FLASK_ENV` — set to `development` for local-only reset-link behavior; use `production` behind HTTPS in deployment.
- `ACCESS_TOKEN_TTL_MINUTES`, `REFRESH_TOKEN_TTL_DAYS` — auth lifetimes.
- `RATELIMIT_STORAGE_URI` — `memory://` for one-process development; use shared storage such as Redis across workers.
- `CORS_ORIGINS` — comma-separated allowed origins; configure explicitly when a separate frontend needs API access.
- `SEMANTIC_MODEL_NAME`, `SEMANTIC_WEIGHT`, `KEYWORD_WEIGHT` — lower-level matcher model and weights. `HYBRID_SEMANTIC_WEIGHT`, `HYBRID_SKILLS_WEIGHT`, `HYBRID_TITLE_WEIGHT`, and `HYBRID_EXPERIENCE_WEIGHT` configure the higher-level component weights (defaults 0.45/0.40/0.10/0.05); weights are normalized automatically, and invalid values fall back to defaults. When embeddings are unavailable, hybrid scoring falls back to explicit skill overlap.
- `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USE_SSL`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_DEFAULT_SENDER` — optional SMTP configuration.

## Database setup

Start a local MongoDB service and set `MONGO_URI=mongodb://localhost:27017/jobplatform`, or set `MONGO_URI` to an Atlas URI with appropriate network and database permissions. The app uses existing collections for users/role accounts, jobs, applications, notifications, refresh sessions, and activity logs. No collection redesign is required for the current changes.

Two explicit migration commands exist. Back up MongoDB and uploaded files before running them:

```powershell
python -m migrations.migrate_private_profile_images
python -m migrations.create_refresh_session_ttl_index
```

The profile-image command copies old public images to private storage and reports skipped files. Verify references and backups before the optional `--remove-public-copies` cleanup. The refresh-session TTL index is cleanup only; auth still validates expiry and revocation itself. Do not run migrations against production before reviewing their output and recovery procedure.

Create the first administrator only through the explicit CLI command after setting `ADMIN_EMAIL` and a strong `ADMIN_PASSWORD`:

```powershell
python -m flask --app app.py seed-admin
```

## Run the application

```powershell
python app.py
```

The development server listens on port 5000. Do not use Flask's development server as a production WSGI server. This repository currently has no Docker/Compose or production WSGI configuration; deployment requires a production server, TLS termination, secret management, shared rate-limit storage, MongoDB backups, and private-upload persistence to be configured separately.

## HTTP API overview

Protected endpoints use `Authorization: Bearer <access-token>`. Exact request field validation and response shapes are in the route implementations in `app.py`; this list is an overview, not a generated OpenAPI specification.

| Area | Routes |
|---|---|
| Authentication | `/api/auth/register`, `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout`, `/api/auth/me`, password reset routes |
| Jobs and applications | `/api/jobs`, `/api/jobs/recommended`, `/api/jobs/<job_id>/apply`, `/api/jobs/<job_id>/unapply`, `/api/jobs/applied` |
| Recruiter | `/api/recruiter/jobs`, `/api/recruiter/jobs/<job_id>/applicants`, `/api/recruiter/applicants` |
| User data | `/api/users/profile`, `/api/users/account`, owner/recruiter-authorized resume and profile-image download routes |
| Intelligence | `/api/users/resume/analysis`, `/api/jobs/<job_id>/skill-gaps`, `/api/career/insights` |
| Admin and notifications | `/api/admin/*`, `/api/notifications` |

The resume-analysis, skill-gap, career and recommendation routes enforce job-seeker role checks. Recruiter resume downloads require the candidate to have applied to a job owned by that recruiter. The local agent service functions are not HTTP routes.

## Security and privacy

- Strong environment-only signing secret; password hashing; access-token expiry and refresh rotation/revocation.
- Rate limits on sensitive auth operations; configure shared limiter storage across workers.
- Restrictive CORS, secure cookie settings in production, CSP report-only by default, and security headers.
- Resume/image content signature and size validation, opaque filenames, private storage outside `static/`, and role/ownership checks on download.
- Resume text is removed from normal user serialization and recruiter application response objects; avoid logging uploaded content.
- Account deletion removes the user's role/base records, uploaded resume, applications, refresh sessions and received notifications. Recruiter deletion also removes owned jobs and their applications. Activity-log retention and Mongo/file deletion are not transactional; use backup and retention policy controls appropriate to your deployment.
- `.env` and user upload directories are excluded by `.gitignore`.

No automatic retention schedule, consent workflow for external LLM processing, or live external AI service is implemented. Resume data is not sent to an external AI service by this application.

## Automated tests

Run all tests with:

```powershell
python -m pytest -q
```

Tests use fake Mongo collections, synthetic PDFs/DOCX, and mocked embedding/provider behavior. No paid API key, production MongoDB, SMTP, or live job site should be required. Test areas cover aliases, hybrid scoring fallback, skill gaps, resume parsing, agent validation, provider normalization/unavailable behavior, auth, upload signatures, ownership, deletion, and a core seeker/recruiter workflow. Coverage is not yet exhaustive for every route, UI browser behavior, external provider failure mode, or deployment setup.

GitHub Actions runs pytest on Python 3.11 for pushes and pull requests.

## Deployment and operational limitations

- No production WSGI server configuration, container build, health/readiness route, or infrastructure manifest is included yet.
- Local `memory://` rate limiting is process-local; production multi-worker deployments need shared storage.
- Matching quality has no labeled evaluation dataset or calibrated accuracy measure.
- OCR quality/runtime depends on optional native dependencies and document quality.
- Agensi/live job discovery requires official API details, permission and credentials; it is not operational.
- Generative Icons assets/runtime are not integrated; see the status and official docs above.
- Career intelligence uses deterministic heuristics and a bounded sample of real jobs, not an external LLM or learning-resource catalog.
- Agent operations are local Python functions, not a remote authenticated agent protocol.
- Account deletion does not use a cross-collection transaction; backups and a defined retention policy remain operational responsibilities.

## Future improvements

- Verify an authorized live job-data provider contract and add a timeout/rate-limit-aware adapter.
- Add an authenticated transport for the local agent operations if a remote agent workflow is required.
- Build and publish a representative, consent-safe labeled matching dataset and methodology before evaluating/tuning ranking quality.
- Complete route and browser tests for skill gaps, career insights, filters, recruiter flows, and responsive accessibility.
- Integrate selected Generative Icons locally after the official package/assets can be installed reproducibly; include reduced-motion behavior, static fallbacks, and third-party notices.
- Add a production WSGI/container and health/readiness configuration after deployment requirements are known.
## Screenshots

Existing UI images are available under `Website_images/`. Keep published screenshots representative of the current app and do not use fictional sample jobs as live inventory.

## License

The repository includes a root `LICENSE` file for the platform. Third-party assets and packages retain their own licenses; add their notices before integrating them.
