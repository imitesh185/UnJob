# Architecture

## Objective and MVP

The system is a modular monolith: independently testable domain services behind one FastAPI deployment, one Next.js UI, and SQLAlchemy persistence. Docker uses PostgreSQL; native development defaults to SQLite. Phase 1 introduced public Greenhouse and Lever connectors, deterministic normalization/deduplication/ranking, persistence, and dashboard review. Phase 2 extends discovery to a company universe and bounded background exploration. Phase 3 adds candidate intelligence: a fact-based candidate profile, JD analysis, candidate-job fit, company target scores, application priority and strategy, truth-preserving resume tailoring with human approval, application tracking, and outcome analytics. No application or outreach is sent.

## Company discovery versus job discovery

```text
Next.js: Companies / Discovery / Jobs
                  |
             FastAPI API
                  |
          Durable database queue
                  |
          Separate discovery worker
             /              \
    Company discovery     Job discovery
    Search providers      Career sources
    Hiring evidence       ATS adapters / HTML
    Domain verification   Job detail extraction
    Review + frontier          |
             \                 /
              Canonical Job / JobSource
                         |
         Normalize -> Deduplicate -> Rank -> Persist
```

Seed imports establish a starting universe. Market searches use target-role and location variations independently of those seeds, so evidence-backed companies not manually entered can become candidates. Tiers organize review and priority; they do not restrict discovery to A-tier companies.

The API persists work and returns its run identity. The worker performs network activity separately, records progress and failures, and processes scheduled work under explicit budgets. There is no Redis or graph-database requirement in this architecture.

## Evidence and safety boundary

Company discovery and job opportunity scores are distinct and explainable. Evidence must identify its source URL, observed signal, confidence, and discovery time. Unknown legal names, funding, engineering quality, locations, salaries, and posting dates remain unknown; neither search snippets nor generated recommendations constitute verified company facts.

Free/no-key search is supported through a provider abstraction; an optional SearXNG endpoint can be configured. LinkedIn/X integrations require legitimate access and are not implicitly enabled by a web-search result. A source that challenges, blocks, or disallows automated access is recorded explicitly. Workers must not bypass login, CAPTCHA, access controls, or site restrictions.

Outbound retrieval is restricted to public HTTP(S) URLs, with DNS/address validation and redirect checks. Requests and bodies are bounded, transient failures use bounded backoff, and crawling respects robot restrictions. Run budgets bound search queries, results, pages, newly discovered companies, expansion, and graph depth.

## Freshness and reconciliation

The primary queue prioritizes known posting times within the configured freshness window (48 hours by default). Source update time and first observation time are separate from posting time. Jobs with unknown dates cannot be presented as verified fresh openings.

Source scans retain completion state. Only a successful complete enumeration can establish that a previously seen job is absent; a failed, blocked, or budget-truncated scan must not close unseen jobs.

## Company universe

Company records retain configurable tiers, verification/activity state, discovery score and reasons, and optional metadata. Multiple career sources can belong to a company. Discovery events retain evidence, the frontier prioritizes unexplored candidates, and relational edges capture evidenced company relationships. Existing jobs and job-source records remain the canonical opportunity model; there is no second job table.

## Repository

```text
backend/
  alembic/             database migrations (0001 Phase 1, 0002 company universe + discovery,
                       0003 candidate intelligence)
  app/
    api/routes/        HTTP boundary: jobs, dashboard, ingestion (202), companies, discovery,
                       profile, opportunities, resume-variants, applications
    connectors/        career-source adapters (Greenhouse, Lever, Ashby, Recruitee, Workday,
                       Oracle Recruiting, amazon.jobs, generic JSON-LD/HTML)
    discovery/         SSRF-safe fetcher, search backends, discovery providers, evidence
                       extraction, ATS detection, JSON-LD parsing, names/domains, role
                       classification, company scoring, runtime settings
    intelligence/      Phase 3 pure logic: skill taxonomy, resume parsing, candidate facts,
                       JD analysis, matching, company positioning, targeting and priority,
                       claim verification, tailoring, quality gate, rendering, optional LLM
    services/          ingestion, deduplication, normalization, ranking, company universe,
                       exploration orchestrator, durable run queue, candidates, intelligence,
                       applications, search cache
    worker.py          background worker (python -m app.worker)
    models.py           persistence model
    schemas.py          API contracts
  tests/               offline tests with a mocked network
frontend/              Next.js: Today (/), Opportunities (/opportunities[/id]), Applications,
                       Profile, Company Universe (/companies), Discovery (/discovery)
docs/                  architecture and operating policy
docker-compose.yml      local PostgreSQL/API/worker/UI
```

## Data model

`jobs` is the canonical opportunity. `job_sources` retains every discovered source URL and source-specific ID. A unique `(source, external_id)` constraint makes ingestion replay-safe. Canonical jobs carry structured raw requirements, normalized technologies, status, transparent score components, and reasons. Later migrations add candidate profiles, company intelligence, resumes, application attempts, reviews, outreach, and audit events without changing the canonical job identity.

Phase 2 adds the following tables:

