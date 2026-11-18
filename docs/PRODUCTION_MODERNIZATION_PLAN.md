# Production Modernization Engineering Plan

## Purpose and constraints

Modernize the existing Flask, MongoDB, and semantic job-matching application
incrementally, without replacing working workflows or unnecessarily changing its
public API. The plan is based on the source audit of the current working tree:

- Flask routes and most application orchestration are in `app.py`.
- Environment configuration is in `app/config.py`; password and upload-name helpers
  are in `app/security.py`.
- `app/services/matching.py` and `app/services/resume_parser.py` currently re-export
  functions implemented in `utils/ai_matching.py`.
- MongoDB access is direct from route handlers. The existing external JSON routes
  cover authentication, jobs, applications, profile, recruiter, admin, and
  notifications.
- The frontend is Jinja templates, vanilla JavaScript, and CSS. Some pages have
  independent inline rendering logic.
- Existing tests are `tests/test_matching.py` and `tests/test_security.py`; the
  existing workflow is `.github/workflows/tests.yml`.
- The existing API surface includes `/api/auth/*`, `/api/jobs`,
  `/api/jobs/recommended`, `/api/jobs/<job_id>/apply`, recruiter applicant
  routes, user profile and file routes, admin routes, and notifications. Keep
  these route names and response contracts as the compatibility baseline.
- `utils/ai_matching.py` currently loads the sentence-transformer model lazily,
  computes a hybrid score with environment-provided weights, and uses fallback
  behavior when model loading or inference fails. `requirements.txt` contains
  the NLP, OCR, and embedding stack; there is no queue, cache, or vector-store
  integration to preserve.
- The database access layer has no explicit versioned migration/index runner.
  Treat collection/index assumptions as unverified until inventory and
  duplicate reports are complete.
- Configuration currently loads `.env` with override behavior, while auth
  issuance and validation are still implemented in `app.py`; current login and
  registration tokens use a seven-day expiry despite configured access-token
  TTL settings. The route-level active-account policy and all token consumers
  need to be inventoried before changing token semantics.
- Semantic model load/inference failures currently collapse to a zero semantic
  score. Preserve the keyword fallback but make its active/degraded state
  observable and test both modes.
- `templates/browse_jobs.html` currently falls back to fabricated example jobs
  when the API errors or returns no usable jobs. It also builds cards with
  `innerHTML` and inline event handlers. Phase 6 must replace this with explicit
  loading, empty, and error states and safe DOM construction; fake jobs must
  never appear as live production records or be actionable.

The repository already has substantial staged, unstaged, and untracked changes.
Before implementation, review and preserve those changes; do not treat the
working tree as a clean baseline or overwrite developer work. This document is a
plan only. No code, dependencies, or runtime configuration are changed by
creating it.

## Design principles

1. Keep the current modular-monolith deployment and Flask entry point until a
   measured need justifies a larger topology change.
2. Keep MongoDB as the source of truth for users, jobs, applications, and
   notifications. Derived AI indexes and caches must be rebuildable.
3. Keep existing API paths and response shapes by default. Add optional fields
   or new versioned endpoints rather than silently changing existing consumers.
4. Make external infrastructure optional and configured explicitly. The app
   must continue to support keyword-only matching and local development without
   a remote vector provider, error tracker, or worker.
5. Add migrations that are idempotent, observable, and reversible where safe.
   Never perform destructive cleanup of user data as an implicit startup action.
6. Add tests around each behavior change before rollout. Separate development
   fixtures from production data and credentials.
7. Prefer a small service abstraction around real requirements over generic
   frameworks or premature microservices.

## Target architecture

```text
Browser: Jinja pages + accessible vanilla JS/CSS
                  |
        Existing Flask app.py entry point
     (introduce a factory only if test isolation
      or deployment configuration requires it)
     auth / validation / HTTP compatibility
                  |
       application services and policies
       /          |            \
 MongoDB      matching       private storage
 source of     service       and parser
 truth            |              |
                  +--- VectorStore adapter
                       (optional derived index)
                  |
         optional Redis-backed queue/cache
         optional worker for expensive tasks

Cross-cutting: structured logs, request IDs, health/readiness,
metrics/error reporting behind configuration.
```

The target is not a mandatory distributed system: a single Flask process,
MongoDB, keyword fallback, and local/test adapters remain supported.

## Phase dependency map

```text
Phase 7 (test/CI foundation) ──┬──> Phase 1 (security)
                               ├──> Phase 2 (matching)
                               ├──> Phase 6 (frontend)
                               └──> all later verification

Phase 1 ──> Phase 4 (private uploads and async work)
       ├──> Phase 5 (account/job data constraints)
       └──> Phase 8 (safe logging and request identity)

Phase 2 ──> Phase 3 (vector indexing and evaluation semantics)
       ├──> Phase 4 (embedding/parser tasks)
       └──> Phase 5 (matching cache keys/invalidation)

Phase 3 ──> Phase 4 (optional vector-index worker task)
       └──> Phase 5 (optional vector/cache coordination)

Phase 1 + Phase 7 ──> Phase 6
Phase 1 + Phase 7 ──> Phase 8
Phases 1–8 ──> Phase 9 production deployment; compose scaffolding may start
               earlier but is not production acceptance.
```

