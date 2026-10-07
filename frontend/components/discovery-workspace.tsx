"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { apiRequest } from "@/lib/api";
import { budgetLabels, budgetLimits, dateLabel, errorMessage, isAbort, runTerminal, validatedBudgets } from "@/lib/discovery";
import type { DiscoverySettings, DiscoverySettingsPatch, DiscoverySummary, EventRead, ExplorationBudgets, FrontierRead, PageResponse, RunRead } from "@/lib/types";
import { activeButtonClass, Badge, buttonClass, ErrorNotice, ExternalLink, Field, inputClass, panelClass, PhaseShell, primaryClass, RunCard, RunMonitor, useRunMonitor } from "./discovery-shared";
import { TargetingSettings } from "./targeting-settings";

type View = "feed" | "frontier" | "runs" | "settings";
const views: View[] = ["feed", "frontier", "runs", "settings"];
type BudgetDraft = Record<keyof ExplorationBudgets, string>;
const budgetKeys = Object.keys(budgetLabels) as (keyof ExplorationBudgets)[];

function budgetDraft(budgets: ExplorationBudgets): BudgetDraft {
  return Object.fromEntries(budgetKeys.map((key) => [key, String(budgets[key])])) as BudgetDraft;
}

function BudgetFields({ draft, onChange }: { draft: BudgetDraft; onChange: (draft: BudgetDraft) => void }) {
  return <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{budgetKeys.map((key) => <Field key={key} label={`${budgetLabels[key]} (0–${budgetLimits[key].toLocaleString()})`}><input className={inputClass} type="number" required min={0} max={budgetLimits[key]} step={1} value={draft[key]} onChange={(event) => onChange({ ...draft, [key]: event.target.value })} /></Field>)}</div>;
}

function usePagedData<T>(path: string, revision: number, pollRuns = false) {
  const [data, setData] = useState<PageResponse<T>>({ items: [], total: 0 });
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [paused, setPaused] = useState(false);
  const [retryRevision, setRetryRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let checks = 0;
    setLoading(true); setError(""); setPaused(false);
    const load = async () => {
      try {
        const next = await apiRequest<PageResponse<T>>(`${path}?limit=100&offset=${offset}`, undefined, controller.signal);
        if (controller.signal.aborted) return;
        setData(next); setLoading(false);
        if (pollRuns && (next.items as RunRead[]).some((run) => !runTerminal(run))) {
          checks += 1;
          if (checks >= 60) { setPaused(true); return; }
          timer = setTimeout(load, 3000);
        }
      } catch (requestError) { if (!isAbort(requestError)) { setError(errorMessage(requestError)); setLoading(false); } }
    };
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, offset, revision, retryRevision, pollRuns]);
  return { ...data, offset, setOffset, loading, error, paused, retry: () => setRetryRevision((value) => value + 1) };
}

function Pagination({ page }: { page: { offset: number; setOffset: (offset: number) => void; total: number; loading: boolean; items: unknown[] } }) {
  return <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>{page.total ? `${page.offset + 1}–${Math.min(page.offset + page.items.length, page.total)} of ${page.total}` : "0 results"}</span><div className="flex gap-2"><button className={buttonClass} disabled={page.loading || page.offset === 0} onClick={() => page.setOffset(Math.max(0, page.offset - 100))}>Previous</button><button className={buttonClass} disabled={page.loading || page.offset + 100 >= page.total} onClick={() => page.setOffset(page.offset + 100)}>Next</button></div></div>;
}