| Table | Purpose |
|---|---|
| `companies` | Company universe: normalized name key and aliases, optional verified metadata (`NULL` = UNKNOWN), tier, state, review status, discovery score, breakdown, and reasons |
| `career_sources` | One or more per company: URL, platform and confidence, how it was found and why, scan status and errors, last successful and last complete scan |
| `company_discovery_events` | Evidence: reason (`JOB_SEARCH`, `LINKEDIN_HIRING_SIGNAL`, `X_HIRING_SIGNAL`, `SEARCH_ENGINE`, `SIMILAR_COMPANY`, `STARTUP_DISCOVERY`, `CAREER_PAGE`, `MANUAL_SEED`), source, source URL, evidence text, confidence, observation count, review state |
| `discovery_candidates` | Discovery frontier: priority with point breakdown, depth, and status |
| `company_relationships` | Evidenced edges (`competes_with`, `similar_to`) with confidence, evidence text, and evidence URL |
| `discovery_runs` | Durable queue and run history: kind, status, budgets, progress, errors, lease, and attempts |
| `app_settings` | Runtime settings and provider response cache |

`jobs` gains `company_id`, listing state, last seen and closed times, posting-date precision, source update time, and role classification. `job_sources` gains a career-source link and per-source listing state.

Phase 3 (migration `0003`) adds candidate intelligence. `companies` gains `tier_source` (`user`, `s_tier_list`, or default), `archetype`, and `positioning`.

| Table | Purpose |
|---|---|
| `candidates` | Single local candidate: identity, current title and employer, years from employment dates, target roles, preferred locations, relocation, compensation, `profile_version` |
| `resumes` | Imported master resumes: file name, parser, parsed structure, version, active flag |
| `candidate_facts` | Atomic facts: category, statement, source (`resume`, `derived`, `user`), source reference, confidence, experience type, verified flag, technologies, concepts, metrics, employer and dates, skill level and `level_source` (`inferred` or `user`) |
| `job_analyses` | One per job: description hash, analyzer version, seniority, years range, requirement items, required/preferred skills, responsibilities, domains, location and compensation requirements, role focus, warnings |
| `candidate_job_matches` | Fit score and breakdown, seniority fit, per-requirement and per-skill assessments with fact IDs, strengths, gaps, explanation, location and compensation fit |
| `target_scores` | Candidate-aware company target score, breakdown, and reasons |
| `application_scores` | Priority score and class, gates, reasons, recommendation, strategy, effort |
| `resume_variants` | Versioned JD-specific resumes: generator, trigger, status, positioning, content, tailoring diff, alignment score and breakdown, truth validation, quality checks, rendered files |
| `resume_claims` | Every line of a variant: section, generated and original text, source fact IDs, JD requirement IDs, tailoring reason, verification result and notes, included flag, rewrite origin |
| `applications`, `application_events` | Application per job with status, resume variant, contacts, next action; append-only status history with actor and note |
| `outreach_drafts` | Drafts only; never sent |
| `search_results`, `provider_health` | Search-result cache and per-provider health |

## Run queue and worker

The API validates and inserts a `discovery_runs` row, then returns it (HTTP 202). Workers claim queued runs with a compare-and-set update and hold a renewable lease. A crashed worker's run is retried after the lease expires, with backoff, up to `max_attempts`. A run ends as `COMPLETED`, `PARTIAL` (finished, but some sources failed or were blocked), `FAILED`, or `CANCELLED`. Run kinds are:

- `seed_resolution`
- `exploration`
- `frontier` and `frontier_explore`
- `company_scan`
- `scan`
- `reconciliation`
- `ingestion` (legacy Greenhouse/Lever imports)
- `candidate_analysis` (re-rank after a resume import, preference or targeting change, or new jobs; auto-tailors per policy)
- `resume_tailoring` (one job, on request)

Requests that duplicate an active run return that run instead of queueing another. This covers the same run kind from the API or scheduler, the same company scan, the same frontier entry, and the same board import.

## Service and agent boundaries

- `GreenhouseConnector` and `LeverConnector`: retained public ATS adapters, dispatched by background ingestion work.
- `JobNormalizer`: maps source payloads into the stable ingestion contract and normalizes semantic aliases.
- `DeduplicationEngine`: resolves exact ATS/application identity first, then normalized company/title/location and conservative description similarity.
- `OpportunityScorer`: deterministic, configurable component scores with human-readable reasons. It now includes the company's discovery score as a separate component.
- `DiscoveryService`: transaction and idempotency coordinator. It also refreshes rescanned jobs and closes absent jobs only after complete scans.
- `SafeFetcher`: every outbound request goes through it. It allows only public HTTP(S) addresses, checks DNS and peer IPs, re-validates every redirect, and respects robots.txt. It also enforces per-host pacing, timeouts, body-size caps, bounded retries with backoff, cooldowns after rate limiting, and run page budgets.
- `SearchService` and `DiscoveryProvider`s: pluggable web search and structured sources that return attributable `CandidateSignal`s. Results with no attributable company, job boards, and aggregators are rejected.
- `Explorer`: orchestrates the run kinds. `companies` services handle company matching, events, career sources, relationships, frontier candidates, and score refresh.
- `classify_role`: semantic role classification from title, description, and technologies. Data Analyst, data-center, master-data, sales, product/program/delivery-manager, enterprise-application (Salesforce, SAP, Power Platform), and specialised non-data engineering roles (network, security, embedded, robotics, QA, mobile) are excluded from data-engineering targets. An employer's own name in its job descriptions is not technology evidence.

