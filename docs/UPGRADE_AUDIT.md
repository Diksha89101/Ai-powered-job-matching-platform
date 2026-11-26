# Project Upgrade Audit and Progress

Audit date: 2026-11-26 (Asia/Kolkata)

This snapshot records the existing working tree and this upgrade pass. Several new service files and edits were already present before this pass began (`app.py`, `resume_parser.py`, `intelligence.py`, `job_discovery.py`, `agent_operations.py`, and `tests/test_intelligence.py`). Those edits were reviewed and preserved.

| Component | Existing implementation | Remaining functionality | Changes in this pass |
|---|---|---|---|
| Resume parsing | PDF/DOCX/TXT/MD/legacy DOC text extraction; PDF OCR if optional libraries exist; signature/size validation; private storage; first-line name, email/phone, skills, section and explicit duration parsing | Legacy DOC extraction is heuristic; soft-skill taxonomy is limited; no deep structured work-history/qualification extraction; OCR optional and can be expensive | Kept the existing parsers; made DOCX paragraphs preserve section boundaries; classified a small allowlisted soft-skill set; added DOCX and empty-input tests |
| AI matching | Sentence Transformer embeddings are lazy; normalized skills and keyword fallback; existing component fields | New hybrid layer needed reliable fallback, tuned weights and more comprehensive evaluation | Fixed hybrid fallback to preserve keyword scores if embeddings are unavailable; added a regression test |
| Explainable recommendations | Match scores, matched/missing skills; new score breakdown and explanation fields in service | Education relevance is currently unknown (`null`); semantic explanation depends on model availability; no labeled evaluation dataset | Added score-factor weights and recommendation explanations to the seeker job details modal; connected the career insights and skill-gap APIs to accessible dashboard/modal UI |
| Skill gap analysis | New pure service compares existing/required skills, categories, priority and topic suggestions | No trusted resource catalog; “skills needing improvement” beyond present/missing is not measured | Kept suggestions clearly heuristic; no unsupported course links added |
| Job recommendations | Existing `/api/jobs/recommended`; scoring, applied-state and location/type/experience/skill filters in working-tree edits | Previous application history is not a ranking signal; empty profiles only produce weak zero-signal matches | Preserved the additive filter behavior and tested service filtering |
| Live job discovery | New provider interface, mock provider, record normalizer and disabled Agensi adapter | No verified Agensi API documentation/contract/credentials; no operational external provider, retry policy or cache | Kept provider disabled until verified; documented test-only mock and integration limits |
| Career intelligence | New deterministic service using profile and listed jobs; protected job-seeker route | No LLM integration, learning-resource catalog, or external course catalog | Preserved deterministic/optional design; documented actual limitations |
| Agent integration | New Python business-operation layer validates profiles and calls matching/intelligence helpers | No network transport/MCP registration; authentication is delegated to caller; operation input/result contracts needed hardening | Added job/query validation, bounded public-field outputs, resume text omission, and `docs/agent/SKILL.md` |
| Security | Strong environment secret checks; JWT auth and refresh rotation; rate limits; private upload storage and protected downloads; role checks; security headers/CSP report-only; account deletion endpoint in working edits | Deletion lacked cleanup for refresh sessions and user notifications; no broad security scan in this pass; multi-worker rate limits need configured shared storage | Account deletion now also clears refresh sessions and notifications; added a deletion regression test |
| Automated testing | Pytest suite and GitHub Actions; 27 baseline tests were discovered | Suite coverage is still not comprehensive for every requested route and external failure case; default `.venv` has broken pytest | Added parser, matching fallback, agent validation and deletion tests; baseline and final results are recorded after running the suite |
| UI/UX | Existing responsive Jinja pages, CSS/JS validation, async upload overlay, match cards and modal | Match factor explanation and fake listing fallback needed correction; no new Generative Icons assets | Removed fictional sample listings from browser success/error paths; added accessible loading status and score-factor explanation, career insights and skill-gap views |
| Database | Existing Mongo collections for users/roles/jobs/applications/notifications/refresh sessions; TTL migration for refresh expiry | No index audit or production duplicate analysis; deletion is not transactional; no schema redesign justified | Used existing collections; no schema migration required |
| Deployment | Environment-driven config; GitHub test workflow; explicit migration scripts | No Docker/Compose or production WSGI server; health/readiness and deployment automation not verified | No deployment topology changed; remaining readiness gaps are documented in README work |
| Documentation | Existing README includes setup, security, matching methodology and migrations | Needs current-service API docs and honest coverage of incomplete external/icons/deployment scope | Added an agent operation skill and this audit; README updates follow final implementation evidence |
| Icon integration | No Generative Icons integration was present | Runtime/package install and selected icon assets need reproducible project-local copies, testing, and licensing notices | Official docs and license were reviewed; package/CLI could not be installed from this workspace, so no false integration is claimed |

## Baseline test run

- Command: `.venv-py314\\Scripts\\python.exe -m pytest -q`
- Result before this pass: 26 passed, 1 failed, 0 skipped (27 collected), 117.82 seconds.
- Failure: the in-progress hybrid scorer returned 40 for a fully matching explicit skill profile when the test disabled semantic embeddings. The score continued to apply semantic/title/experience weights despite the underlying matcher falling back to keyword-only scoring.
- The default `.venv` is Python 3.15 alpha with an installed pytest import failure (`NameError: frozendict`); `.venv-py314` is the usable local runner.

## External documentation findings

- Generative Icons official docs describe a dotLottie native asset format, a vanilla JS runtime, `prefers-reduced-motion` handling, per-icon installation, and MIT licensing with separate third-party notices. The project has no package manifest/bundler, and package installation could not be completed here. The implementation remains incomplete pending a reproducible asset/runtime install.
- The official Agensi website did not return content through the available web tools, and searches found no official API documentation. The disabled provider is not a live integration.

## Verification after this upgrade pass

- Full suite: `.venv-py314\\Scripts\\python.exe -m pytest -q -p no:cacheprovider` — **36 passed, 0 failed, 0 skipped** in 16.15 seconds, with one upstream PyMuPDF `fitz` deprecation warning.
- `python -m compileall -q app.py app utils tests` — passed.
- `git diff --check` — passed.
- `node --check` on static JavaScript entry files and the updated dashboard inline script — passed.
- Flask entry point imported via `runpy.run_path('app.py')` — passed without connecting to MongoDB.
- No Docker, production WSGI server, MongoDB Atlas connection, live browser session, live job API, or Generative Icons asset playback was verified.

## Security-scan limitations

A managed Standard scan was started for the repository, but the connected account reports Daybreak access as not granted, so protected scan results may not be displayable. The local policy-resolution helper was also blocked by Windows Application Control when loading the plugin's `windows.node` binary. No repository `SECURITY.md` was found. The scan has not been finalized, and no claim of a completed repository-wide automated security audit or a clean scan is made. A manual source review and route tests were performed, but they do not replace the unavailable scan results.