export function DiscoveryWorkspace() {
  const [view, setView] = useState<View>("feed");
  const [revision, setRevision] = useState(0);
  const [settings, setSettings] = useState<DiscoverySettings | null>(null);
  const [settingsError, setSettingsError] = useState("");
  const [summary, setSummary] = useState<DiscoverySummary | null>(null);
  const [summaryError, setSummaryError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const monitor = useRunMonitor(refresh);
  const feed = usePagedData<EventRead>("/api/v1/discovery/feed", revision);
  const frontier = usePagedData<FrontierRead>("/api/v1/discovery/frontier", revision);
  const runs = usePagedData<RunRead>("/api/v1/discovery/runs", revision, true);
  const previousRuns = useRef(new Map<string, RunRead>());

  useEffect(() => {
    let finished = false;
    for (const run of runs.items) {
      const previous = previousRuns.current.get(run.id);
      // Partial runs still save companies and jobs, so any newly finished run triggers a refresh.
      if (previous && !runTerminal(previous) && runTerminal(run)) finished = true;
      previousRuns.current.set(run.id, run);
    }
    if (finished) refresh();
  }, [runs.items, refresh]);

  useEffect(() => {
    const updateView = () => { const next = window.location.hash.slice(1); if (views.includes(next as View)) setView(next as View); };
    updateView();
    window.addEventListener("hashchange", updateView);
    return () => window.removeEventListener("hashchange", updateView);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setSummaryError(""); setSettingsError("");
    void apiRequest<DiscoverySummary>("/api/v1/discovery/summary", undefined, controller.signal).then(setSummary).catch((error) => { if (!isAbort(error)) setSummaryError(errorMessage(error)); });
    void apiRequest<DiscoverySettings>("/api/v1/discovery/settings", undefined, controller.signal).then(setSettings).catch((error) => { if (!isAbort(error)) setSettingsError(errorMessage(error)); });
    return () => controller.abort();
  }, [revision]);

  async function review(event: EventRead, action: "accept" | "ignore") {
    setBusy(true); setActionError("");
    try { await apiRequest<EventRead>(`/api/v1/discovery/feed/${encodeURIComponent(event.id)}/review`, { method: "POST", body: JSON.stringify({ action }) }); refresh(); }
    catch (error) { setActionError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  async function explore(item: FrontierRead) {
    setBusy(true); setActionError("");
    try { monitor.track(await apiRequest<RunRead>(`/api/v1/discovery/frontier/${encodeURIComponent(item.id)}/explore`, { method: "POST" })); runs.retry(); }
    catch (error) { setActionError(errorMessage(error)); }
    finally { setBusy(false); }
  }

  return <PhaseShell current="discovery" title="Discovery" description="Find companies with evidence of data engineering hiring now. Review traceable signals, explore the frontier, and inspect bounded background work.">
    <section aria-label="Discovery summary">
      <ErrorNotice message={summaryError} retry={refresh} />
      {summary ? <>
        <div className={`${panelClass} mb-3 flex flex-wrap items-center justify-between gap-3`}>
          <div><p className="text-xs font-bold uppercase tracking-widest text-brand-600">New companies discovered</p><p className="mt-1 text-lg font-semibold">{summary.companies_discovered_today.toLocaleString()} discovered today</p><p className="text-sm text-slate-600">{summary.high_confidence} high-confidence · {summary.medium_confidence} medium-confidence · {summary.low_confidence} low-confidence</p></div>
          <a href="#feed" className={primaryClass}>Review {summary.pending_reviews.toLocaleString()} pending signal{summary.pending_reviews === 1 ? "" : "s"}</a>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">{([
          ["Companies tracked", summary.companies_total], ["Frontier pending", summary.frontier_pending], ["Active runs", summary.active_runs], ["Fresh data jobs", summary.fresh_data_jobs],
        ] as const).map(([label, value]) => <div className={panelClass} key={label}><p className="text-xs text-slate-500">{label}</p><p className="mt-2 text-2xl font-semibold">{value.toLocaleString()}</p></div>)}</div>
      </> : !summaryError && <p role="status" className="text-sm text-slate-500">Loading discovery summary…</p>}
    </section>
    <RunMonitor monitor={monitor} />
    <nav className="flex flex-wrap gap-2" aria-label="Discovery views">{views.map((item) => <a key={item} href={`#${item}`} aria-current={view === item ? "page" : undefined} className={`${view === item ? activeButtonClass : buttonClass} capitalize`}>{item === "feed" ? "Discovery feed" : item}</a>)}<button className={`${buttonClass} ml-auto`} onClick={refresh}>Refresh data</button></nav>
    <ErrorNotice message={actionError} />
    {view === "feed" && <section id="feed" className={panelClass} aria-labelledby="feed-heading">
      <h2 id="feed-heading" className="text-lg font-semibold">Discovery feed</h2><p className="mt-1 text-sm text-slate-500">Accept evidenced candidates into your universe, or ignore irrelevant signals. A discovery score is not a job-fit score.</p>
      <ErrorNotice message={feed.error} retry={feed.retry} />
      {feed.loading ? <p role="status" className="py-6">Loading discovery feed…</p> : !feed.error && !feed.items.length ? <p className="py-6 text-sm text-slate-500">No discovery events yet. Add seeds or queue an exploration run. Providers report only observed evidence.</p> : !feed.error && <div className="mt-4 grid gap-4 lg:grid-cols-2">{feed.items.map((event) => <article key={event.id} className="rounded-xl border border-slate-200 p-4">
        <div className="flex flex-wrap justify-between gap-2"><h3 className="font-semibold">{event.company_name || "UNKNOWN company"}</h3><Badge>{event.status}</Badge></div>
        <p className="mt-3 text-sm font-medium">{event.reason || "UNKNOWN reason"}</p><p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">{event.evidence || "UNKNOWN — no evidence recorded"}</p>
        <p className="mt-3 text-sm">Discovery score: {event.discovery_score} · Confidence: {(event.confidence * 100).toFixed(0)}%</p>
        <p className="mt-2 text-xs text-slate-500">Source: {event.source || "UNKNOWN"} · First seen: {dateLabel(event.discovered_at)} · Last seen: {dateLabel(event.last_seen_at)}{event.observation_count > 1 ? ` · Observed ${event.observation_count}×` : ""}</p><div className="mt-1 text-xs"><ExternalLink url={event.source_url}>View source evidence</ExternalLink></div>
        <div className="mt-4 flex flex-wrap gap-2">{event.company_id && <a className={buttonClass} href={`/companies?company=${encodeURIComponent(event.company_id)}`}>Explore company</a>}<button className={primaryClass} disabled={busy || event.status !== "PENDING"} onClick={() => void review(event, "accept")}>Add to universe</button><button className={buttonClass} disabled={busy || event.status !== "PENDING"} onClick={() => void review(event, "ignore")}>Ignore</button></div>
      </article>)}</div>}
      <Pagination page={feed} />
    </section>}
    {view === "frontier" && <section id="frontier" className={panelClass} aria-labelledby="frontier-heading">
      <h2 id="frontier-heading" className="text-lg font-semibold">Discovery frontier</h2><p className="mt-1 text-sm text-slate-500">Candidates awaiting deeper source and hiring-signal exploration, prioritized by evidence.</p><ErrorNotice message={frontier.error} retry={frontier.retry} />
      {frontier.loading ? <p role="status" className="py-6">Loading frontier…</p> : !frontier.error && !frontier.items.length ? <p className="py-6 text-sm text-slate-500">The frontier is empty. Exploration will add promising candidates when evidence is found.</p> : !frontier.error && <div className="mt-4 overflow-x-auto"><table className="w-full min-w-[650px] text-left text-sm"><thead><tr className="border-b text-xs text-slate-500">{["Company / reason", "Priority", "Depth", "Status", "Action"].map((label) => <th className="px-2 py-3" key={label}>{label}</th>)}</tr></thead><tbody>{frontier.items.map((item) => <tr key={item.id} className="border-b border-slate-100"><td className="px-2 py-4"><a className="font-semibold text-brand-700 underline" href={`/companies?company=${encodeURIComponent(item.company_id)}`}>{item.company_name}</a><p className="mt-1 text-xs text-slate-500">{item.reason}</p>{item.error && <p className="mt-1 text-xs text-rose-700">Last attempt failed: {item.error}</p>}<p className="mt-1 text-xs text-slate-400">{dateLabel(item.created_at)}</p></td><td className="px-2" title={Object.entries(item.priority_breakdown ?? {}).map(([key, value]) => `${key.replaceAll("_", " ")}: +${value}`).join("\n") || undefined}>{item.priority}{Object.keys(item.priority_breakdown ?? {}).length > 0 && <p className="mt-1 text-[11px] text-slate-500">{Object.entries(item.priority_breakdown ?? {}).map(([key, value]) => `${key.replaceAll("_", " ")} +${value}`).join(", ")}</p>}</td><td className="px-2">{item.depth}</td><td className="px-2"><Badge>{item.status}</Badge></td><td className="px-2"><button className={buttonClass} disabled={busy || item.status.toUpperCase() !== "PENDING"} onClick={() => void explore(item)}>Queue exploration</button></td></tr>)}</tbody></table></div>}
      <Pagination page={frontier} />
    </section>}
    {view === "runs" && <section id="runs" className={panelClass} aria-labelledby="runs-heading">
      <h2 id="runs-heading" className="text-lg font-semibold">Background runs</h2><p className="mt-1 text-sm text-slate-500">Queued is not completed. Run states refresh every 3 seconds while active, for up to 60 checks per page load.</p>
      <ErrorNotice message={settingsError} retry={refresh} />
      {settings && <RunLauncher settings={settings} onQueued={(run) => { monitor.track(run); runs.retry(); }} />}
      <ErrorNotice message={runs.error} retry={runs.retry} />
      {runs.paused && <p role="status" className="my-3 text-sm text-amber-800">Automatic run-list polling paused; work may still be in progress. <button className={buttonClass} onClick={runs.retry}>Resume run-list polling</button></p>}
      {runs.loading ? <p role="status" className="py-6">Loading runs…</p> : !runs.error && !runs.items.length ? <p className="py-6 text-sm text-slate-500">No runs recorded yet.</p> : !runs.error && <div className="mt-5 space-y-3">{runs.items.map((run) => <div key={run.id}><RunCard run={run} />{!runTerminal(run) && <button className={`${buttonClass} mt-2`} onClick={() => monitor.track(run)}>Track this run</button>}</div>)}</div>}
      <Pagination page={runs} />
    </section>}
    {view === "settings" && <section id="settings" className={panelClass} aria-labelledby="settings-heading">
      <h2 id="settings-heading" className="text-lg font-semibold">Discovery settings</h2><p className="mt-1 text-sm text-slate-500">Configure boundaries, not a closed company list. Providers requiring keys are optional; no credential entry is required here.</p>
      <ErrorNotice message={settingsError} retry={refresh} />
      {settings ? <><SettingsEditor settings={settings} onSaved={setSettings} /><TargetingSettings settings={settings} onSaved={setSettings} /></> : !settingsError && <p role="status" className="py-6">Loading settings…</p>}
    </section>}
  </PhaseShell>;
}