Phase 7 is listed as Phase 7 per the requested organization, but its minimal
test/CI baseline should be established before changing security-sensitive
behavior. Its later work—coverage expansion and optional evaluation CI—continues
alongside the preceding phases.

## Cross-phase API and data migration policy

### Public API compatibility

- Preserve existing paths, HTTP methods, authorization behavior (except when
  tightening unsafe access), and existing response fields.
- Add response fields additively. Keep the current `matchPercentage`,
  `keywordMatchPercentage`, `semanticSimilarity`, `semanticMatchPercentage`,
  `matchedSkills`, and `missingSkills` fields when their existing endpoints are
  used.
- Add cursor pagination as opt-in initially. If an endpoint currently returns a
  JSON array, continue returning that array when pagination parameters are
  absent; introduce a documented paged response only by opt-in or a new
  `/api/v2/...` route. Confirm each existing frontend before changing defaults.
- Keep bearer access-token support during the refresh-token transition. Do not
  require a client update until the new flow is available and the deprecation
  window has been communicated.
- Tightened authorization (especially private resume access and test-email
  access) may deny requests that were previously accepted. Treat these as
  intentional security corrections; update clients/admin workflows and provide
  release notes rather than weakening checks to preserve unsafe behavior.

### Database migration principles

- Add versioned migration scripts or commands that can be run repeatedly and
  report their changes. Avoid schema changes from arbitrary web requests.
- Deploy additive indexes/fields first. Backfill in bounded batches; verify
  counts and uniqueness before enabling stricter writes.
- For unique user email and application constraints, first report and resolve
  existing duplicates; then create unique indexes; finally map duplicate-key
  errors to stable API responses.
- Store explicit schema/index migration version in a migration collection or
  equivalent deployment metadata. Do not assume a migration has run because
  the application started.
- Treat FAISS indexes and caches as derived artifacts: version, rebuild, and
  atomically replace them; never make them the only copy of canonical data.
- For any model upgrade, version embeddings by model name/revision and avoid
  comparing vectors generated by incompatible model versions.

### Rollout and rollback principles

- Use feature flags/configuration for refresh enforcement, remote vector stores,
  queues, Redis cache, and optional monitoring services.
- Roll out security controls in compatibility-aware steps; do not ship a
  publicly callable credential/fixture or mail endpoint while rollout is in
  progress.
- Document backup/restore expectations for MongoDB and private uploads before
  production migration.
- Use a canary or staging environment for schema/index, token, storage, and
  model changes. Preserve an application rollback path while keeping additive
  database changes backward compatible.

## Phase 1: Security hardening

### Files to modify

- `app/config.py`, `app.py`, `app/security.py`
- `app/services/resume_parser.py` and `utils/ai_matching.py` as upload/parser
  boundaries are tightened
- `.env.example`, `.gitignore`, relevant templates that present authentication
  or upload state
- Add route/security tests under `tests/`

### New files

- `app/services/auth.py` for token issuance/validation/revocation policy, if
  extraction is needed to keep route handlers thin
- `app/services/storage.py` for private upload naming, validation, and storage
- `migrations/` scripts for token/session and any security-related data changes
- Optional `tests/fixtures/` for harmless synthetic upload examples

### Dependencies

- Reuse Flask, PyJWT, Werkzeug, Flask-Limiter, and existing validation helpers.
- Add a file-signature detection package only if a small, well-supported
  library is required for the accepted document/image formats.
- Use ClamAV through an optional scanner adapter/configuration; do not require a
  remote scanning service for local tests. No encryption library is selected
  until the sensitive fields, key management, and rotation requirements are
  specified.

### Architectural decisions

- Fail startup when the signing secret is missing, known-placeholder, or below
  an agreed entropy/length floor. Production secrets come from deployment
  secrets, not repository defaults.
- Load `.env` for local development without overriding explicitly provided
  process environment configuration.
- Keep JWT access tokens short-lived and add refresh-token rotation and
  revocation. Store only refresh-token digests/identifiers, not bearer refresh
  secrets. Keep legacy access tokens valid only for their existing expiry during
  the migration window; define immediate invalidation rules for account
  disablement and password change.
- Continue accepting the existing API bearer token while introducing refresh
  endpoints. Do not silently move API authentication to cookies. If cookie
  auth is later adopted, add CSRF protection and secure cookie flags.
- Store resumes and private images outside the Flask static root with opaque
  random filenames. Apply authorization at download time. Decide explicitly
  whether profile images are public; default to private until approved.
- Remove runtime demo-account creation and expose development fixtures only
  through a deliberate, development-only seed operation.
- Restrict email test functionality to an authorized operator or remove it
  from production routing.
- Add a baseline CSP/security-header policy compatible with current scripts;
  start in report-only mode if inline scripts/styles or the Tailwind CDN still
  require migration.
- Validate inputs server-side by field, type, length, enum, and ownership.
  Escape user-controlled text at HTML output sinks; context-appropriate output
  encoding remains mandatory even with validation.

### Implementation steps

