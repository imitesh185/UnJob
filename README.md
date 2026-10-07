# JobOS

A local-first, human-controlled job-market discovery and application-intelligence engine. UnJob separates company discovery from job discovery: seed companies are a starting point, not a boundary. Public hiring evidence can add previously unknown companies to a reviewable discovery frontier. Jobs continue through the original canonical normalization, deduplication, ranking, and persistence pipeline.

Phase 3 adds a candidate-aware layer on top: your master resume becomes a set of verifiable facts, every relevant job is analysed and matched against them, each opportunity gets an explainable priority (P0–P3 or Reject) and an application strategy, and JD-specific resumes are generated without inventing anything. UnJob never submits applications or sends outreach, never stores credentials, and never bypasses robots rules, CAPTCHAs, or logins.

## Run locally

1. Copy `.env.example` to `.env` and change the PostgreSQL password.
2. Start the stack:

   ```powershell
   docker compose up --build
   ```

3. Open `http://localhost:3000`. API documentation is at `http://localhost:8000/docs`.

The dashboard does not contain mock jobs. Start at `/profile` and import your master resume (text-based PDF, DOCX, or TXT). The home page then answers "If I have two hours today, which three applications should I work on?". Open `/companies` to paste seed company names and manage career sources, and `/discovery` to review discovery evidence, explore the frontier, configure budgets and targeting, and monitor background runs.

Greenhouse and Lever remain available as direct source imports. Discovery requests enqueue durable work; they do not crawl websites inside an HTTP request. The worker must be running for queued work to progress. Docker Compose starts it alongside the API.

## How discovery works

1. **Seed import** (`/companies`): pasted names are normalized and deduplicated, then a `seed_resolution` run resolves each company's career source. Domains and careers sites are recorded only when verified (search evidence, a public ATS board that names the company, or a probed page whose host and title name the company). Otherwise they stay `UNKNOWN`.
2. **Career scans**: the detected platform's adapter fetches recent openings. Jobs go through the existing normalization, deduplication, ranking, and persistence pipeline and are linked to the company and its career source.
3. **Market exploration**: a follow-up `exploration` run searches target-role and location variants, regardless of the seed list. It also reads no-key structured sources: Hacker News "Who is hiring?", Remotive, and the YC open dataset. New companies are added only with recorded evidence: source URL, reason, confidence, and time. They then enter the discovery frontier.
4. **Frontier**: candidates are explored in priority order. Strong recent hiring signals score higher. Each candidate goes through career-source resolution, a scan, verification, and similar-company expansion, all within run budgets.
5. **Review** (`/discovery`): every discovered company appears in the discovery feed with its evidence. *Add to universe* accepts it and *Ignore* marks it inactive.

Company state follows `DISCOVERED → UNVERIFIED → VERIFIED → ACTIVE` (relevant open roles found) or `INACTIVE`. The company discovery score is separate from each job's opportunity score; a job's score includes the company's contribution, and the reason is shown with it.

### Discovery providers (no paid API required)

| Provider | Key | Notes |
|---|---|---|
| DuckDuckGo HTML | none | Default web search. Paced. Rate-limit/anomaly pages are recorded as `SOURCE_BLOCKED`; the backend then cools down (15 min by default) and is never bypassed. |
| SearXNG | none | Optional. Set `SEARXNG_URL` to an instance you operate or may query; JSON output must be enabled. Preferred when configured. |
| Brave Search | `BRAVE_SEARCH_API_KEY` | Optional and off without a key. |
| Tavily | `TAVILY_API_KEY` | Optional and off without a key. Results are cached and provider health is tracked. |
| Hacker News, Remotive, YC (yc-oss) | none | Structured hiring and startup sources, cached. |
| LinkedIn / X | none | Only public pages already indexed by the web-search backend. LinkedIn and X are never fetched or logged into. |
| Bing | — | Not used: its robots.txt disallows automated `/search`. |

### Career-source adapters