function RunLauncher({ settings, onQueued }: { settings: DiscoverySettings; onQueued: (run: RunRead) => void }) {
  const [draft, setDraft] = useState<BudgetDraft>(() => budgetDraft(settings.budgets));
  const [kind, setKind] = useState("exploration");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    let budgets: ExplorationBudgets;
    try { budgets = validatedBudgets(draft); } catch (error) { setError(errorMessage(error)); return; }
    setBusy(true);
    try { onQueued(await apiRequest<RunRead>("/api/v1/discovery/runs", { method: "POST", body: JSON.stringify({ kind, budgets }) })); }
    catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  return <form onSubmit={submit} className="my-5 space-y-4 rounded-xl bg-slate-50 p-4">
    <div className="flex flex-wrap items-end justify-between gap-3"><Field label="Run type"><select className={inputClass} value={kind} onChange={(event) => setKind(event.target.value)}><option value="exploration">Market exploration</option><option value="frontier">Process discovery frontier</option><option value="scan">Scan known companies</option><option value="reconciliation">Reconcile stale jobs</option></select></Field><button type="button" className={buttonClass} onClick={() => setDraft(budgetDraft(settings.budgets))}>Use saved budgets</button></div>
    <BudgetFields draft={draft} onChange={setDraft} /><ErrorNotice message={error} /><button className={primaryClass} disabled={busy}>{busy ? "Queueing…" : "Queue bounded run"}</button>
  </form>;
}