1. Inventory all API routes, roles, data access, template rendering sinks, and
   upload/download paths. Capture current response shapes and add regression
   tests for existing successful workflows before changing authorization or
   token behavior.
2. Remove hard-coded demo recruiter/admin credentials from runtime paths.
   Replace production-seeded sample jobs with a development-only fixture command.
3. Restrict `/api/test-email`, validate recipient and request type, and ensure
   responses do not disclose SMTP exceptions or reset tokens.
4. Make secret validation strict and unify config loading; document secret
   bootstrap, rotation, and environment-specific options in `.env.example`.
5. Centralize access-token validation; enforce user-active and role checks on
   every protected action. Add refresh rotation/revocation with a staged client
   migration and narrowly scoped token claims.
6. Tighten resume authorization to the owner, admin, or a recruiter with an
   explicit application/job relationship. Audit file access without logging
   file content or tokens.
7. Route all uploads through one bounded pipeline: request-size limit, filename
   generation, allowlisted format, content/signature validation, private
   storage, optional malware scan, and safe parser invocation.
8. Fix API-driven HTML rendering to use DOM `textContent`/safe nodes or a
   consistently tested escaping method. Apply CSRF protection to any
   cookie-authenticated state-changing route if that auth mode is introduced.
9. Add security response headers and CSP; tighten CORS to explicitly configured
   origins and document production proxy/TLS expectations.
10. Add an auditable retention/deletion policy for resumes, profile images,
    reset/refresh tokens, and activity logs. Encrypt selected sensitive fields
    only after key management and query/index implications are resolved.

### Potential breaking changes

- Runtime demo users/jobs disappear outside explicit development seeding.
- `/api/test-email` becomes admin-only or unavailable in production.
- Recruiters without an application relationship can no longer download
  arbitrary resumes.
- Existing uploaded profile-image URLs may change when files move from `static`.
- Previously accepted files with false extensions, oversized pages, or unsafe
  content may be rejected.
- Expired, revoked, or disabled-account tokens may stop working earlier than
  current seven-day token behavior.

Migration: audit legacy files and references; copy to private storage with
verified owner mappings; update user records only after successful copy; retain
rollback backups for the agreed period; remove legacy public copies only after
the migration is verified and approved.

### Testing strategy

- Unit tests for secret validation, token claim validation/rotation/revocation,
  field validators, opaque filenames, signature/MIME mismatches, upload caps,
  and scanner behavior.
- Flask test-client tests for role/ownership matrices, disabled users, test
  email access, reset flow, upload/download authorization, and legacy token
  migration behavior.
- Stored-XSS regression tests for every audited API-to-HTML sink.
- Tests must use synthetic files and mocked MongoDB/mail/scanner integrations;
  no production data, live SMTP, or real user files.

### Acceptance criteria

- No default production credentials, placeholder signing key acceptance, or
  public demo-seed path remains.
- Private uploads cannot be fetched through `/static/`; authorized downloads
  enforce owner/admin/relevant recruiter policy.
- Inactive users are consistently rejected; refresh tokens rotate and can be
  revoked; access tokens remain compatible during the documented migration.
- Authenticated user-controlled strings are rendered as text, not executable
  markup.
- All API changes and intentional authorization denials are documented and
  covered by tests.

## Phase 2: AI matching accuracy and robustness

### Files to modify

- `utils/ai_matching.py`, `app/services/matching.py`, `app.py`
- `app/config.py`, `.env.example`, `README.md`
- `tests/test_matching.py` and matching route tests

### New files

- `app/services/matching_service.py` if the facade evolves into a dependency
  injected service; otherwise extend the existing facade in place
- `evaluation/` containing a small, consent-safe synthetic or licensed labeled
  dataset, scoring script, and methodology
- `tests/test_matching_evaluation.py` or equivalent deterministic unit/eval
  checks

### Dependencies

- Reuse existing sentence-transformers, spaCy, and normalized-skill logic.
- No new dependency is required for the initial evaluation script or alias map.
- Add a fuzzy-matching or NLP package only if baseline evaluation shows a
  specific measurable benefit and false-positive controls are documented.

### Architectural decisions

- Expose a `MatchingService`-style API that accepts candidate/job profile data
  and returns a stable score contract; route handlers should not duplicate
  scoring orchestration.
- Preserve existing response fields and add structured component/explanation
  fields additively.
- Validate that weights are finite, bounded, and normalized, or clearly define
  score behavior when they do not sum to one.
- Include manual skills and resume-derived skills according to an explicit,
  tested policy. The current route path visibly passes resume-derived
  `profile_keywords` into explicit keyword scoring; settle the intended use of
  manual skills rather than assume.
- Make model loading and inference failures observable. A missing model must
  degrade to keyword matching with an explicit internal health state rather
  than an indistinguishable zero semantic score.
- Keep ranking scores labeled as relevance signals, not accuracy or hiring
  probability.
- Use canonical skill IDs only through an additive data contract; preserve
  legacy skill strings during migration.

### Implementation steps

1. Capture current deterministic matching behavior and identify all API
   consumers of the score fields.
2. Add golden examples for alias normalization, empty profiles, missing
   embeddings, invalid weights, duplicate skills, multiword skills, and
   resume/manual-skill interactions.
