"use client";

import {
  AlertCircle,
  ArrowUpRight,
  BriefcaseBusiness,
  CheckCircle2,
  ChevronDown,
  Clock3,
  DatabaseZap,
  FileCheck2,
  Inbox,
  LoaderCircle,
  MapPin,
  Menu,
  RefreshCw,
  Search,
  Send,
  ShieldAlert,
  Sparkles,
  Star,
  UserCheck,
  Users,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { apiRequest } from "@/lib/api";
import type { DashboardSummary, IngestionProvider, Job, JobsResponse, RunRead } from "@/lib/types";
import { dateLabel, freshnessLabel, jobSources, postedLabel } from "@/lib/discovery";
import { ExternalLink, RunMonitor, useRunMonitor } from "./discovery-shared";
import { TodayTargets } from "./today-targets";

const EMPTY_SUMMARY: DashboardSummary = {
  jobs_discovered_today: 0,
  high_fit_jobs: 0,
  applications_prepared: 0,
  applications_submitted: 0,
  blocked_applications: 0,
  human_reviews: 0,
  outreach_opportunities: 0,
  interviews: 0,
};

const metrics = [
  { key: "jobs_discovered_today", label: "Discovered today", icon: BriefcaseBusiness, tone: "emerald" },
  { key: "high_fit_jobs", label: "High-fit jobs", icon: Star, tone: "amber" },
  { key: "applications_prepared", label: "Prepared", icon: FileCheck2, tone: "blue" },
  { key: "applications_submitted", label: "Submitted", icon: Send, tone: "violet" },
  { key: "blocked_applications", label: "Blocked", icon: ShieldAlert, tone: "rose" },
  { key: "human_reviews", label: "Human reviews", icon: UserCheck, tone: "sky" },
  { key: "outreach_opportunities", label: "Outreach", icon: Users, tone: "orange" },
  { key: "interviews", label: "Interviews", icon: Sparkles, tone: "teal" },
] as const;

const toneClasses: Record<string, string> = {
  emerald: "bg-emerald-50 text-emerald-700",
  amber: "bg-amber-50 text-amber-700",
  blue: "bg-blue-50 text-blue-700",
  violet: "bg-violet-50 text-violet-700",
  rose: "bg-rose-50 text-rose-700",
  sky: "bg-sky-50 text-sky-700",
  orange: "bg-orange-50 text-orange-700",
  teal: "bg-teal-50 text-teal-700",
};

type LoadState = "loading" | "ready" | "error";

export function Dashboard() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<DashboardSummary>(EMPTY_SUMMARY);
  const [jobsState, setJobsState] = useState<LoadState>("loading");
  const [summaryState, setSummaryState] = useState<LoadState>("loading");
  const [jobsError, setJobsError] = useState("");
  const [summaryError, setSummaryError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);

  const loadJobs = useCallback(async (signal?: AbortSignal) => {
    setJobsState("loading");
    setJobsError("");
    try {
      const data = await apiRequest<JobsResponse>("/api/v1/jobs?limit=100", undefined, signal);
      setJobs(Array.isArray(data.items) ? data.items : []);
      setTotal(Number.isFinite(data.total) ? data.total : 0);
      setJobsState("ready");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setJobsError(error instanceof Error ? error.message : "Unable to load jobs.");
      setJobsState("error");
    }
  }, []);

  const loadSummary = useCallback(async (signal?: AbortSignal) => {
    setSummaryState("loading");
    setSummaryError("");
    try {
      const data = await apiRequest<DashboardSummary>("/api/v1/dashboard/summary", undefined, signal);
      setSummary(data);
      setSummaryState("ready");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setSummaryError(error instanceof Error ? error.message : "Unable to load the summary.");
      setSummaryState("error");
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadJobs(controller.signal);
    void loadSummary(controller.signal);
    return () => controller.abort();
  }, [loadJobs, loadSummary]);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-black/[0.06] bg-canvas/90 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1480px] items-center justify-between px-4 sm:px-6 lg:px-8">
          <a href="#overview" className="flex items-center gap-2.5" aria-label="UnJob dashboard home">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-ink text-white shadow-sm">
              <Sparkles size={18} aria-hidden="true" />
            </span>
            <span className="text-[17px] font-bold tracking-[-0.03em]">UnJob</span>
            <span className="hidden rounded-md bg-brand-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-brand-700 sm:inline">
              Phase 3
            </span>
          </a>
          <nav className="hidden items-center gap-1 rounded-xl bg-black/[0.035] p-1 md:flex" aria-label="Main navigation">
            <NavLink href="#today">Today</NavLink>
            <NavLink href="/opportunities">Opportunities</NavLink>
            <NavLink href="/applications">Applications</NavLink>
            <NavLink href="/profile">Profile</NavLink>
            <NavLink href="#jobs">Jobs</NavLink>
            <NavLink href="/companies">Companies</NavLink>
            <NavLink href="/discovery">Discovery</NavLink>
          </nav>
          <div className="flex items-center gap-2">
            <div className="hidden items-center gap-2 rounded-full border border-black/[0.07] bg-white px-3 py-1.5 text-xs text-slate-600 sm:flex">
              <span className="h-2 w-2 rounded-full bg-emerald-500 ring-4 ring-emerald-500/10" />
              Live workspace
            </div>
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              className="grid h-10 w-10 place-items-center rounded-xl border border-black/[0.08] bg-white md:hidden"
              aria-expanded={menuOpen}
              aria-label="Toggle navigation"
            >
              {menuOpen ? <X size={19} /> : <Menu size={19} />}
            </button>
          </div>
        </div>
        {menuOpen && (
          <nav className="border-t border-black/[0.06] bg-white p-3 md:hidden" aria-label="Mobile navigation">
            {["today", "jobs", "ingestion"].map((item) => (
              <a
                key={item}
                href={`#${item}`}
                onClick={() => setMenuOpen(false)}
                className="block rounded-lg px-3 py-2 text-sm font-medium capitalize text-slate-700 hover:bg-slate-50"
              >
                {item}
              </a>
            ))}
            <a href="/opportunities" className="block rounded-lg px-3 py-2 text-sm font-medium text-slate-700">Opportunities</a>
            <a href="/applications" className="block rounded-lg px-3 py-2 text-sm font-medium text-slate-700">Applications</a>
            <a href="/profile" className="block rounded-lg px-3 py-2 text-sm font-medium text-slate-700">Profile</a>
            <a href="/companies" className="block rounded-lg px-3 py-2 text-sm font-medium text-slate-700">Companies</a>
            <a href="/discovery" className="block rounded-lg px-3 py-2 text-sm font-medium text-slate-700">Discovery</a>
          </nav>
        )}
      </header>

      <main className="mx-auto max-w-[1480px] space-y-12 px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
        <section id="overview" className="scroll-mt-24">
          <div className="mb-7 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
            <div>
              <p className="mb-2 text-xs font-bold uppercase tracking-[0.18em] text-brand-600">Opportunity command center</p>
              <h1 className="text-3xl font-semibold tracking-[-0.04em] text-ink sm:text-4xl">
                Good {getTimeOfDay()}, welcome back.
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600 sm:text-base">
                Your job search, distilled. Review the strongest matches and keep applications moving.
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                void loadJobs();
                void loadSummary();
              }}
              disabled={jobsState === "loading" || summaryState === "loading"}
              className="inline-flex h-10 items-center justify-center gap-2 self-start rounded-xl border border-black/[0.08] bg-white px-4 text-sm font-semibold shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 sm:self-auto"
            >
              <RefreshCw size={15} className={jobsState === "loading" || summaryState === "loading" ? "animate-spin" : ""} />
              Refresh data
            </button>
          </div>

          {summaryState === "error" ? (
            <ErrorPanel message={summaryError} onRetry={() => void loadSummary()} compact />
          ) : (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-8">
              {metrics.map(({ key, label, icon: Icon, tone }) => (
                <article key={key} className="rounded-2xl border border-black/[0.055] bg-white p-4 shadow-card">
                  <div className={`mb-5 grid h-9 w-9 place-items-center rounded-xl ${toneClasses[tone]}`}>
                    <Icon size={17} aria-hidden="true" />
                  </div>
                  {summaryState === "loading" ? (
                    <div className="skeleton mb-2 h-8 w-14 rounded-md" />
                  ) : (
                    <p className="text-2xl font-semibold tracking-[-0.04em]">{formatNumber(summary[key])}</p>
                  )}
                  <p className="mt-1 truncate text-xs font-medium text-slate-500" title={label}>{label}</p>
                </article>
              ))}
            </div>
          )}
        </section>

        <TodayTargets />

        <JobsSection
          jobs={jobs}
          total={total}
          state={jobsState}
          error={jobsError}
          onRetry={() => void loadJobs()}
        />
        <IngestionSection onIngested={() => { void loadJobs(); void loadSummary(); }} />
      </main>

      <footer className="border-t border-black/[0.06] px-4 py-7 text-center text-xs text-slate-500">
        UnJob workspace · Built to keep your search focused
      </footer>
    </div>
  );
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} className="rounded-lg px-4 py-2 text-xs font-semibold text-slate-600 transition hover:bg-white hover:text-ink hover:shadow-sm">
      {children}
    </a>
  );
}