const hourFields = {
  freshness_hours: { label: "Primary freshness window (hours)", max: 720 },
  secondary_freshness_hours: { label: "Secondary discovery window (hours)", max: 2160 },
  scan_interval_hours: { label: "Scan known companies every (hours)", max: 720 },
  exploration_interval_hours: { label: "Explore job market every (hours)", max: 720 },
  frontier_interval_hours: { label: "Process frontier every (hours)", max: 720 },
  reconciliation_interval_hours: { label: "Reconcile closed jobs every (hours)", max: 720 },
} as const;
type HourKey = keyof typeof hourFields;
const hourKeys = Object.keys(hourFields) as HourKey[];

function textMap(values: Record<string, number>): Record<string, string> {
  return Object.fromEntries(Object.entries(values ?? {}).map(([key, value]) => [key, String(value)]));
}

function numberMap(values: Record<string, string>, label: string): Record<string, number> {
  const result: Record<string, number> = {};
  for (const [key, value] of Object.entries(values)) {
    const number = Number(value);
    if (!value.trim() || !Number.isFinite(number) || number < 0) throw new Error(`${label} "${key.replaceAll("_", " ")}" must be a nonnegative number.`);
    result[key] = number;
  }
  if (Object.values(result).reduce((total, value) => total + value, 0) <= 0) throw new Error(`At least one ${label.toLowerCase()} must be greater than zero.`);
  return result;
}

