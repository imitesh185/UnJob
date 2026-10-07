# UnJob frontend — Phase 3

The dashboard at `/` opens with **Today's targets**: up to three applications
for a two-hour session, each with fit, company target, and priority scores, the
reasons, and the state of its tailored resume. Phase 3 pages:

- `/opportunities`: every analysed job, ranked P0–P3 (rejected jobs are hidden
  unless you ask for them with their reasons). You can filter by priority, tier,
  minimum fit, and tailored-resume state.
- `/opportunities/[id]`: why the job fits (per-skill evidence, gaps, and accepted
  alternatives), the application strategy, the JD analysis, the company target
  score, and the tailored resume. The resume view has a master-vs-tailored
  comparison, every claim with its source facts and verification result,
  editable claims (re-verified on save), approve and reject actions, and
  DOCX/HTML/Markdown/text downloads.
- `/applications`: the application board (sorted by priority) and outcome analytics
  with low-sample markers.
- `/profile`: import the master resume, edit preferences, review skills by
  evidence, and override levels.

Company Universe is at `/companies`; its table can be sorted by your target score
and its edit form can rename a company (the old name becomes an alias). The
discovery feed, frontier, background runs, and settings are at `/discovery`.
`/discovery#settings` includes the S-tier list, per-tier tailoring policy, and
the company registry. Both desktop and mobile navigation link these pages.
Nothing in the UI submits an application or sends a message.

Set `NEXT_PUBLIC_API_URL` to the backend origin before starting or building:

```powershell
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run dev
```

Container builds must supply `NEXT_PUBLIC_API_URL` as a build argument. The
builder stage exposes that argument before `next build`, because Next.js
embeds public environment variables into browser bundles at build time;
changing only the running container's environment cannot update the client URL.

Paste one seed company per line and optionally choose a server-configured tier.
Company details show:

- official domains and career portals
- each career source's platform, how it was found and why, its scan state, and its errors
- discovery reasons and score breakdowns
- evidence events and relationships

Unknown information is explicitly unknown, and no demo companies or dates are
generated. Adding a career source URL queues a company scan and tracks it.
Saving company edits sends only the fields you changed. Filters and pagination
operate through the companies API.

Use `/discovery#runs` to queue exploration, frontier processing, scans, or
reconciliation. Budgets are editable whole numbers within the server's bounds;
0 disables that kind of work for a run. `/discovery#settings` configures:

- tiers
- primary and secondary freshness windows
- default budgets
- company score weights and frontier priority points
- the worker scheduler and its intervals
- provider toggles

Provider availability comes from the backend; optional keyed providers do not
require keys to use this interface.

Run creation, including legacy Greenhouse/Lever ingestion, queues background work.
The UI never treats HTTP 202 as completed ingestion. Selected run status and the
run list poll every three seconds, stopping after 60 checks, terminal state, or
request failure. Resume checks or refresh manually for longer runs. When a tracked
run reaches any terminal state, the relevant data refreshes. Its result is labeled
*Completed*, *Finished with errors — results are partial*, or *Failed*, never as
success when errors occurred. Workers must be running on the backend for queued
work to progress.

Fresh job counts use the backend's configurable freshness window and known
posting dates. Discovery time is displayed separately from posting time.
Date-only postings are shown without an invented time of day. Jobs can be
filtered by posting freshness, including "posting date unknown". Sources are
read from `sources[]` (`source`, `external_id`, `source_url`, ...), with the
ATS as a fallback. API timestamps are UTC (`Z`) and are displayed in local time.

Validation (stop `npm run dev` first; `next build` and `next dev` share `.next`):

```powershell
npx tsc --noEmit
node --test tests\phase2-contract.test.cjs tests\phase3-intel.test.cjs
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run build
```
