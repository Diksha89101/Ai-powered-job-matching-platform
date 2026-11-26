# Job Matching Platform Agent Skill

This skill describes the local, reusable business operations in `app/services/agent_operations.py` for the Flask + MongoDB platform. It does not create a public agent endpoint, MCP server, or authentication mechanism. The caller must authenticate the person, authorize access to their profile, and pass only data the caller is allowed to use.

## Privacy and authorization

- Never load another person's resume from a path or database record on the basis of agent input.
- Obtain candidate profile data from the authenticated platform context. Do not accept a user ID from an untrusted prompt as proof of access.
- Do not send resume text to an external model or job provider. Current operations are local and deterministic apart from the configured semantic matching model.
- Keep private resume text out of search queries and tool outputs unless the user has explicitly requested an authorized resume analysis. `analyze_resume` omits the full text from its parsed result.
- There is no HTTP transport for these operations yet. Do not describe them as remotely callable tools.

## Operations

Import from `app.services.agent_operations`:

| Operation | Inputs | Result |
|---|---|---|
| `analyze_resume(profile)` | `profile`: object with optional `skills`, `resume_text`, and `experience` strings (maximum 50,000 characters each) | `profile` skills/text features and `resume` fields explicitly parsed from supplied text, excluding the full text |
| `search_jobs(provider, query)` | configured `JobProvider`; query object of up to 20 scalar filters (each at most 200 characters) | up to 500 provider records, limited to public job fields |
| `match_jobs(profile, jobs)` | profile object; up to 500 job objects | deterministically ranked records with 0–100 match score, matched/missing skills, score breakdown and evidence-based explanations |
| `identify_skill_gaps(profile, job)` | profile and one job object | existing and missing skills with category, priority and learning topics |
| `recommend_jobs(profile, jobs, preferences=None)` | profile, jobs and optional string filters (`location`, `job_type`, `experience_level`, `skill`) | ranked, filtered, de-duplicated matching records |
| `compare_job_descriptions(profile, jobs)` | profile and a list of jobs | the same ranking contract as `match_jobs`, for comparison |
| `analyze_career_options(profile, jobs)` | profile and jobs | profile-conditioned role suggestions, missing-skill priorities and interview topics |

Profiles accept strings for `skills`, `resume_text`, and `experience`. Jobs accept string fields for `title`, `company`, `location`, `skills`, `description`, `job_type`, `experience_level`, `application_url`, and `source`; `skills` may also be an array of strings. Identifiers are returned as strings.

Errors are raised as `AgentInputError` for invalid caller data and `ProviderUnavailable` when a provider is not configured or fails. Catch these at the transport boundary and return a stable JSON error object without stack traces or profile content.

## Example workflow

```python
from app.services.agent_operations import analyze_resume, identify_skill_gaps, recommend_jobs

profile = {"skills": "Python, SQL", "resume_text": authorized_resume_text}
analysis = analyze_resume(profile)
ranked = recommend_jobs(profile, normalized_jobs, {"location": "Remote"})
gaps = identify_skill_gaps(profile, normalized_jobs[0])
```

The caller is responsible for obtaining `authorized_resume_text` after authentication and ownership checks. Do not persist these intermediate values unless the platform's privacy and retention policy permits it.

## Matching limitations

- The percentage is a relevance score, not hiring probability or measured accuracy.
- Semantic scoring depends on the configured Sentence Transformer model; when it is unavailable, matching falls back to explicit skill overlap.
- Education and experience data are only reported when detectable from supplied text; the parser does not infer missing qualifications.
- Career outputs are deterministic heuristics, not a qualified career counselor or guarantee of employment.
- Agensi has no verified API contract or credentials in this repository. `AgensiProvider` intentionally reports unavailable.
- No live job discovery provider is currently operational. Use `MockJobProvider` for isolated tests only.

## External requirements

No external LLM or job-board credentials are used by these operations. Live provider configuration must be implemented only after an official provider contract, authentication method, usage permissions, rate limits, and pricing are verified. Obtain the user's explicit consent before any future integration sends personal resume data outside the application.