API adapters: Greenhouse, Lever, Ashby, Workday, Oracle Recruiting (HCM Candidate Experience), Recruitee, and amazon.jobs. Listing pages with schema.org `JobPosting` JSON-LD or parseable job links are read by the generic adapter. The generic adapter also covers SmartRecruiters, iCIMS, Jobvite, and Teamtailor pages. JavaScript-only or blocked portals are marked `UNSUPPORTED` or `BLOCKED` with a diagnostic, never as an empty successful scan.

### Background worker and schedules

`python -m app.worker` claims runs from the `discovery_runs` table. It uses leases, heartbeats, crash recovery, and bounded retries, so several workers can share one database. With the scheduler enabled (the default), the worker also queues periodic work:

- scans of known companies (every 6 h)
- market exploration (daily; the first one starts as soon as the worker starts)
- frontier processing (hourly)
- reconciliation of closed jobs (daily)

Every run is bounded by its budgets: new companies, search queries, pages, results per query, company expansions, and depth. To stop scheduled crawling, use the *Background schedules* toggle at `/discovery#settings` or set `DISCOVERY_SCHEDULER_ENABLED=false`. Tiers, budgets, weights, freshness windows, intervals, and provider toggles are editable there and persisted in the database.

Freshness uses the source's posting date only. `fresh` means inside the primary window (48 h by default) and `recent` means inside the secondary discovery window (7 days). Jobs without a posting date are `unknown` and never fresh. Date-only postings are compared conservatively, and an update timestamp is never treated as a posting date. Only a complete, successful scan closes jobs that are no longer listed.

## Candidate intelligence and truthful tailoring (Phase 3)

Importing a resume, changing your preferences, or changing targeting settings queues a `candidate_analysis` run; new jobs found by scans are analysed automatically. Everything below is deterministic and explained in the UI.

**Profile** (`/profile`). A text-based PDF (parsed with pdfminer.six), DOCX, or TXT resume becomes `CandidateFact`s: each role, bullet, project, skill group, education entry, and certification, with its source and confidence. Scanned images are rejected rather than guessed. Skill levels (`STRONG`, `MODERATE`, `RUSTY`, `PROJECT`, `LISTED`) come from where a skill appears: production bullets (and how recently), projects, or only the skills list. You can override a level; overrides survive re-imports. Years of experience are computed from employment dates. Target roles, preferred locations, relocation, and compensation are editable.

**Job analysis and fit.** Each JD is split into required, responsibility, preferred, and context items (`R1`, `D2`, `P1`, `C1`), each with "any of" or "all of" semantics, years (a range in the title wins), seniority, location, and compensation. Executive titles (Director, VP, Head of) rank above staff level, and a bank-style "VP" on an individual-contributor role reads as staff. The hiring company's own name is never treated as a skill. Fit (0–100) combines skills evidence, experience, role, seniority, location, domain, and compensation. Every JD skill is labelled strong, moderate, transferable, historical, project-only, listed, or gap, with the facts that prove it. A missing option is not a gap when the JD accepts an alternative you have.

**Company target score.** This score is separate from the discovery score. It combines hiring activity, role relevance, engineering and product quality, career growth (your tier values), compensation, India/remote fit, and technical relevance. Your current employer is capped at 35.

**Application priority.** Fit (45%), company target (25%), freshness (15%), and career value (15%) produce P0–P3 or Reject.

- Gates (Reject): closed listing, non-target role, location mismatch, or fit below 45.
- Caps: underqualified or current employer → P3. Unknown or 30+ day-old posting date, adjacent role, people-management title, or a role below your level → P2.
- Minimum fit per class: P0 75, P1 65, P2 55.

Today's plan picks up to three applications (about 45 minutes each). It ranks by class, then prefers companies not yet in the plan, and never spends two slots on re-posts of the same role.

**Tailoring policy.** S-tier companies (a configurable list) get a dedicated resume for every relevant job: target role, open, and location fits. Other tiers follow per-tier rules (fit at least, priority classes, or priority score at least) set in `/discovery#settings`. A user-editable company registry maps names and aliases (for example JPMC → JPMorgan Chase) to verified career portals. A portal is trusted only after a successful scan.

**Truth constraint.**