3. Extract repeated candidate/job scoring into one service used by
   recommendations, recruiter applicants, and application-threshold checks.
4. Make model name/revision, weights, maximum text length, and fallback mode
   validated configuration; preserve sensible existing defaults.
5. Add an evaluation set with relevance labels and clear source/license,
   population, and limitations; implement precision/recall, nDCG, and
   calibration metrics with deterministic keyword-only baselines.
6. Measure semantic and keyword-only results before altering default weights.
   Tune only after documented evaluation and stakeholder review.
7. Add explainability fields: component scores, matched/missing/canonical
   skills, and a bounded list of top contributing phrases. Return no raw
   resumes or sensitive text in explanations.
8. Add an experiment harness using explicit cohort/feature flags. Keep the
   default behavior unchanged until experiment results are reviewed.
9. Add a model-version migration checklist and define when stored embeddings
   must be invalidated/recomputed.

### Potential breaking changes

- Scores or ordering can change if the manual-skill policy, aliases, weights,
  or model revision changes.
- Additive explanation fields increase response size.
- Canonical skill identifiers may add fields but must not remove existing
  string arrays until API versioning/deprecation is approved.

Migration: preserve existing score fields and aliases; add new fields first;
run evaluations side by side; gate changed weights/model by configuration;
announce any changed ranking behavior and retain rollback to the prior model
and weights.

### Testing strategy

- Unit tests for score components, deterministic fallback, configuration
  validation, aliases, and edge cases.
- Golden evaluation checks with bounded tolerances and metric reporting; do not
  assert arbitrary quality thresholds until a reviewed baseline exists.
- API contract tests asserting current fields and types remain available.
- Tests that explanations never include unredacted resume text or PII.

### Acceptance criteria

- All matching entry points use the same tested service and policy.
- The system reports whether semantic inference is active and falls back
  predictably when unavailable.
- A documented labeled evaluation reports precision, recall, nDCG, and
  calibration for a baseline; no unsubstantiated accuracy claims are made.
- Existing score fields remain backward compatible; explanations are
  additive, bounded, and privacy safe.

## Phase 3: Vector store architecture

### Files to modify

- `app/services/matching.py` or new matching service, `utils/ai_matching.py`,
  `app/config.py`, `.env.example`, `app.py`
- `requirements.txt` only if a chosen adapter requires a package

### New files

- `app/services/vector_store.py` defining the minimal vector-store protocol
- `app/services/vector_stores/` with a local FAISS adapter and optional remote
  adapter modules only when selected/implemented
- `migrations/` or a rebuild command for index metadata and embedding versions
- `tests/test_vector_store.py` and adapter-specific tests

### Dependencies

- FAISS CPU is optional and justified for local similarity retrieval once
  evaluation and the use case confirm it is needed.
- Do not add Weaviate/Pinecone SDKs until a deployment selects and operates
  those services. Keep their configuration slots optional; adapter support
  must not be claimed until implemented and tested.

### Architectural decisions

- MongoDB remains authoritative; vector indexes store derived vectors and
  identifiers only.
- Interface supports deterministic upsert/delete/search, model/index version,
  readiness, and rebuild. Search results must be revalidated against current
  MongoDB documents and access policy.
- Default local mode may use in-memory/FAISS when configured; keyword-only
  matching remains the zero-service fallback.
- Persist FAISS artifacts outside the web root with atomic index snapshots and
  a rebuild-from-source path. Avoid pickle/deserialization of untrusted data.
- Never mix vectors from different model revisions in one active index.

### Implementation steps

1. Define retrieval requirements and data scale from Phase 2 evaluation and
   expected workloads. Decide whether vector retrieval is actually warranted.
2. Define `VectorStore` operations and error/fallback contract. Keep matching
   business policy outside provider-specific adapters.
3. Implement and test a local adapter only if the use case needs nearest-neighbor
   retrieval; otherwise ship the interface and keyword/embedding scoring
   without a persisted index.
4. Add explicit index metadata for model/version/dimension and a rebuild
   command. Validate metadata before loading.
5. Add optional remote adapters behind config and separate dependencies only
   after service ownership, authentication, network controls, and cost are
   accepted.
6. Add a reconciliation path to detect and repair missing/stale job/candidate
   vectors.

### Potential breaking changes

- None to existing API responses when vector retrieval is kept internal.
- Ranking/order can change if candidate retrieval prunes the set before the
  current exact matching/ranking stage.
- Index metadata changes may require a full rebuild after model upgrades.

Migration: run old and new retrieval paths in shadow mode; compare recall of
known relevant pairs; rebuild a versioned index; switch behind a flag; retain
the prior index and keyword-only path for rollback.

### Testing strategy

- Contract tests against a fake in-memory store and the selected local adapter.
- Test dimensions, duplicate upsert, deletion, stale/missing index, wrong model
  version, corrupt artifact, and rebuild idempotency.
- Verify API authorization after vector retrieval and confirm no private
  profile/resume content is returned.

### Acceptance criteria

- Store is selected via validated configuration and can fail back safely.
- MongoDB remains the source of truth and the vector index is rebuildable.
- Index metadata prevents incompatible embedding versions from being mixed.
- Optional remote providers are either fully tested integrations or explicitly
  documented as unsupported; no false capability claims.