### Candidate intelligence pipeline (Phase 3)

```text
Master resume -> resume_parser -> facts (CandidateFact) -> CandidateModel (skills by evidence)
Job -> jd_analyzer (requirement items, years, seniority, location, pay) -> JobAnalysis
CandidateModel x JobAnalysis -> matching -> fit + skill assessments + explanation
Company jobs + profile -> targeting.company_target_score
fit + target + freshness + career value -> application_priority (gates, caps, fit floors)
  -> application_strategy (emphasize / de-emphasize / concerns, all evidence-based)
  -> tailoring policy (S-tier: every relevant job; other tiers: configurable rules)
  -> tailor_resume (select, reorder, verbatim bullets, composed headline/summary)
  -> verify_claim per claim -> evaluate_resume (alignment score, ATS, seniority, stuffing)
  -> ResumeVariant (USER_REVIEW) -> human approval -> application tracking -> analytics
```

- `IntelligenceService.run_analysis` re-classifies jobs, refreshes analyses whose description hash or analyzer version changed, recomputes matches, target scores, and priorities, keeps the application queue in sync (rejected jobs are withdrawn by the system and re-opened if recommended again), and tailors resumes within `max_variants_per_run`.
- `verify_claim` is the truth boundary. A claim must be supported by the facts it cites: technologies, numbers, leadership verbs, production and seniority words. JD-copied text is rejected. Optional LLM rewrites pass through the same verifier and are discarded on any failure.
- Approval is enforced server-side: a variant with unsupported included claims cannot be approved, and applications cannot reach `READY_TO_APPLY`, `APPLIED`, or later without an approval that is newer than the latest resume replacement.
- Future agents remain separate: company research, resume tailoring, browser application, question answering, exception classification, outreach research/writing, human review, and analytics. LLM access will sit behind a provider interface and receive only necessary fields.

## Application state machine

Implemented in Phase 3 (`services/applications.py`): `RECOMMENDED -> USER_REVIEW -> APPROVED -> READY_TO_APPLY -> APPLICATION_STARTED -> APPLIED -> OA -> RECRUITER_SCREEN -> TECHNICAL -> FINAL -> OFFER`, with `REJECTED` or `WITHDRAWN` reachable from any state. The system moves applications only through the automatic statuses (up to `USER_REVIEW`) and withdraws them only from those statuses; everything from approval onwards is a user action recorded in `application_events`. Resume variants follow `USER_REVIEW -> APPROVED`, or `SUPERSEDED`, `STALE` (an approved version replaced after a JD change), and `REJECTED`.

The future browser-assisted flow keeps these exceptional transitions: `Preparing|Ready -> Blocked`, `Ready -> Submission Unverified`, and `Blocked|Submission Unverified -> Preparing|Applied` after review or verification. Submission requires an idempotency key and a fresh server-side duplicate check.

## Browser automation abstraction

Future `BrowserAdapter` implementations expose `detect`, `authenticate`, `map_fields`, `fill`, `validate`, `submit`, and `verify`. Each returns typed observations rather than hiding failures. ATS adapters never bypass CAPTCHA, MFA, anti-bot controls, or access controls. A browser run persists checkpoints and pauses with a review item on security challenges, uncertain answers, or unknown fields.

## Human review

A review record contains the blocked action, redacted context, confidence, reason, permitted choices, expiration, and resolution audit fields. Workers transition to `WAITING_FOR_HUMAN`; only an explicit resolution can resume them. External outreach always creates a draft review regardless of confidence.

## Security

Single-user local deployment is the first boundary. Secrets remain in environment/secret stores, logs are structured and exclude payloads containing credentials or personal answers, and only minimal fields are sent to model providers: optional resume rewording sends the role title, company name, up to 25 JD terms, and the bullets being reworded. Resume files live under `DATA_DIR`, which is git-ignored. Production adds authenticated admin access, encrypted object storage, field-level encryption for high-risk PII, retention rules, and immutable audit logs.

## Implementation sequence

1. Phase 1: discovery, normalization, deduplication, ranking, dashboard.
2. Company universe, evidence-backed company discovery, career-source discovery, bounded workers, market exploration, and reconciliation.
3. Phase 3 (done): candidate profile, JD analysis, fit, company target score, application priority and strategy, S-tier tailoring policy, truth-preserving versioned resumes with approval, application tracking, and outcome analytics.
4. Expand source coverage and validate discovery quality against more live career portals.
5. Learning loop: feed recorded outcomes back into weights once sample sizes are meaningful.
6. Browser-assisted application adapters, account state, and mandatory reviews.
7. LinkedIn/X signal ingestion where permitted, outreach drafts and mandatory approval.
8. Deployment hardening.
