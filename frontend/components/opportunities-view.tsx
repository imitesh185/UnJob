"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { errorMessage, isAbort } from "@/lib/discovery";
import { freshnessText, score, statusLabel, tierLabel, type Opportunity } from "@/lib/intel";
import type { PageResponse, RunRead } from "@/lib/types";
import { buttonClass, ErrorNotice, Field, inputClass, panelClass, PhaseShell, primaryClass, RunMonitor, useRunMonitor } from "./discovery-shared";
import { PriorityBadge } from "./today-targets";

const filtersDefault = { priority: "", tier: "", min_fit: "", tailored: "", include_rejected: "" };

export function OpportunitiesView() {
  const [items, setItems] = useState<Opportunity[]>([]);
  const [total, setTotal] = useState(0);
  const [profileReady, setProfileReady] = useState(true);
  const [filters, setFilters] = useState(filtersDefault);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const monitor = useRunMonitor(() => setRevision((value) => value + 1));

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    const query = new URLSearchParams({ limit: "50", offset: String(offset) });
    Object.entries(filters).forEach(([key, value]) => { if (value) query.set(key, value); });
    apiRequest<PageResponse<Opportunity> & { profile_ready: boolean }>(`/api/v1/opportunities?${query}`, undefined, controller.signal)
      .then((data) => { setItems(data.items); setTotal(data.total); setProfileReady(data.profile_ready); })
      .catch((requestError) => { if (!isAbort(requestError)) setError(errorMessage(requestError)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters, offset, revision]);

  async function reanalyze() {
    setError("");
    try { monitor.track(await apiRequest<RunRead>("/api/v1/opportunities/analyze", { method: "POST" })); }
    catch (requestError) { setError(errorMessage(requestError)); }
  }

  function filter(key: keyof typeof filtersDefault, value: string) { setOffset(0); setFilters((current) => ({ ...current, [key]: value })); }

  return <PhaseShell current="opportunities" title="Opportunities" description="Jobs ranked for you: candidate fit, company target score, freshness and career value combine into an explainable priority (P0–P3 or Reject).">
    {!profileReady && <div className={panelClass}><p className="text-sm">Import your master resume to rank jobs for you. <Link className="text-brand-700 underline" href="/profile">Go to Profile</Link></p></div>}
    <RunMonitor monitor={monitor} />
    <section className={panelClass} aria-labelledby="opps-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="opps-title" className="text-lg font-semibold">Ranked jobs {loading ? "" : `(${total})`}</h2>
        <button className={primaryClass} onClick={() => void reanalyze()} disabled={!profileReady}>Re-analyze all jobs</button>
      </div>
      <div className="my-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="Priority"><select className={inputClass} value={filters.priority} onChange={(event) => filter("priority", event.target.value)}><option value="">P0–P3</option><option value="P0">P0 only</option><option value="P0,P1">P0 and P1</option><option value="P2">P2</option><option value="P3">P3</option><option value="REJECT">Rejected</option></select></Field>
        <Field label="Tier"><select className={inputClass} value={filters.tier} onChange={(event) => filter("tier", event.target.value)}><option value="">All tiers</option>{["S", "A", "B", "C", "UNCATEGORIZED"].map((tier) => <option key={tier}>{tier}</option>)}</select></Field>
        <Field label="Minimum fit"><input className={inputClass} type="number" min={0} max={100} value={filters.min_fit} onChange={(event) => filter("min_fit", event.target.value)} /></Field>
        <Field label="Tailored resume"><select className={inputClass} value={filters.tailored} onChange={(event) => filter("tailored", event.target.value)}><option value="">Any</option><option value="true">Ready</option><option value="false">Not yet</option></select></Field>
        <Field label="Rejected jobs"><select className={inputClass} value={filters.include_rejected} onChange={(event) => filter("include_rejected", event.target.value)}><option value="">Hide</option><option value="true">Show with reasons</option></select></Field>
      </div>
      <ErrorNotice message={error} retry={() => setRevision((value) => value + 1)} />
      {loading ? <p role="status" className="py-6 text-sm text-slate-500">Loading…</p> : !error && items.length === 0 ? <p className="py-6 text-sm text-slate-500">No jobs match. Scans and analysis run in the background.</p> : !error && <div className="overflow-x-auto">
        <table className="w-full min-w-[1100px] text-left text-sm">
          <thead><tr className="border-b text-xs text-slate-500">{["Company", "Role", "Fit", "Target", "Priority", "Freshness", "Location", "Why recommended", "Resume", ""].map((label) => <th key={label} className="px-2 py-3">{label}</th>)}</tr></thead>
          <tbody>{items.map((item) => <tr key={item.job_id} className="border-b border-slate-100 align-top">
            <td className="px-2 py-3"><p className="font-medium">{item.company}</p><p className="text-xs text-slate-500">{tierLabel(item.tier)}</p></td>
            <td className="px-2 py-3"><Link className="font-medium text-brand-700 underline" href={`/opportunities/${item.job_id}`}>{item.title}</Link><p className="text-xs text-slate-500">{statusLabel(item.seniority_fit)}</p></td>
            <td className="px-2 py-3 font-semibold">{score(item.fit_score)}</td>
            <td className="px-2 py-3">{score(item.company_target_score)}</td>
            <td className="px-2 py-3"><PriorityBadge value={item.priority_class} /><p className="mt-1 text-xs text-slate-500">{score(item.priority_score)}</p></td>
            <td className="px-2 py-3 text-xs">{freshnessText(item.freshness_status, item.age_hours)}</td>
            <td className="px-2 py-3 text-xs">{item.location}<p className="text-slate-500">{statusLabel(item.location_fit?.status)}</p></td>
            <td className="px-2 py-3 text-xs text-slate-600">{item.priority_class === "REJECT" ? item.gates.join(" ") || item.recommendation : (item.why[0] ?? item.recommendation)}{item.gaps.length > 0 && <p className="mt-1 text-rose-700">Gaps: {item.gaps.slice(0, 3).join(", ")}</p>}</td>
            <td className="px-2 py-3 text-xs">{item.resume ? <span className={item.resume.truth_passed ? "text-emerald-700" : "text-rose-700"}>{item.resume.status === "APPROVED" ? "Approved" : "Ready"} · {score(item.resume.alignment_score)}</span> : "—"}</td>
            <td className="px-2 py-3"><Link className={buttonClass} href={`/opportunities/${item.job_id}`}>Open</Link></td>
          </tr>)}</tbody>
        </table>
      </div>}
      <div className="mt-4 flex justify-end gap-2"><button className={buttonClass} disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</button><button className={buttonClass} disabled={offset + 50 >= total || loading} onClick={() => setOffset(offset + 50)}>Next</button></div>
    </section>
  </PhaseShell>;
}