## Phase 4: Background processing

### Files to modify

- `app.py`, `app/config.py`, `app/services/resume_parser.py`,
  `app/services/matching.py`, `utils/ai_matching.py`
- Profile/upload frontend templates and JavaScript for task progress
- `requirements.txt`, `.env.example`

### New files

- `app/worker.py` and `app/tasks/` for explicit task registration
- `app/services/task_queue.py` interface and local synchronous/test adapter
- `app/services/tasks.py` or task-specific service modules for parsing,
  embedding, and optional vector indexing
- `tests/test_tasks.py`; queue configuration for local/dev environments

### Dependencies

- Add Celery and a Redis client only if asynchronous workload volume/latency
  justifies an operational broker. RQ is an alternative; choose one, not both.
- Redis remains optional for local keyword-only and synchronous test flows.
- Use the Phase 1 scanning/storage integration before processing untrusted
  uploads asynchronously.

### Architectural decisions

- Persist task state/progress and ownership in MongoDB (or another selected
  authoritative store); broker messages are delivery mechanisms, not state.
- Tasks are idempotent, retryable, bounded, and safe under duplicate delivery.
- Preserve the current profile update API initially: return an accepted task
  identifier/progress additively or expose a new async route; do not claim a
  resume is parsed before the task completes.
- Protect progress endpoints by owner/admin policy. SSE may be added after
  polling is sufficient; avoid introducing WebSockets unless a real UX need is
  measured.
- Keep synchronous parser mode available in test/local mode for compatibility.

### Implementation steps

1. Measure current resume parse/embedding latency and failure rates; select only
   work that exceeds the request budget.
2. Define task records, state transitions, retry policy, timeouts, dead-letter
   handling, and idempotency keys.
3. Add a worker adapter and task runner with synchronous test implementation.
4. Move resume extraction and embedding/index work into isolated tasks; keep
   MongoDB writes and profile state transitions consistent.
5. Add task status/progress API with owner authorization; add frontend progress,
   retry messaging, and accessible status announcements.
6. Add queue health/readiness and graceful behavior when broker is unavailable.
7. Add safe cleanup for expired task records and abandoned temporary files.

### Potential breaking changes

- Profile updates may become eventually consistent for parsed skills. Clients
  that expect extracted fields immediately need a migration period or explicit
  sync mode.
- New progress endpoints and task IDs are additive; existing JSON keys remain.
- Queue outages can delay processing, but must not corrupt profile/job data.

Migration: deploy worker and task schema first; keep synchronous processing as
the default; enable async for a controlled cohort; compare completion/failure
rates; then change default only after operational acceptance.

### Testing strategy

- Unit tests for idempotency, task transitions, retry limits, ownership, and
  parser errors.
- Integration tests with a fake queue by default; optional Redis/Celery
  integration tests in a service-enabled CI job.
- Verify task duplication cannot double-write profile state or corrupt vector
  indexes; test broker outage and worker restart.

### Acceptance criteria

- Expensive processing does not block the configured request path when async
  mode is enabled.
- UI shows pending/running/succeeded/failed state without exposing other users’
  tasks.
- Retry and duplicate delivery are safe; broker/worker health is observable.
- Synchronous fallback remains available where explicitly configured.

## Phase 5: Database optimization and caching

### Files to modify

- `app.py`, matching/application services, `app/config.py`
- `.env.example`, `requirements.txt` only if Redis caching is accepted
- API templates and JavaScript if opt-in pagination is introduced

### New files

- `migrations/` for MongoDB index creation and data cleanup/verification
- `app/services/cache.py` with no-op and optional Redis adapters
- `tests/test_pagination.py`, migration tests, and cache invalidation tests

### Dependencies

- Reuse MongoDB indexes and aggregation operators.
- Add Redis client only when cache deployment is selected; keep no-op cache
  usable in development/tests.

### Architectural decisions

- Index actual query patterns, including normalized user email,
  `(job_id, seeker_id)` applications, recruiter/job ownership, and relevant
  created/applied timestamps. Validate query plans before adding indexes.
- Do not make a text index a substitute for safe literal search semantics.
- Cursor pagination uses stable sort keys and opaque cursors; existing
  unpaginated response behavior remains available during transition.
- Cache only derived, non-authoritative data. Cache keys include profile/job
  version and model/index version; invalidation occurs on update/delete.
- Avoid caching personal resume text or access-controlled documents.

### Implementation steps

1. Capture representative MongoDB explain plans and production-like collection
   sizes before selecting indexes or page sizes.
2. Detect existing duplicate records; provide report-only migration mode and
   approved remediation before unique indexes.
3. Add versioned, idempotent index migrations with verification output.
4. Add bounded cursor pagination to large list endpoints; keep array contract
   for legacy calls or offer explicit opt-in/new API version.
5. Consolidate recruiter applicant aggregation where explain plans show repeated
   per-job query overhead.
6. Add optional Redis cache for embeddings/match results only after Phase 2/3
   key semantics are stable. Add invalidation on user profile, job, and model
   updates.