- Resumes only select, reorder, and emphasize your facts. Experience and project bullets are used verbatim.
- The headline and one summary sentence are composed only from your current title, years from employment dates, and skills with production evidence. Total tenure is never attributed to a single skill.
- Every claim cites its source facts and the JD requirements it serves, and is verified. Technologies, numbers, leadership verbs, "production", and seniority words must be supported by the cited facts. Text copied from the JD is rejected.
- Edits are re-verified. A resume with any unsupported claim cannot be approved.
- A quality gate reports the alignment score: JD coverage, skill alignment, experience match, keyword coverage, truth confidence, and readability. It also runs ATS and seniority checks and detects keyword stuffing relative to the master resume. Truth is never traded for alignment.
- Optional AI rewording uses `LLM_PROVIDER=openai|azure`, `LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL`. Each rewrite is verified against the same facts and discarded if it adds anything. Without a key, tailoring is fully deterministic.
- Unapproved resumes are regenerated when the JD analysis or the generator improves. An approved resume is replaced only when the job description changes; the application then returns to review and must be approved again.

**Applications** (`/applications`). The flow is `RECOMMENDED → USER_REVIEW → APPROVED → READY_TO_APPLY → APPLICATION_STARTED → APPLIED → OA → RECRUITER_SCREEN → TECHNICAL → FINAL → OFFER`, ending in `REJECTED` or `WITHDRAWN` at any point. Nothing can be marked ready or applied before approval. You apply on the company's site and record outcomes. Analytics group results by company, tier, role, technology, resume positioning, industry, size, and source, and flag small samples.

**Privacy.** Your master resume and generated resumes (DOCX, HTML, Markdown, text) are stored under `DATA_DIR` (default `backend/data`, git-ignored) and in the local database. Nothing leaves the machine unless you configure an LLM key. Then only the role title, company name, up to 25 JD terms, and the bullets being reworded are sent to that provider.

## Native Windows development

The native development default is SQLite in the backend directory. Use three PowerShell terminals:

```powershell
# Terminal 1: API
Set-Location backend
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Terminal 2: background discovery and scheduling
Set-Location backend
.\.venv\Scripts\python.exe -m app.worker
```

```powershell
# Terminal 3: UI
Set-Location frontend
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run dev -- --hostname 127.0.0.1 --port 3000
```

If dependencies are not installed yet, create the backend environment with the commands below and run `npm ci` in the frontend directory.

Open `http://localhost:3000` (or `http://127.0.0.1:3000`; both origins are allowed by the API's default CORS settings). Run the migration again after pulling schema changes. Migrations preserve existing jobs.

## Discovery operating policy

- Search providers must offer a no-cost development mode. API keys are optional; no paid API is required.
- Public search availability is not guaranteed. Rate limits, robot restrictions, challenges, and inaccessible sources are recorded as errors or blocked states, never hidden behind fabricated results.
- A configured SearXNG endpoint can provide an alternative free search backend. Running your own instance is optional and not required for the default no-key mode.
- Source evidence, confidence, and discovery reasons are retained. Missing company metadata and unknown posting dates are not inferred as facts.
- The primary fresh-job queue requires a known posting date within the configured freshness window, initially 48 hours. An update timestamp is not a posting timestamp.
- Runs and schedules are bounded. Review the discovery budgets before enabling broad exploration; a free search endpoint is not permission for unlimited crawling.
- ATS recognition does not imply successful retrieval. Unsupported or JavaScript-only sources must show an explicit diagnostic rather than a successful empty scan.

## Development

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check app alembic tests
```

The backend tests run offline against an in-memory database and a mocked network. They include an acceptance test where seeding Amazon, Flipkart, and Mastercard leads to unseeded companies discovered from hiring evidence. Phase 3 tests use a synthetic resume and job descriptions, and cover profile import, matching, priority calibration, S-tier auto-tailoring, claim verification, and the approval workflow.

```powershell
cd frontend
npx tsc --noEmit
node --test tests\phase2-contract.test.cjs tests\phase3-intel.test.cjs
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"; npm run build
```

Stop `npm run dev` before running `npm run build`, because both write to `.next`.

See [Architecture](docs/architecture.md) for service boundaries, state machines, and the implementation sequence.