function JobsSection({
  jobs,
  total,
  state,
  error,
  onRetry,
}: {
  jobs: Job[];
  total: number;
  state: LoadState;
  error: string;
  onRetry: () => void;
}) {
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState("all");
  const [source, setSource] = useState("all");
  const [freshness, setFreshness] = useState("all");
  const [minimumScore, setMinimumScore] = useState("all");
  const [sort, setSort] = useState("score");
  const [expandedJob, setExpandedJob] = useState<string | number | null>(null);

  const sources = useMemo(
    () => Array.from(new Set(jobs.flatMap(jobSources))).sort(),
    [jobs],
  );

  const filteredJobs = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const result = jobs.filter((job) => {
      const searchText = [job.company, job.title, job.location, job.ats].filter(Boolean).join(" ").toLowerCase();
      const score = job.overall_score ?? 0;
      return (
        (!normalizedQuery || searchText.includes(normalizedQuery)) &&
        (remote === "all" || normalize(job.remote_status) === normalize(remote)) &&
        (source === "all" || jobSources(job).includes(source)) &&
        (freshness === "all" || (job.posted_at ? job.freshness_status ?? "unknown" : "unknown") === freshness) &&
        (minimumScore === "all" || score >= Number(minimumScore))
      );
    });
    return result.sort((a, b) => {
      if (sort === "newest") return dateValue(b.discovered_at) - dateValue(a.discovered_at);
      // Undated jobs sort last: discovery time is never used as a posting time.
      if (sort === "posted") return (dateValue(b.posted_at) || -1) - (dateValue(a.posted_at) || -1);
      if (sort === "company") return a.company.localeCompare(b.company);
      return (b.overall_score ?? -1) - (a.overall_score ?? -1);
    });
  }, [freshness, jobs, minimumScore, query, remote, sort, source]);

  const clearFilters = () => {
    setQuery("");
    setRemote("all");
    setSource("all");
    setFreshness("all");
    setMinimumScore("all");
  };
  const hasFilters = Boolean(query || remote !== "all" || source !== "all" || freshness !== "all" || minimumScore !== "all");

  return (
    <section id="jobs" className="scroll-mt-24">
      <SectionHeading
        eyebrow="Opportunity pipeline"
        title="Jobs"
        description={state === "ready" ? `${formatNumber(total)} discovered · showing ${filteredJobs.length} of ${jobs.length} loaded` : "Search and prioritize new opportunities."}
      />
      <div className="overflow-hidden rounded-2xl border border-black/[0.06] bg-white shadow-card">
        <div className="grid gap-3 border-b border-black/[0.06] bg-slate-50/60 p-4 md:grid-cols-3 lg:grid-cols-[minmax(220px,1fr)_150px_150px_170px_140px_170px]">
          <label className="relative">
            <span className="sr-only">Search jobs</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search title, company, location…"
              className="h-10 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-3 text-sm text-ink placeholder:text-slate-400"
            />
          </label>
          <FilterSelect label="Workplace" value={remote} onChange={setRemote}>
            <option value="all">All workplaces</option>
            <option value="remote">Remote</option>
            <option value="hybrid">Hybrid</option>
            <option value="onsite">On-site</option>
          </FilterSelect>
          <FilterSelect label="Source" value={source} onChange={setSource}>
            <option value="all">All sources</option>
            {sources.map((value) => <option key={value} value={value}>{titleCase(value)}</option>)}
          </FilterSelect>
          <FilterSelect label="Posting freshness" value={freshness} onChange={setFreshness}>
            <option value="all">Any posting date</option>
            <option value="fresh">Fresh (primary window)</option>
            <option value="recent">Recent (secondary window)</option>
            <option value="stale">Older</option>
            <option value="unknown">Posting date unknown</option>
          </FilterSelect>
          <FilterSelect label="Minimum score" value={minimumScore} onChange={setMinimumScore}>
            <option value="all">Any score</option>
            <option value="80">80+ score</option>
            <option value="60">60+ score</option>
            <option value="40">40+ score</option>
          </FilterSelect>
          <FilterSelect label="Sort jobs" value={sort} onChange={setSort}>
            <option value="score">Best fit first</option>
            <option value="posted">Most recently posted</option>
            <option value="newest">Newest discovered</option>
            <option value="company">Company A–Z</option>
          </FilterSelect>
        </div>

        {state === "loading" && <JobsSkeleton />}
        {state === "error" && <div className="p-6"><ErrorPanel message={error} onRetry={onRetry} /></div>}
        {state === "ready" && filteredJobs.length === 0 && (
          <EmptyJobs hasFilters={hasFilters} onClear={clearFilters} />
        )}
        {state === "ready" && filteredJobs.length > 0 && (
          <>
            <div className="hidden overflow-x-auto lg:block">
              <table className="w-full min-w-[980px] border-collapse text-left">
                <thead>
                  <tr className="border-b border-black/[0.06] text-[11px] font-bold uppercase tracking-wider text-slate-400">
                    <th className="px-5 py-3.5">Role</th>
                    <th className="px-4 py-3.5">Location</th>
                    <th className="px-4 py-3.5">Source</th>
                    <th className="px-4 py-3.5">Discovered</th>
                    <th className="px-4 py-3.5">Fit</th>
                    <th className="px-4 py-3.5">Status</th>
                    <th className="px-5 py-3.5 text-right">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredJobs.map((job) => (
                    <JobTableRow
                      key={job.id}
                      job={job}
                      expanded={expandedJob === job.id}
                      onToggle={() => setExpandedJob(expandedJob === job.id ? null : job.id)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="divide-y divide-black/[0.06] lg:hidden">
              {filteredJobs.map((job) => <JobCard key={job.id} job={job} />)}
            </div>
          </>
        )}
      </div>
      {state === "ready" && total > jobs.length && (
        <p className="mt-3 text-right text-xs text-slate-500">
          Showing the first {jobs.length} results returned by the API.
        </p>
      )}
    </section>
  );
}

function JobTableRow({ job, expanded, onToggle }: { job: Job; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="border-b border-black/[0.05] transition hover:bg-brand-50/30">
        <td className="px-5 py-4">
          <button type="button" onClick={onToggle} aria-expanded={expanded} className="group flex max-w-[340px] items-center gap-3 text-left">
            <CompanyMark company={job.company} />
            <span className="min-w-0">
              <span className="block truncate text-sm font-semibold text-ink group-hover:text-brand-700">{job.title}</span>
              <span className="mt-0.5 block truncate text-xs text-slate-500">{job.company}</span>
            </span>
            <ChevronDown size={14} className={`shrink-0 text-slate-400 transition ${expanded ? "rotate-180" : ""}`} />
          </button>
        </td>
        <td className="px-4 py-4">
          <p className="max-w-[180px] truncate text-xs font-medium text-slate-700">{job.location || "Not specified"}</p>
          {job.remote_status && <p className="mt-1 text-[11px] text-slate-400">{titleCase(job.remote_status)}</p>}
        </td>
        <td className="px-4 py-4">
          <p className="text-xs font-medium text-slate-700">{jobSources(job).map(titleCase).join(", ") || "Unknown"}</p>
          {job.ats && <p className="mt-1 text-[11px] text-slate-400">{titleCase(job.ats)}</p>}
        </td>
        <td className="px-4 py-4 text-xs text-slate-600">
          {relativeTime(job.discovered_at)}
          <p className="mt-1 text-[11px] text-slate-400">Posted: {postedLabel(job.posted_at, job.posted_at_precision)}</p>
          <p className="mt-1 text-[11px] font-medium">{freshnessLabel(job)}</p>
        </td>
        <td className="px-4 py-4"><ScoreBadge score={job.overall_score} /></td>
        <td className="px-4 py-4"><StatusBadge status={job.status} /></td>
        <td className="px-5 py-4 text-right">
          {job.application_url ? (
            <a
              href={job.application_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-ink px-3 text-xs font-semibold text-white transition hover:bg-brand-700"
            >
              View <ArrowUpRight size={13} />
            </a>
          ) : <span className="text-xs text-slate-400">Unavailable</span>}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-black/[0.06] bg-slate-50/70">
          <td colSpan={7} className="px-5 py-4">
            <div className="grid gap-5 md:grid-cols-2">
              <div>
                <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-400">Why it fits</p>
                {job.score_reasons?.length ? (
                  <ul className="space-y-1.5">
                    {job.score_reasons.map((reason, index) => (
                      <li key={`${reason}-${index}`} className="flex gap-2 text-xs leading-5 text-slate-600">
                        <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-brand-500" />{reason}
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-xs text-slate-500">No scoring reasons available.</p>}
              </div>
              <div>
                <p className="mb-2 text-[11px] font-bold uppercase tracking-wider text-slate-400">Score breakdown</p>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(job.score_breakdown ?? {}).length ? Object.entries(job.score_breakdown).map(([key, value]) => (
                    <span key={key} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600">
                      {titleCase(key)} <strong className="ml-1 text-ink">{value ?? "—"}</strong>
                    </span>
                  )) : <span className="text-xs text-slate-500">No breakdown available.</span>}
                </div>
              </div>
            </div>
            <div className="mt-4 flex flex-wrap gap-3 text-xs text-slate-600">
              {job.company_id && <a href={`/companies?company=${encodeURIComponent(job.company_id)}`} className="text-brand-700 underline">View company evidence</a>}
              <span>Role category: {job.role_category || "UNKNOWN"}</span>
              {job.source_updated_at && <span>Source last updated: {dateLabel(job.source_updated_at)} (not a posting date)</span>}
              {job.sources?.map((source, index) => <ExternalLink key={`${source.source}:${source.external_id}`} url={source.source_url}>{`Source evidence ${index + 1} (${source.source})`}</ExternalLink>)}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function JobCard({ job }: { job: Job }) {
  return (
    <article className="p-4">
      <div className="flex items-start gap-3">
        <CompanyMark company={job.company} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{job.title}</p>
          <p className="mt-0.5 truncate text-xs text-slate-500">{job.company}</p>
        </div>
        <ScoreBadge score={job.overall_score} />
      </div>
      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-500">
        <span className="flex items-center gap-1.5"><MapPin size={13} />{job.location || "Not specified"}</span>
        <span className="flex items-center gap-1.5"><Clock3 size={13} />Discovered {relativeTime(job.discovered_at)}</span>
        <span>Posted: {postedLabel(job.posted_at, job.posted_at_precision)} · {freshnessLabel(job)}</span>
        <span>Source: {jobSources(job).map(titleCase).join(", ") || "UNKNOWN"}</span>
      </div>
      <div className="mt-4 flex items-center justify-between">
        <StatusBadge status={job.status} />
        {job.application_url && (
          <a href={job.application_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs font-bold text-brand-700">
            View job <ArrowUpRight size={13} />
          </a>
        )}
      </div>
    </article>
  );
}

function IngestionSection({ onIngested }: { onIngested: () => void }) {
  const [provider, setProvider] = useState<IngestionProvider>("greenhouse");
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const monitor = useRunMonitor(onIngested);

  const providerDetails = provider === "greenhouse"
    ? { label: "Board token", placeholder: "e.g. stripe", help: "The token from boards.greenhouse.io/{token}", field: "board_token" }
    : { label: "Company slug", placeholder: "e.g. figma", help: "The slug from jobs.lever.co/{slug}", field: "company_slug" };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedValue = value.trim();
    if (!trimmedValue) {
      setError(`${providerDetails.label} is required.`);
      return;
    }
    setSubmitting(true);
    setError("");
    setSuccess("");
    try {
      const run = await apiRequest<RunRead>(`/api/v1/ingestion/${provider}`, {
        method: "POST",
        body: JSON.stringify({ [providerDetails.field]: trimmedValue }),
      });
      monitor.track(run);
      setSuccess(`${titleCase(provider)} ingestion queued (${run.id}). Jobs refresh when the background run finishes.`);
      setValue("");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Unable to start ingestion.");
    } finally {
      setSubmitting(false);
    }
  }

  function selectProvider(nextProvider: IngestionProvider) {
    setProvider(nextProvider);
    setValue("");
    setError("");
    setSuccess("");
  }

  return (
    <section id="ingestion" className="scroll-mt-24 pb-4">
      <SectionHeading
        eyebrow="Data sources"
        title="Ingest job boards"
        description="Pull the latest openings from a supported applicant tracking system."
      />
      <div className="grid overflow-hidden rounded-2xl border border-black/[0.06] bg-white shadow-card lg:grid-cols-[0.85fr_1.15fr]">
        <div className="border-b border-black/[0.06] bg-ink p-6 text-white sm:p-8 lg:border-b-0 lg:border-r">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-white/10">
            <DatabaseZap size={21} />
          </div>
          <h3 className="mt-8 text-xl font-semibold tracking-tight">Connect a source</h3>
          <p className="mt-2 max-w-md text-sm leading-6 text-white/60">
            Trigger a fresh import on demand. Duplicate roles are handled by the ingestion service.
          </p>
          <div className="mt-7 space-y-3 text-xs text-white/65">
            <p className="flex items-center gap-2"><CheckCircle2 size={15} className="text-emerald-400" />No credentials required</p>
            <p className="flex items-center gap-2"><CheckCircle2 size={15} className="text-emerald-400" />Jobs refresh automatically when the run finishes</p>
          </div>
        </div>
        <div className="p-5 sm:p-8">
          <div className="mb-7 grid grid-cols-2 gap-2 rounded-xl bg-slate-100 p-1">
            {(["greenhouse", "lever"] as IngestionProvider[]).map((name) => (
              <button
                type="button"
                key={name}
                onClick={() => selectProvider(name)}
                className={`rounded-lg px-4 py-2.5 text-sm font-semibold transition ${
                  provider === name ? "bg-white text-ink shadow-sm" : "text-slate-500 hover:text-slate-700"
                }`}
              >
                {titleCase(name)}
              </button>
            ))}
          </div>
          <form onSubmit={submit}>
            <label htmlFor="ingestion-value" className="text-sm font-semibold text-ink">{providerDetails.label}</label>
            <p className="mt-1 text-xs text-slate-500">{providerDetails.help}</p>
            <input
              id="ingestion-value"
              value={value}
              onChange={(event) => {
                setValue(event.target.value);
                setError("");
                setSuccess("");
              }}
              disabled={submitting}
              autoComplete="off"
              placeholder={providerDetails.placeholder}
              className="mt-3 h-11 w-full rounded-xl border border-slate-200 bg-white px-3.5 text-sm shadow-sm placeholder:text-slate-400 disabled:bg-slate-50"
            />
            <div aria-live="polite" className="mt-3 min-h-6">
              {error && <p className="flex items-start gap-2 text-xs font-medium text-rose-700"><AlertCircle size={15} className="shrink-0" />{error}</p>}
              {success && <p className="flex items-start gap-2 text-xs font-medium text-emerald-700"><CheckCircle2 size={15} className="shrink-0" />{success}</p>}
            </div>
            <button
              type="submit"
              disabled={submitting}
              className="mt-3 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-brand-600 px-5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
            >
              {submitting ? <LoaderCircle size={16} className="animate-spin" /> : <DatabaseZap size={16} />}
              {submitting ? "Starting ingestion…" : `Ingest from ${titleCase(provider)}`}
            </button>
          </form>
        </div>
      </div>
      <div className="mt-4"><RunMonitor monitor={monitor} /></div>
    </section>
  );
}

function SectionHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return (
    <div className="mb-5">
      <p className="mb-1.5 text-[11px] font-bold uppercase tracking-[0.18em] text-brand-600">{eyebrow}</p>
      <h2 className="text-2xl font-semibold tracking-[-0.035em]">{title}</h2>
      <p className="mt-1.5 text-sm text-slate-500">{description}</p>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <label className="relative">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-10 w-full appearance-none rounded-xl border border-slate-200 bg-white px-3 pr-8 text-sm text-slate-600"
      >
        {children}
      </select>
      <ChevronDown className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" size={14} />
    </label>
  );
}

function CompanyMark({ company }: { company: string }) {
  return (
    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-black/[0.06] bg-slate-50 text-xs font-bold text-slate-600">
      {(company || "?").slice(0, 2).toUpperCase()}
    </span>
  );
}

function ScoreBadge({ score }: { score: number | null }) {
  if (score === null || score === undefined) return <span className="text-xs text-slate-400">Not scored</span>;
  const style = score >= 80
    ? "bg-emerald-50 text-emerald-700 ring-emerald-600/10"
    : score >= 60
      ? "bg-amber-50 text-amber-700 ring-amber-600/10"
      : "bg-slate-100 text-slate-600 ring-slate-600/10";
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset ${style}`}>{Math.round(score)}</span>;
}

function StatusBadge({ status }: { status: string }) {
  const normalized = normalize(status);
  const style = normalized.includes("submit") || normalized.includes("interview")
    ? "bg-emerald-50 text-emerald-700"
    : normalized.includes("block") || normalized.includes("reject")
      ? "bg-rose-50 text-rose-700"
      : normalized.includes("prepar") || normalized.includes("review")
        ? "bg-blue-50 text-blue-700"
        : "bg-slate-100 text-slate-600";
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-[11px] font-semibold ${style}`}>{titleCase(status || "New")}</span>;
}

function JobsSkeleton() {
  return (
    <div className="divide-y divide-black/[0.05]" aria-label="Loading jobs">
      {Array.from({ length: 5 }).map((_, index) => (
        <div key={index} className="flex items-center gap-4 p-5">
          <div className="skeleton h-9 w-9 shrink-0 rounded-lg" />
          <div className="flex-1">
            <div className="skeleton h-4 w-2/5 rounded" />
            <div className="skeleton mt-2 h-3 w-1/4 rounded" />
          </div>
          <div className="skeleton hidden h-5 w-20 rounded-full sm:block" />
        </div>
      ))}
    </div>
  );
}

function EmptyJobs({ hasFilters, onClear }: { hasFilters: boolean; onClear: () => void }) {
  return (
    <div className="grid min-h-72 place-items-center px-5 py-12 text-center">
      <div>
        <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-500">
          {hasFilters ? <Search size={21} /> : <Inbox size={21} />}
        </span>
        <h3 className="mt-4 text-sm font-semibold">{hasFilters ? "No matching jobs" : "No jobs discovered yet"}</h3>
        <p className="mx-auto mt-1 max-w-sm text-xs leading-5 text-slate-500">
          {hasFilters ? "Try broadening your filters or clearing the search." : "Use the ingestion form below to bring in your first opportunities."}
        </p>
        {hasFilters && <button type="button" onClick={onClear} className="mt-4 text-xs font-bold text-brand-700 hover:underline">Clear all filters</button>}
      </div>
    </div>
  );
}

function ErrorPanel({ message, onRetry, compact = false }: { message: string; onRetry: () => void; compact?: boolean }) {
  return (
    <div className={`flex flex-col items-start justify-between gap-4 rounded-xl border border-rose-200 bg-rose-50 text-rose-900 sm:flex-row sm:items-center ${compact ? "p-4" : "p-5"}`} role="alert">
      <div className="flex items-start gap-3">
        <AlertCircle size={18} className="mt-0.5 shrink-0 text-rose-600" />
        <div>
          <p className="text-sm font-semibold">We couldn&apos;t load this data</p>
          <p className="mt-0.5 text-xs leading-5 text-rose-700">{message}</p>
        </div>
      </div>
      <button type="button" onClick={onRetry} className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-white px-3 py-2 text-xs font-bold shadow-sm ring-1 ring-rose-200">
        <RefreshCw size={13} />Try again
      </button>
    </div>
  );
}

function titleCase(value: string) {
  return value.replace(/[_-]+/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function normalize(value: string | null | undefined) {
  return (value || "").toLowerCase().replace(/[\s_-]/g, "");
}

function dateValue(value: string | null) {
  const timestamp = value ? new Date(value).getTime() : 0;
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function relativeTime(value: string | null, ageHours: number | null = null) {
  let hours = ageHours;
  if (hours === null || hours === undefined) {
    const timestamp = dateValue(value);
    if (!timestamp) return "Unknown";
    hours = Math.max(0, (Date.now() - timestamp) / 3_600_000);
  }
  if (hours < 1) return "Just now";
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat("en-US", { notation: value >= 10_000 ? "compact" : "standard" }).format(value ?? 0);
}

function getTimeOfDay() {
  const hour = new Date().getHours();
  if (hour < 12) return "morning";
  if (hour < 18) return "afternoon";
  return "evening";
}