7. Add cache bypass, TTL controls, and cache health metrics; MongoDB query path
   remains the fallback.

### Potential breaking changes

- Duplicate data can block unique index creation until resolved.
- Opt-in paged responses introduce an object envelope for clients that request
  pagination; legacy calls remain arrays during migration.
- Stale cache results are possible if invalidation/versioning is incomplete.

Migration: report and reconcile duplicates; create non-unique performance
indexes first; verify plans; create unique indexes only after cleanup; introduce
pagination opt-in; enable cache in shadow/read-through mode before raising
traffic.

### Testing strategy

- Migration idempotency and preexisting-duplicate tests.
- Mongo integration tests for unique constraints and stable cursor boundaries.
- Cache tests for TTL, invalidation, version changes, and backend unavailability.
- Explain-plan smoke tests against representative test data where CI MongoDB is
  available.

### Acceptance criteria

- Indexes correspond to measured query patterns and migrations are repeatable.
- Pagination is deterministic and backward-compatible by default.
- Matching cache invalidates on profile/job/model changes and degrades safely.
- No private resume data is cached or exposed through a list endpoint.

## Phase 6: Frontend and UX modernization

### Files to modify

- `templates/*.html`, especially views using dynamic API data
- `static/css/style.css`, `static/css/animations.css`,
  `static/js/main.js`, `static/js/app.js`, and page-specific JavaScript
- `templates/browse_jobs.html` for safe job-card rendering and truthful
  loading/empty/error states
- `README.md` for frontend setup and accessibility behavior

### New files

- `static/css/tokens.css` or equivalent shared design-token stylesheet if the
  existing CSS organization benefits from extraction
- `tests/frontend/` for DOM/security/accessibility checks
- Optional browser-test configuration and accessibility test workflow

### Dependencies

- Keep vanilla JavaScript and existing templates unless measured UX needs
  justify a framework.
- Add Playwright/axe tooling as development dependencies only for automated
  browser/accessibility testing; do not add a production frontend framework.

### Architectural decisions

- Use semantic headings/forms, associated labels, keyboard-visible focus,
  accessible error/status announcements, and reduced-motion support.
- Escape/render API data as text and avoid inline event handlers where practical.
- Keep shared styling consistent but migrate page-by-page to limit regressions.
- Distinguish an API failure from a successful empty result; never convert
  network, authorization, or server failures into mock job listings.
- Remove fabricated applied-job history from production UI. If examples remain,
  label them unmistakably and make them impossible to apply to unless persisted.
- Lazy-load non-critical images and keep the existing lightweight frontend.

### Implementation steps

1. Map existing user flows and document visual/behavioral baselines from current
   pages.
2. Audit responsive layouts at supported viewport widths; add keyboard,
   contrast, focus, form-label, and empty/error/loading-state fixes.
3. In `templates/browse_jobs.html`, remove fabricated `exampleJobs` as a
   fallback for API errors or empty results. Show a recoverable error state for
   request failures, a real empty state for a successful empty response, and
   preserve apply/recommendation behavior for actual API records.
4. Replace API-derived `innerHTML` interpolation and inline event handlers
   with safe DOM construction, `textContent`, and explicit event listeners.
   Keep static trusted markup separate from user/API-controlled values.
5. Standardize shared tokens, buttons, forms, cards, and status messaging
   without changing core route behavior.
6. Add accessible upload progress and async task feedback from Phase 4 when
   available.
7. Add automated accessibility checks on representative flows and fix findings.
8. Optimize image dimensions/format and remove unused assets only after
   reference and licensing checks.

### Potential breaking changes

- Styling/layout changes can alter page appearance; no intended API change.
- Removing example application history changes empty-state behavior but
  corrects false representation of user data.
- Replacing inline handlers requires careful preservation of existing page
  actions.

Migration: modernize one flow at a time; maintain route/API behavior and compare
against screenshots/manual QA; use feature flags only for major visual
navigation changes.

### Testing strategy

- Browser tests for registration/login, browse/apply, recruiter applicant
  views, profile upload, and application history.
- For browse jobs, assert that actual API results render, an empty API result
  renders an empty state, a failed request renders an error/retry state, and
  neither case renders or allows applying to example records.
- axe checks on representative pages; manual keyboard and screen-reader smoke
  checks.
- Viewport checks for narrow/mobile and desktop widths.
- Security regression tests for API-provided markup and template output.

### Acceptance criteria

- Core workflows remain operable with keyboard and assistive technologies.
- No API-controlled string is interpreted as markup or executable code.
- Pages work at supported viewport widths and honor reduced-motion settings.
- Empty states reflect actual user data; examples are clearly isolated from
  real applications.

## Phase 7: Testing and CI/CD

### Files to modify

- `.github/workflows/tests.yml`, `requirements.txt`, `pytest.ini`
- Existing `tests/test_matching.py`, `tests/test_security.py`
- `README.md`, `.env.example`

### New files

- `pyproject.toml` for formatter/linter/type-check configuration, if adopted
- `.pre-commit-config.yaml`
- Additional unit, route, integration, and migration test modules
- `.github/dependabot.yml`
- Separate optional model-evaluation workflow, triggered manually or on
  relevant changes