function WeightFields({ values, onChange, suffix = "" }: { values: Record<string, string>; onChange: (values: Record<string, string>) => void; suffix?: string }) {
  return <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(values).map(([key, value]) => <Field key={key} label={`${key.replaceAll("_", " ")}${suffix}`}><input className={inputClass} required type="number" min={0} step="any" value={value} onChange={(event) => onChange({ ...values, [key]: event.target.value })} /></Field>)}</div>;
}

function SettingsEditor({ settings, onSaved }: { settings: DiscoverySettings; onSaved: (settings: DiscoverySettings) => void }) {
  const [tiers, setTiers] = useState(settings.tiers.join("\n"));
  const [draft, setDraft] = useState<BudgetDraft>(() => budgetDraft(settings.budgets));
  const [hours, setHours] = useState<Record<HourKey, string>>(() => Object.fromEntries(hourKeys.map((key) => [key, String(settings[key])])) as Record<HourKey, string>);
  const [weights, setWeights] = useState(() => textMap(settings.company_score_weights));
  const [priorityWeights, setPriorityWeights] = useState(() => textMap(settings.company_priority_weights));
  const [providers, setProviders] = useState<Record<string, boolean>>(() => ({ ...settings.providers }));
  const [schedulerEnabled, setSchedulerEnabled] = useState(settings.scheduler_enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function load(next: DiscoverySettings) {
    setTiers(next.tiers.join("\n")); setDraft(budgetDraft(next.budgets));
    setHours(Object.fromEntries(hourKeys.map((key) => [key, String(next[key])])) as Record<HourKey, string>);
    setWeights(textMap(next.company_score_weights)); setPriorityWeights(textMap(next.company_priority_weights));
    setProviders({ ...next.providers }); setSchedulerEnabled(next.scheduler_enabled);
  }

  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setNotice("");
    let patch: DiscoverySettingsPatch;
    try {
      const tierList = tiers.split(/\r?\n/).map((tier) => tier.trim()).filter(Boolean);
      if (!tierList.length || new Set(tierList.map((tier) => tier.toLowerCase())).size !== tierList.length) throw new Error("Enter at least one tier, with no duplicates.");
      const hourValues = {} as Record<HourKey, number>;
      for (const key of hourKeys) {
        const number = Number(hours[key]);
        if (!hours[key].trim() || !Number.isFinite(number) || number <= 0 || number > hourFields[key].max) throw new Error(`${hourFields[key].label} must be greater than 0 and at most ${hourFields[key].max}.`);
        hourValues[key] = number;
      }
      if (hourValues.secondary_freshness_hours < hourValues.freshness_hours) throw new Error("The secondary discovery window must be at least the primary freshness window.");
      patch = { tiers: tierList, budgets: validatedBudgets(draft), ...hourValues, company_score_weights: numberMap(weights, "Score weight"), company_priority_weights: numberMap(priorityWeights, "Priority weight"), providers, scheduler_enabled: schedulerEnabled };
    } catch (error) { setError(errorMessage(error)); return; }
    setBusy(true);
    try {
      const next = await apiRequest<DiscoverySettings>("/api/v1/discovery/settings", { method: "PATCH", body: JSON.stringify(patch) });
      onSaved(next); load(next); setNotice("Settings saved. New runs and the worker scheduler use these values.");
    } catch (error) { setError(errorMessage(error)); }
    finally { setBusy(false); }
  }
  const toggles = settings.provider_status.filter((provider) => provider.name in providers);
  return <div className="mt-5 space-y-6">
    <form onSubmit={submit} className="space-y-5">
      <div className="grid gap-4 md:grid-cols-2"><Field label="Company tiers (one per line)"><textarea className={inputClass} required rows={4} value={tiers} onChange={(event) => setTiers(event.target.value)} /></Field><div className="grid gap-3">{(["freshness_hours", "secondary_freshness_hours"] as const).map((key) => <Field key={key} label={hourFields[key].label}><input required min={0.01} max={hourFields[key].max} step="any" type="number" className={inputClass} value={hours[key]} onChange={(event) => setHours({ ...hours, [key]: event.target.value })} /></Field>)}<p className="text-xs text-slate-500">The primary window is the application queue (default 48h); the secondary window is a discovery layer. Unknown posting dates remain UNKNOWN, never fresh. Discovery time is not a posting date.</p></div></div>
      <fieldset className="space-y-3"><legend className="mb-3 text-sm font-semibold">Default exploration budgets</legend><BudgetFields draft={draft} onChange={setDraft} /><p className="text-xs text-slate-500">A budget of 0 disables that kind of work (for example, 0 search queries uses only direct probes and no-key structured sources).</p></fieldset>
      <fieldset><legend className="mb-3 text-sm font-semibold">Company discovery score weights</legend><WeightFields values={weights} onChange={setWeights} /><p className="mt-2 text-xs text-slate-500">Relative weights are normalized by the server. Discovery score is separate from job opportunity fit.</p></fieldset>
      <fieldset><legend className="mb-3 text-sm font-semibold">Frontier priority points</legend><WeightFields values={priorityWeights} onChange={setPriorityWeights} suffix=" (+points)" /><p className="mt-2 text-xs text-slate-500">Points added to a candidate&apos;s exploration priority for each observed signal; strong recent hiring is explored first.</p></fieldset>
      <fieldset><legend className="mb-3 text-sm font-semibold">Background schedules</legend><label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={schedulerEnabled} onChange={(event) => setSchedulerEnabled(event.target.checked)} />Run scheduled work in the worker (scans, exploration, frontier, reconciliation)</label><div className="grid gap-3 sm:grid-cols-2">{(["scan_interval_hours", "exploration_interval_hours", "frontier_interval_hours", "reconciliation_interval_hours"] as const).map((key) => <Field key={key} label={hourFields[key].label}><input className={inputClass} required type="number" min={0.01} max={hourFields[key].max} step="any" value={hours[key]} onChange={(event) => setHours({ ...hours, [key]: event.target.value })} /></Field>)}</div></fieldset>
      <fieldset><legend className="mb-3 text-sm font-semibold">Discovery providers</legend><div className="grid gap-2 sm:grid-cols-2">{toggles.map((provider) => <label key={provider.name} className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={providers[provider.name]} onChange={(event) => setProviders({ ...providers, [provider.name]: event.target.checked })} /><span><span className="font-medium">{provider.name}</span>{provider.requires_key && <span className="text-xs text-slate-500"> — needs a server-side key to run</span>}</span></label>)}</div><p className="mt-2 text-xs text-slate-500">Turning a provider off stops new queries to it. A checked provider still runs only when it is available (see status below).</p></fieldset>
      <div aria-live="polite"><ErrorNotice message={error} />{notice && <p className="mt-2 text-sm text-brand-700">{notice}</p>}</div><button disabled={busy} className={primaryClass}>{busy ? "Saving…" : "Save settings"}</button>
    </form>
    <section aria-label="Provider availability"><h3 className="mb-3 font-semibold">Provider status</h3><div className="grid gap-3 md:grid-cols-2">{settings.provider_status.map((provider) => <article key={provider.name} className="rounded-xl border border-slate-200 p-4 text-sm"><div className="flex justify-between gap-2"><h4 className="font-semibold">{provider.name}</h4><Badge>{provider.enabled ? "Enabled" : "Unavailable / disabled"}</Badge></div><p className="mt-2 text-slate-600">{provider.detail}</p><p className="mt-2 text-xs text-slate-500">{provider.requires_key ? "Optional provider — requires server-side key configuration." : "No API key required."}</p></article>)}</div></section>
  </div>;
}