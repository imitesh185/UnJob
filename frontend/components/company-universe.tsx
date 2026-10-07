"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { companyStatuses, dateLabel, errorMessage, isAbort, safeUrl } from "@/lib/discovery";
import type { CareerSourceCreated, CompanyDetail, CompanyRead, DiscoverySettings, PageResponse, RunRead } from "@/lib/types";
import { Badge, buttonClass, ErrorNotice, ExternalLink, Field, inputClass, panelClass, PhaseShell, primaryClass, RunMonitor, useRunMonitor } from "./discovery-shared";

const emptyFilters = { q: "", tier: "", status: "", min_score: "", fresh_hiring: "", industry: "", stage: "", location: "", remote: "", data_hiring: "", source: "", sort: "target" };
type CompanyFilters = typeof emptyFilters;

export function CompanyUniverse() {
  const [companies, setCompanies] = useState<CompanyRead[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [filters, setFilters] = useState<CompanyFilters>(emptyFilters);
  const [settings, setSettings] = useState<DiscoverySettings | null>(null);
  const [settingsError, setSettingsError] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [names, setNames] = useState("");
  const [seedTier, setSeedTier] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<CompanyDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const monitor = useRunMonitor(refresh);

  const loadSettings = useCallback(async (signal?: AbortSignal) => {
    setSettingsError("");
    try { setSettings(await apiRequest<DiscoverySettings>("/api/v1/discovery/settings", undefined, signal)); }
    catch (requestError) { if (!isAbort(requestError)) setSettingsError(errorMessage(requestError)); }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void loadSettings(controller.signal);
    setSelectedId(new URLSearchParams(window.location.search).get("company"));
    return () => controller.abort();
  }, [loadSettings]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const timer = setTimeout(async () => {
      const query = new URLSearchParams({ limit: "100", offset: String(offset) });
      Object.entries(filters).forEach(([key, value]) => { if (value) query.set(key, value); });
      try {
        const data = await apiRequest<PageResponse<CompanyRead>>(`/api/v1/companies?${query}`, undefined, controller.signal);
        setCompanies(data.items); setTotal(data.total);
      } catch (requestError) { if (!isAbort(requestError)) setError(errorMessage(requestError)); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [filters, offset, revision]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    const controller = new AbortController();
    // Keep showing the same company while it reloads so its notices and edits are not reset.
    setDetail((current) => (current?.id === selectedId ? current : null)); setDetailLoading(true); setDetailError("");
    apiRequest<CompanyDetail>(`/api/v1/companies/${encodeURIComponent(selectedId)}`, undefined, controller.signal)
      .then(setDetail)
      .catch((requestError) => { if (!isAbort(requestError)) setDetailError(errorMessage(requestError)); })
      .finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedId, revision]);

  function filter(key: keyof CompanyFilters, value: string) {
    setOffset(0); setFilters((previous) => ({ ...previous, [key]: value }));
  }

  async function addSeeds(event: FormEvent) {
    event.preventDefault(); setActionError(""); setNotice("");
    const seeds = [...new Set(names.split(/\r?\n/).map((name) => name.trim()).filter(Boolean))];
    if (!seeds.length) { setActionError("Paste at least one company name, one per line."); return; }
    if (seeds.length > 1000) { setActionError("Import at most 1,000 names at a time."); return; }
    setBusy(true);
    try {
      const result = await apiRequest<{ companies: CompanyRead[]; run_id: string | null }>("/api/v1/companies/seeds", { method: "POST", body: JSON.stringify({ names: seeds, ...(seedTier ? { tier: seedTier } : {}) }) });
      setNames("");
      setNotice(`${result.companies.length} company records saved. ${result.run_id ? `Source resolution and scanning queued (run ${result.run_id}).` : "No scan was queued. Company hiring information remains unverified."}`);
      refresh();
      if (result.run_id) {
        try { monitor.track(await apiRequest<RunRead>(`/api/v1/discovery/runs/${encodeURIComponent(result.run_id)}`)); }
        catch (requestError) { setActionError(`Seeds saved, but run status could not be loaded: ${errorMessage(requestError)}`); }
      }
    } catch (requestError) { setActionError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }

  async function scan(id: string) {
    setBusy(true); setActionError(""); setNotice("");
    try { monitor.track(await apiRequest<RunRead>(`/api/v1/companies/${encodeURIComponent(id)}/scan`, { method: "POST" })); }
    catch (requestError) { setActionError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }

  return <PhaseShell current="companies" title="Company Universe" description="Track evidence of hiring, verify career sources, and expand beyond your seed companies. Tiers organize the universe; they do not restrict discovery.">
    <section className={panelClass} aria-labelledby="seed-title">
      <h2 id="seed-title" className="text-lg font-semibold">Add seed companies</h2>
      <p className="mt-1 text-sm text-slate-500">One company per line. Names are starting points, not confirmed domains or hiring activity.</p>
      <form onSubmit={addSeeds} className="mt-4 grid gap-4 md:grid-cols-[1fr_220px]">
        <Field label="Company names"><textarea rows={4} className={inputClass} value={names} onChange={(event) => setNames(event.target.value)} placeholder="Paste company names here" required /></Field>
        <div className="space-y-4"><Field label="Seed tier"><select className={inputClass} value={seedTier} onChange={(event) => setSeedTier(event.target.value)}><option value="">Server default tier</option>{settings?.tiers.map((tier) => <option key={tier}>{tier}</option>)}</select></Field><button disabled={busy} className={primaryClass}>{busy ? "Submitting…" : "Add seed companies"}</button><a href="/discovery#settings" className="block text-xs text-brand-700 underline">Configure tiers and discovery</a></div>
      </form>
      <div className="mt-3" aria-live="polite">{notice && <p className="mb-2 text-sm text-brand-700">{notice}</p>}<ErrorNotice message={actionError} /><ErrorNotice message={settingsError} retry={() => void loadSettings()} /></div>
    </section>
    <RunMonitor monitor={monitor} />
    <section className={panelClass} aria-labelledby="universe-title">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 id="universe-title" className="text-lg font-semibold">Companies {loading ? "" : `(${total})`}</h2><button className={buttonClass} onClick={refresh} disabled={loading}>Refresh</button></div>
      <div className="my-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Search companies"><input className={inputClass} value={filters.q} onChange={(event) => filter("q", event.target.value)} placeholder="Company name or domain" /></Field>
        <Field label="Sort by"><select className={inputClass} value={filters.sort} onChange={(event) => filter("sort", event.target.value)}><option value="target">Your target score</option><option value="discovery">Discovery score</option></select></Field>
        <Field label="Tier"><select className={inputClass} value={filters.tier} onChange={(event) => filter("tier", event.target.value)}><option value="">All tiers</option>{settings?.tiers.map((tier) => <option key={tier}>{tier}</option>)}</select></Field>
        <Field label="Status"><select className={inputClass} value={filters.status} onChange={(event) => filter("status", event.target.value)}><option value="">All statuses</option>{companyStatuses.map((status) => <option key={status}>{status}</option>)}</select></Field>
        <Field label="Minimum discovery score"><input className={inputClass} type="number" min={0} max={100} value={filters.min_score} onChange={(event) => { const value = event.target.value; if (value === "" || (Number(value) >= 0 && Number(value) <= 100)) filter("min_score", value); }} /></Field>
        {(["fresh_hiring", "remote", "data_hiring"] as const).map((key) => <Field key={key} label={{ fresh_hiring: "Fresh data hiring", remote: "Remote presence", data_hiring: "Data engineering hiring" }[key]}><select className={inputClass} value={filters[key]} onChange={(event) => filter(key, event.target.value)}><option value="">Any (including unknown)</option><option value="true">Yes</option><option value="false">No</option></select></Field>)}
        {(["industry", "stage", "location", "source"] as const).map((key) => <Field key={key} label={{ industry: "Industry", stage: "Company stage", location: "Location", source: "Discovery source" }[key]}><input className={inputClass} value={filters[key]} onChange={(event) => filter(key, event.target.value)} /></Field>)}
        <button className={`${buttonClass} self-end`} onClick={() => { setFilters(emptyFilters); setOffset(0); }}>Clear filters</button>
      </div>
      <ErrorNotice message={error} retry={refresh} />
      {loading ? <p role="status" className="py-8 text-sm text-slate-500">Loading companies…</p> : !error && companies.length === 0 ? <p className="py-8 text-sm text-slate-500">No companies match. Add seeds or review the discovery feed; no companies are prefilled.</p> : !error && <div className="overflow-x-auto">
        <table className="w-full min-w-[1080px] text-left text-sm"><caption className="sr-only">Company hiring signals. Fresh jobs require a known posting date.</caption><thead><tr className="border-b text-xs text-slate-500">{["Company", "Tier", "Target score", "Discovery score", "DE jobs", "Fresh DE jobs", "Platform jobs", "Last scan", "Source", "Status", "Actions"].map((title) => <th key={title} className="px-2 py-3">{title}</th>)}</tr></thead>
          <tbody>{companies.map((company) => <tr key={company.id} className="border-b border-slate-100"><td className="px-2 py-4"><button onClick={() => setSelectedId(company.id)} className="font-semibold text-brand-700 underline">{company.name}</button><p className="mt-1 text-xs text-slate-500">{company.domain || "Domain UNKNOWN"}</p><p className="mt-0.5 text-[11px] text-slate-400">{company.is_seed ? "Seed" : `Discovered · review ${company.review_status}`}</p></td><td className="px-2"><Badge>{company.tier}</Badge></td><td className="px-2" title={(company.target_reasons ?? []).join("\n") || undefined}>{company.target_score === null || company.target_score === undefined ? <span className="text-xs text-slate-400">—</span> : <span className="font-semibold">{Math.round(company.target_score)}</span>}</td><td className="px-2">{company.discovery_score}</td><td className="px-2">{company.data_roles_count}</td><td className="px-2">{company.fresh_data_roles_count}</td><td className="px-2">{company.platform_roles_count}</td><td className="px-2 text-xs">{dateLabel(company.last_scanned_at)}</td><td className="px-2 text-xs">{company.discovery_source || "UNKNOWN"}</td><td className="px-2"><Badge>{company.status}</Badge></td><td className="px-2"><button className={buttonClass} disabled={busy} onClick={() => void scan(company.id)}>Scan</button></td></tr>)}</tbody>
        </table>
      </div>}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-500"><span>{total ? `${offset + 1}–${Math.min(offset + companies.length, total)} of ${total}` : "0 companies"} · Freshness window: {settings ? `${settings.freshness_hours}h` : "UNKNOWN"}; undated jobs are not fresh.</span><div className="flex gap-2"><button className={buttonClass} disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</button><button className={buttonClass} disabled={loading || offset + 100 >= total} onClick={() => setOffset(offset + 100)}>Next</button></div></div>
    </section>
    {selectedId && <section className={panelClass} aria-label="Company details">
      <div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-semibold">Company details</h2><button className={buttonClass} onClick={() => setSelectedId(null)}>Close details</button></div>
      {detailLoading && <p role="status">Loading company details…</p>}
      <ErrorNotice message={detailError} retry={refresh} />
      {detail && <CompanyDetails key={detail.id} company={detail} tiers={settings?.tiers ?? []} busy={busy} onScan={() => void scan(detail.id)} onSaved={refresh} onRunQueued={monitor.track} />}
    </section>}
  </PhaseShell>;
}

type CompanyForm = { name: string; tier: string; status: string; domain: string; careers: string };

function companyForm(company: CompanyDetail): CompanyForm {
  return { name: company.name, tier: company.tier, status: company.status, domain: company.domain || "", careers: company.careers_url || "" };
}

function sameForm(left: CompanyForm, right: CompanyForm): boolean {
  return left.name === right.name && left.tier === right.tier && left.status === right.status && left.domain === right.domain && left.careers === right.careers;
}

function CompanyDetails({ company, tiers, busy, onScan, onSaved, onRunQueued }: { company: CompanyDetail; tiers: string[]; busy: boolean; onScan: () => void; onSaved: () => void; onRunQueued: (run: RunRead) => void }) {
  const [form, setForm] = useState<CompanyForm>(() => companyForm(company));
  // Server values the form was last synced to; only fields edited away from them are sent.
  const [baseline, setBaseline] = useState<CompanyForm>(() => companyForm(company));
  const [source, setSource] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const { name, tier, status, domain, careers } = form;
  const update = (key: keyof CompanyForm, value: string) => setForm((current) => ({ ...current, [key]: value }));

  useEffect(() => {
    const next = companyForm(company);
    setForm((current) => (sameForm(current, baseline) ? next : current));
    setBaseline(next);
    // Re-sync only when the server record changes; `baseline` is read from that render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [company]);

  async function save(event: FormEvent) {
    event.preventDefault(); setError(""); setNotice("");
    if (careers.trim() && !safeUrl(careers.trim())) { setError("Career URL must be an absolute HTTP or HTTPS URL."); return; }
    if (domain.trim()) {
      try { const parsed = new URL(`https://${domain.trim()}`); if (parsed.hostname !== domain.trim().toLowerCase() || !parsed.hostname.includes(".") || parsed.port) throw new Error(); }
      catch { setError("Domain must be a hostname (for example company.com), without a protocol or path."); return; }
    }
    const patch: Record<string, string> = {};
    if (!name.trim()) { setError("Company name cannot be empty."); return; }
    if (name.trim() !== baseline.name) patch.name = name.trim();
    if (tier !== baseline.tier) patch.tier = tier;
    if (status !== baseline.status) patch.status = status;
    if (domain.trim() && domain.trim() !== baseline.domain) patch.domain = domain.trim();
    if (careers.trim() && careers.trim() !== baseline.careers) patch.careers_url = careers.trim();
    if (!Object.keys(patch).length) { setNotice("No changes to save."); return; }
    setSaving(true);
    try {
      await apiRequest<CompanyRead>(`/api/v1/companies/${encodeURIComponent(company.id)}`, { method: "PATCH", body: JSON.stringify(patch) });
      setBaseline(form);
      setNotice("Company updated."); onSaved();
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setSaving(false); }
  }
  async function addSource(event: FormEvent) {
    event.preventDefault(); setError(""); setNotice("");
    if (!safeUrl(source.trim())) { setError("Source must be an absolute HTTP or HTTPS URL."); return; }
    setSaving(true);
    try {
      const created = await apiRequest<CareerSourceCreated>(`/api/v1/companies/${encodeURIComponent(company.id)}/sources`, { method: "POST", body: JSON.stringify({ url: source.trim() }) });
      setSource("");
      setNotice(created.scan_run_id ? `Career source saved as ${created.platform}. A company scan is queued (run ${created.scan_run_id}).` : `Career source saved as ${created.platform}. Queue a scan to inspect its jobs.`);
      onSaved();
      if (created.scan_run_id) {
        try { onRunQueued(await apiRequest<RunRead>(`/api/v1/discovery/runs/${encodeURIComponent(created.scan_run_id)}`)); }
        catch (requestError) { setError(`Source saved, but scan status could not be loaded: ${errorMessage(requestError)}`); }
      }
    }
    catch (requestError) { setError(errorMessage(requestError)); }
    finally { setSaving(false); }
  }
  return <div className="space-y-5">
    <div className="flex flex-wrap justify-between gap-3"><div><h3 className="text-xl font-semibold">{company.name}</h3><p className="mt-1 text-sm">Discovery score {company.discovery_score} · Confidence {(company.confidence * 100).toFixed(0)}% · {company.is_seed ? "Manual seed" : `Discovered · review ${company.review_status}`}</p></div><button className={primaryClass} disabled={busy || saving} onClick={onScan}>Queue company scan</button></div>
    <dl className="grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
      <div><dt className="text-xs text-slate-500">Official domain</dt><dd>{company.domain ? <ExternalLink url={`https://${company.domain}`} /> : "UNKNOWN — not resolved"}</dd></div>
      <div><dt className="text-xs text-slate-500">Career portal</dt><dd><ExternalLink url={company.careers_url} /></dd></div>
      <div><dt className="text-xs text-slate-500">Industry / stage</dt><dd>{company.industry || "UNKNOWN"} / {company.company_stage || "UNKNOWN"}</dd></div>
      <div><dt className="text-xs text-slate-500">Headquarters</dt><dd>{company.headquarters || "UNKNOWN"}</dd></div>
      <div><dt className="text-xs text-slate-500">India / remote presence</dt><dd>{presence(company.india_presence)} / {presence(company.remote_presence)}</dd></div>
      <div><dt className="text-xs text-slate-500">Known technologies</dt><dd>{company.known_technologies?.join(", ") || "UNKNOWN"}</dd></div>
      <div><dt className="text-xs text-slate-500">All / data / fresh data / platform jobs</dt><dd>{company.jobs_count} / {company.data_roles_count} / {company.fresh_data_roles_count} / {company.platform_roles_count}</dd></div>
      <div><dt className="text-xs text-slate-500">Last scanned / explored</dt><dd>{dateLabel(company.last_scanned_at)} / {dateLabel(company.last_explored_at)}</dd></div>
      <div><dt className="text-xs text-slate-500">Created / updated</dt><dd>{dateLabel(company.created_at)} / {dateLabel(company.updated_at)}</dd></div>
    </dl>
    <div className="rounded-xl bg-slate-50 p-4 text-sm"><h4 className="font-semibold">Why discovered</h4><p className="mt-2 whitespace-pre-line">{company.discovery_reason || "UNKNOWN — no reason recorded"}</p><p className="mt-2 text-xs text-slate-500">Source: {company.discovery_source || "UNKNOWN"}</p><ul className="mt-2 list-inside list-disc">{company.score_reasons?.map((reason, index) => <li key={index}>{reason}</li>)}</ul><dl className="mt-3 grid gap-2 sm:grid-cols-3">{Object.entries(company.score_breakdown ?? {}).map(([key, value]) => <div key={key}><dt className="text-xs text-slate-500">{key.replaceAll("_", " ")}</dt><dd>{value}</dd></div>)}</dl></div>
    <form onSubmit={save} className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Field label="Company name"><input className={inputClass} value={name} maxLength={200} onChange={(event) => update("name", event.target.value)} /></Field>
      <Field label="Company tier"><select className={inputClass} value={tier} onChange={(event) => update("tier", event.target.value)}>{[...new Set([company.tier, ...tiers])].map((value) => <option key={value}>{value}</option>)}</select></Field>
      <Field label="Company status"><select className={inputClass} value={status} onChange={(event) => update("status", event.target.value)}>{[...new Set([company.status, ...companyStatuses])].map((value) => <option key={value}>{value}</option>)}</select></Field>
      <Field label="Verified domain (optional)"><input className={inputClass} value={domain} onChange={(event) => update("domain", event.target.value)} /></Field>
      <Field label="Career URL (optional)"><input className={inputClass} value={careers} onChange={(event) => update("careers", event.target.value)} type="url" /></Field>
      <button disabled={saving || busy} className={buttonClass}>Save company changes</button>
    </form>
    <p className="text-xs text-slate-500">Only edited fields are saved. Status is recomputed from evidence on the next scan. A renamed company keeps its previous name as an alias.</p>
    <div aria-live="polite">{notice && <p className="mb-2 text-sm text-brand-700">{notice}</p>}<ErrorNotice message={error} /></div>
    <section><h4 className="mb-3 font-semibold">Career sources</h4><div className="space-y-3">{company.sources?.length ? company.sources.map((item) => <article key={item.id} className="rounded-xl border border-slate-200 p-3 text-sm"><ExternalLink url={item.url} /><p className="mt-2">{item.platform || "UNKNOWN"} · Confidence {(item.platform_confidence * 100).toFixed(0)}% · {item.active ? "Active" : "Inactive"} · <Badge>{item.scan_status}</Badge></p><p className="mt-2 text-xs text-slate-600">Found via {item.discovered_via || "UNKNOWN"}{item.evidence ? `: ${item.evidence}` : ""}</p><p className="mt-1 text-xs text-slate-500">Listings inspected: {item.jobs_found} · Relevant open roles: {item.relevant_jobs_found}</p><p className="mt-1 text-xs text-slate-500">Last scanned: {dateLabel(item.last_scanned_at)} · Last success: {dateLabel(item.last_success_at)} · Last complete scan: {dateLabel(item.last_complete_scan_at)}</p><ErrorNotice message={item.error || ""} /></article>) : <p className="text-sm text-slate-500">No career sources resolved. Add a verified source URL or queue a scan.</p>}</div>
      <form className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-end" onSubmit={addSource}><div className="flex-1"><Field label="Add career source URL"><input type="url" required className={inputClass} value={source} onChange={(event) => setSource(event.target.value)} placeholder="https://…" /></Field></div><button disabled={saving || busy} className={buttonClass}>Add source and scan</button></form>
    </section>
    <section><h4 className="mb-3 font-semibold">Discovery evidence</h4>{company.events?.length ? <div className="space-y-3">{company.events.map((event) => <article key={event.id} className="rounded-xl bg-slate-50 p-3 text-sm"><p className="font-medium">{event.reason}</p><p className="mt-1 whitespace-pre-wrap">{event.evidence || "UNKNOWN"}</p><p className="mt-2 text-xs text-slate-500">{event.source} · First seen {dateLabel(event.discovered_at)} · Last seen {dateLabel(event.last_seen_at)}{event.observation_count > 1 ? ` · Observed ${event.observation_count}×` : ""} · Confidence {(event.confidence * 100).toFixed(0)}% · {event.status}</p><ExternalLink url={event.source_url} /></article>)}</div> : <p className="text-sm text-slate-500">No discovery evidence recorded.</p>}</section>
    <section><h4 className="mb-3 font-semibold">Company relationships</h4>{company.relationships?.length ? <ul className="space-y-3 text-sm">{company.relationships.map((relationship) => {
      const outgoing = relationship.source_company_id === company.id;
      const otherId = outgoing ? relationship.target_company_id : relationship.source_company_id;
      const otherName = outgoing ? relationship.target_company_name : relationship.source_company_name;
      return <li key={relationship.id}><span className="text-slate-600">{outgoing ? `${company.name} ${relationship.relationship_type.replaceAll("_", " ")} ` : `${otherName} ${relationship.relationship_type.replaceAll("_", " ")} ${company.name}`}</span>{outgoing && <a className="font-medium text-brand-700 underline" href={`/companies?company=${encodeURIComponent(otherId)}`}>{otherName}</a>}{!outgoing && <a className="ml-2 text-xs text-brand-700 underline" href={`/companies?company=${encodeURIComponent(otherId)}`}>View {otherName}</a>}<span className="ml-2 text-xs text-slate-500">Confidence {(relationship.confidence * 100).toFixed(0)}%</span>{relationship.evidence && <p className="text-xs text-slate-500">{relationship.evidence}</p>}{relationship.evidence_url && <div className="text-xs"><ExternalLink url={relationship.evidence_url}>Relationship evidence</ExternalLink></div>}</li>;
    })}</ul> : <p className="text-sm text-slate-500">No relationships verified yet.</p>}</section>
  </div>;
}

function presence(value: boolean | null): string {
  return value === true ? "Yes" : value === false ? "No" : "UNKNOWN";
}