### Dependencies

- Add development-only Black, isort, Flake8 (or choose Ruff as a consolidated
  alternative; do not configure overlapping linters without a policy),
  mypy/appropriate Flask stubs, and pre-commit.
- Use GitHub dependency review/Dependabot and a supported SCA scanner. Pin
  versions and review advisories rather than applying blind automated upgrades.
- Test suite should avoid installing heavyweight model packages unless a test
  explicitly exercises them.

### Architectural decisions

- CI jobs are deterministic, scoped, and secret-free. Unit tests use mocks/fakes;
  service integration tests run separately with explicit Mongo/Redis services.
- The current workflow installs all of `requirements.txt` before running tests.
  Avoid pulling OCR/NLP/embedding packages into every lightweight test job when
  those integrations are not under test; keep real parser/model validation in a
  separate explicit job.
- Define supported Python versions from syntax and dependency compatibility,
  then test the minimum and deployment runtime.
- Model evaluation is a separate optional job; standard pull-request tests do
  not download models or datasets from unreviewed sources.
- Keep formatting/lint/type gates enforceable without reformatting unrelated
  user changes.

### Implementation steps

1. Preserve and run current tests in a clean CI environment; capture baseline
   results without changing application behavior.
2. Resolve and document Python minimum version mismatch (README says 3.9+ while
   current syntax requires Python 3.10+).
3. Add focused tests for route contracts, auth/role matrix, parsing fixtures,
   uploads, serialization, and regressions identified in Phase 1.
4. Add formatter, linter, and type-check configuration; scope mypy initially to
   public modules and expand as annotations permit.
5. Split CI into lint/type, unit tests, integration tests, security/SCA, and
   optional evaluation jobs with cached dependencies where safe.
6. Configure Dependabot and dependency-review workflow; document review and
   rollback policy.
7. Add pre-commit hooks and local commands; enforce on pull requests after
   baseline violations are resolved.
8. Track coverage for critical security/matching modules without using
   unreviewed global coverage thresholds.

### Potential breaking changes

- CI begins failing on existing style/type issues once gates are enforced.
- Minimum Python declaration may change.
- Dependency pinning/upgrade may expose platform or ML wheel incompatibilities.

Migration: establish baseline first; apply formatter in targeted commits only;
roll out type gates by module; update Python support statement with tested
evidence; do not auto-upgrade production dependencies without review.

### Testing strategy

- Every CI job must run on a clean checkout and not need developer secrets.
- Unit tests always run; integration/evaluation jobs are clearly separated.
- Test supported Python versions, with the deployment version mandatory.
- Validate workflow syntax and ensure Dependabot/SCA changes are reviewable.

### Acceptance criteria

- Pull requests run tests and static checks; security dependency scanning is
  configured.
- The documented Python range matches CI and dependency support.
- Critical auth/upload/matching flows have regression tests.
- Optional model evaluation runs reproducibly without impacting default CI.

## Phase 8: Observability

### Files to modify

- `app.py`, `app/config.py`, service boundaries for auth, matching, uploads,
  database, and queue
- `.env.example`, `README.md`, CI workflow for observability smoke checks

### New files

- `app/observability.py` for structured logging, request ID, and metrics
  integration
- Health/readiness route tests and operational runbook under `docs/`
- Optional Sentry and Prometheus adapters with no-op defaults

### Dependencies

- Use Python standard logging/JSON formatter first.
- Add Prometheus client only if a Prometheus scrape endpoint is deployed.
- Add Sentry SDK only when a project DSN, data-processing policy, and
  environment configuration are approved. Both integrations remain optional.

### Architectural decisions

- Do not log passwords, authorization headers, refresh/reset tokens, raw resume
  text, uploaded file contents, or unnecessary PII.
- Add request correlation ID and structured fields; honor trusted-proxy
  configuration rather than accepting arbitrary forwarded headers.
- Separate liveness from readiness; readiness checks required dependencies
  without exposing credentials or detailed exception traces.
- Use bounded-cardinality metric labels; do not label by user, job, email, or
  arbitrary URL parameter.
- Track matching mode/model version, inference failures/latency, parser task
  outcomes, queue depth, DB latency, and HTTP latency/errors.

### Implementation steps

1. Define event/metric names, PII handling, retention, access, and alert owners.
2. Add JSON structured logging and request IDs at the Flask boundary.
3. Add `/health/live` and `/health/ready` or equivalent; keep existing routes
   unchanged and return minimal public information.
4. Instrument database, upload/parser, matching/model, and queue stages with
   latency/error counters.
5. Add optional error-tracking integration that scrubs secrets and personal
   content before transmission.
6. Document operational dashboards, alerts, and incident triage.

### Potential breaking changes

- New health routes are additive. Log format changes may affect log consumers.
- External telemetry can transmit metadata; it stays disabled until approved
  and configured.

Migration: dual-write logs during transition if existing consumers require
plain text; enable telemetry by environment; verify scrubbing in staging before
production activation.

### Testing strategy

- Test request IDs, log redaction, metric label cardinality, health status with
  required dependencies available/unavailable, and telemetry disabled mode.
- Use fake Sentry/metrics adapters; do not send test user data to vendors.

### Acceptance criteria

- Operators can distinguish liveness/readiness and identify matching/parser/
  queue failures without exposing sensitive data.
- Structured logs correlate one request across route and service boundaries.
- Optional telemetry is off or safely configured by default and passes
  redaction tests.

## Phase 9: Deployment and documentation

### Files to modify

- `README.md`, `.env.example`, `.gitignore`, CI workflow
- `app/config.py` and startup/health configuration only as required

### New files

- `Dockerfile`, `docker-compose.yml` for local development
- `docs/UPGRADE.md`, `docs/OPERATIONS.md`, backup/restore and migration
  instructions
- Optional Kubernetes/Helm example under `deploy/` after a concrete target
  platform and operating model are selected

### Dependencies

- Docker/Compose tooling is operational tooling, not a Python runtime
  dependency.
- Include MongoDB and Redis in local compose only if Redis queue/cache adapters
  have been adopted; make optional profiles for optional services.
- No Helm chart, cloud vector DB, or secret-vault integration is required until
  deployment target and operator are specified.

### Architectural decisions

- Run Flask behind a production WSGI server and reverse proxy/TLS termination;
  do not use Flask’s development server in production.
- Keep secrets outside the image and repository. Use managed secret storage in
  production.
- Persist MongoDB and private upload data; define backup encryption, retention,
  restore testing, and migration rollback.
- Use non-root containers, pinned base image versions, health checks, and
  minimal build context; exclude virtual environments, uploaded data, `.env`,
  and model cache.
- Document optional profiles for worker, Redis, scanner, telemetry, and vector
  service; no optional service silently becomes mandatory.

### Implementation steps

1. Select the initial production target, supported Python runtime, WSGI server,
   proxy, storage, secret manager, and backup ownership.
2. Build a minimal non-root application image; scan it and pin base images.
3. Add Compose for local services and health dependencies, with optional
   profiles for worker/cache/scanner.
4. Add startup validation and readiness probes; ensure workers and web
   processes have distinct commands and resource limits.
5. Document environment variables, secrets, migrations, token rollout,
   private-file migration, model download/cache, and rollback in `docs/UPGRADE.md`.
6. Document deployment, TLS, backups, restore drills, monitoring, retention,
   and incident response in `docs/OPERATIONS.md`.
7. Add deployment smoke tests and a release checklist. Add a Helm example only
   after production settings and ownership are known.

### Potential breaking changes

- Container deployment changes filesystem expectations; uploaded files must
  use persistent private storage.
- Removing local-only defaults may require developers to provide a `.env`.
- Production startup may fail fast on missing mandatory secrets/dependencies,
  which is intentional.

Migration: validate compose locally, then staging; back up database/uploads;
run additive migrations; deploy app/worker versions compatible with both
schemas; switch traffic; verify health and API flows; retain documented
rollback images and backups.

### Testing strategy

- Build image in CI; run container as non-root; verify health endpoint and
  secret absence in image layers.
- Compose smoke test for required services, optional profiles, task processing,
  persistent volumes, and restart behavior.
- Validate upgrade/rollback instructions in a disposable environment.
- Perform backup restore and private-upload recovery drills before production.

### Acceptance criteria

- Reproducible local development stack and documented production runtime.
- No secrets or user uploads are baked into images or committed.
- Health checks, data persistence, migration, backup/restore, and rollback are
  tested in a staging-like environment.
- Every optional dependency is explicitly configured and has a documented
  degraded mode.

## Recommended execution order

The numbered phases above remain the project organization. For safe execution:

1. Start Phase 7 baseline tests/CI first, without broad cleanup.
2. Complete Phase 1 security blockers before deploying further changes.
3. Run Phase 2 evaluation and score-contract work before choosing vector
   retrieval or caching semantics.
4. Decide Phase 3 vector-store need from measured evaluation/retrieval scale.
5. Implement Phase 4 asynchronous work only for measured expensive operations.
6. Apply Phase 5 indexes and pagination with additive migrations.
7. Complete Phase 6 frontend changes incrementally alongside API-compatible
   server changes.
8. Instrument Phase 8 boundaries as their services are stabilized.
9. Deliver Phase 9 deployment only after security, migration, and recovery
   acceptance is met.

## Global release gates

- No unresolved Critical or High security finding is knowingly exposed in a
  production release.
- Existing API consumers pass compatibility tests or have an approved migration
  path.
- Database and private-file migrations have dry-run reports, backups, and
  rollback instructions.
- Matching changes have reproducible evaluation evidence and do not claim
  hiring accuracy from unvalidated percentages.
- Queue, vector, cache, scanner, and telemetry failures have documented
  degraded behavior.
- CI is green on the declared runtime and checks tests, static quality, and
  dependency risk.
- Staging QA covers job seeker, recruiter, and admin workflows, including
  authorization denial cases.

## Production readiness exit criteria

The platform may be considered for production only after the release gates pass,
the chosen deployment has verified restore/rollback procedures, security
controls have been validated against route-level tests, and model ranking has
been evaluated on an approved representative dataset. Production readiness is
an explicit release decision; completing the plan alone does not establish it.
