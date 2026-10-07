"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { errorMessage, isAbort, safeUrl } from "@/lib/discovery";
import { freshnessText, priorityTone, score, statusLabel, tierLabel, type Opportunity, type TodayResponse } from "@/lib/intel";
import type { PageResponse } from "@/lib/types";
import { Badge, buttonClass, ErrorNotice, panelClass, primaryClass } from "./discovery-shared";

export function PriorityBadge({ value }: { value: string | null | undefined }) {
  return <span className={`inline-flex rounded-md px-2 py-0.5 text-xs font-bold ${priorityTone(value)}`}>{value ?? "—"}</span>;
}

export function ResumeChecklist({ item }: { item: Opportunity }) {
  const resume = item.resume;
  if (!resume) {
    return <p className="text-xs text-slate-500">{item.tier === "S" ? "Tailored resume pending (generated automatically for relevant S-tier jobs)." : "No tailored resume yet."}</p>;
  }
  const checks: [string, boolean][] = [
    ["Tailored", true],
    ["JD analyzed", true],
    ["Truth validated", resume.truth_passed],
    ["ATS checked", resume.ats_status === "pass"],
  ];
  return <div className="text-xs">
    <p className="font-semibold text-emerald-700">{resume.status === "APPROVED" ? "Approved resume" : "Tailored resume ready"} · v{resume.version} · alignment {score(resume.alignment_score)}</p>
    <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-slate-600">{checks.map(([label, ok]) => <li key={label} className={ok ? "" : "text-rose-700"}>{ok ? "\u2713" : "\u2717"} {label}</li>)}</ul>
  </div>;
}

function PlanCard({ item, index }: { item: Opportunity; index: number }) {
  const apply = safeUrl(item.application_url);
  return <article className="flex flex-col rounded-2xl border border-slate-200 bg-white p-4">
    <div className="flex items-start justify-between gap-2">
      <div>
        <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400">#{index + 1} · ~{item.effort_minutes ?? 45} min</p>
        <h3 className="mt-1 font-semibold leading-snug">{item.title}</h3>
        <p className="text-sm text-slate-600">{item.company}{item.tier ? ` · ${tierLabel(item.tier)}` : ""}</p>
      </div>
      <PriorityBadge value={item.priority_class} />
    </div>
    <dl className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">
      <div className="rounded-lg bg-slate-50 p-2"><dt className="text-slate-500">Fit</dt><dd className="text-lg font-semibold">{score(item.fit_score)}</dd></div>
      <div className="rounded-lg bg-slate-50 p-2"><dt className="text-slate-500">Company</dt><dd className="text-lg font-semibold">{score(item.company_target_score)}</dd></div>
      <div className="rounded-lg bg-slate-50 p-2"><dt className="text-slate-500">Priority</dt><dd className="text-lg font-semibold">{score(item.priority_score)}</dd></div>
    </dl>
    <p className="mt-3 text-xs text-slate-600">{item.location} · {freshnessText(item.freshness_status, item.age_hours)}</p>
    <p className="mt-2 text-sm font-medium text-ink">{item.recommendation}</p>
    {item.why.length > 0 && <ul className="mt-2 list-inside list-disc space-y-1 text-xs text-slate-600">{item.why.slice(0, 2).map((line) => <li key={line}>{line}</li>)}</ul>}
    <div className="mt-3"><ResumeChecklist item={item} /></div>
    <div className="mt-auto flex flex-wrap gap-2 pt-4">
      <Link href={`/opportunities/${item.job_id}`} className={primaryClass}>Review</Link>
      {item.resume && <Link href={`/opportunities/${item.job_id}#resume`} className={buttonClass}>View resume</Link>}
      {apply && <a href={apply} target="_blank" rel="noopener noreferrer" className={buttonClass}>Job posting</a>}
    </div>
  </article>;
}

export function TodayTargets() {
  const [today, setToday] = useState<TodayResponse | null>(null);
  const [queue, setQueue] = useState<Opportunity[]>([]);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    Promise.all([
      apiRequest<TodayResponse>("/api/v1/opportunities/today", undefined, controller.signal),
      apiRequest<PageResponse<Opportunity> & { profile_ready: boolean }>("/api/v1/opportunities?priority=P0,P1&limit=12", undefined, controller.signal),
    ])
      .then(([plan, list]) => { setToday(plan); setQueue(list.items); })
      .catch((requestError) => { if (!isAbort(requestError)) setError(errorMessage(requestError)); });
    return () => controller.abort();
  }, [revision]);

  if (error) return <section className={panelClass}><ErrorNotice message={error} retry={refresh} /></section>;
  if (!today) return <section className={panelClass}><p role="status" className="text-sm text-slate-500">Loading today&apos;s targets…</p></section>;
  if (!today.profile_ready) {
    return <section className={panelClass} aria-labelledby="today-title">
      <h2 id="today-title" className="text-xl font-semibold">Today&apos;s targets</h2>
      <p className="mt-2 text-sm text-slate-600">Import your master resume to get fit scores, priorities and truthfully tailored resumes. Nothing is inferred about you until then.</p>
      <Link href="/profile" className={`${primaryClass} mt-4`}>Import master resume</Link>
    </section>;
  }
  const pipeline = today.interview_pipeline ?? {};
  return <section id="today" className="scroll-mt-24 space-y-4" aria-labelledby="today-title">
    <div className={panelClass}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-bold uppercase tracking-widest text-brand-600">If you have 2 hours today</p>
          <h2 id="today-title" className="mt-1 text-xl font-semibold">Work on these {today.plan.length} application{today.plan.length === 1 ? "" : "s"}{today.plan_minutes ? ` (~${today.plan_minutes} min)` : ""}</h2>
          <p className="mt-1 text-sm text-slate-600">Ranked by fit, company target, freshness and career value. Every recommendation explains itself; nothing is submitted for you.</p>
        </div>
        <button className={buttonClass} onClick={refresh}>Refresh</button>
      </div>
      {today.plan.length === 0
        ? <p className="mt-4 text-sm text-slate-500">No P0–P2 opportunities right now. Discovery and scans keep running; check the Opportunities page for lower priorities.</p>
        : <div className="mt-4 grid gap-4 lg:grid-cols-3">{today.plan.map((item, index) => <PlanCard key={item.job_id} item={item} index={index} />)}</div>}
    </div>
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      {([
        ["P0 opportunities", today.counts?.P0 ?? 0],
        ["P1 opportunities", today.counts?.P1 ?? 0],
        ["S-tier tailored", `${today.s_tier?.tailored ?? 0}/${today.s_tier?.opportunities ?? 0}`],
        ["New companies today", today.new_companies_today],
        ["Awaiting your review", today.awaiting_review ?? 0],
        ["Interviews active", today.interviews_active ?? 0],
      ] as const).map(([label, value]) => <div key={label} className={panelClass}><p className="text-xs text-slate-500">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div>)}
    </div>
    <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
      <div className={panelClass} aria-labelledby="queue-title">
        <div className="flex items-center justify-between"><h3 id="queue-title" className="font-semibold">Application queue (P0/P1)</h3><Link href="/opportunities" className="text-sm text-brand-700 underline">All opportunities</Link></div>
        {queue.length === 0 ? <p className="mt-3 text-sm text-slate-500">No P0/P1 opportunities yet.</p> : <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm">
          <thead><tr className="border-b text-xs text-slate-500">{["Company / role", "Fit", "Company", "Priority", "Resume", "Status", ""].map((label) => <th key={label} className="px-2 py-2">{label}</th>)}</tr></thead>
          <tbody>{queue.map((item) => <tr key={item.job_id} className="border-b border-slate-100">
            <td className="px-2 py-2"><p className="font-medium">{item.company}{item.tier ? ` · ${item.tier}` : ""}</p><p className="text-xs text-slate-500">{item.title}</p></td>
            <td className="px-2">{score(item.fit_score)}</td>
            <td className="px-2">{score(item.company_target_score)}</td>
            <td className="px-2"><PriorityBadge value={item.priority_class} /></td>
            <td className="px-2 text-xs">{item.resume ? `${item.resume.status === "APPROVED" ? "Approved" : "Ready"} · ${score(item.resume.alignment_score)}` : "—"}</td>
            <td className="px-2 text-xs">{statusLabel(item.application?.status)}</td>
            <td className="px-2 text-right"><Link className="text-brand-700 underline" href={`/opportunities/${item.job_id}`}>View analysis</Link></td>
          </tr>)}</tbody>
        </table></div>}
      </div>
      <div className="space-y-4">
        <div className={panelClass}>
          <h3 className="font-semibold">Interview pipeline</h3>
          <dl className="mt-3 grid grid-cols-3 gap-2 text-center text-xs">{Object.entries(pipeline).map(([stage, count]) => <div key={stage} className="rounded-lg bg-slate-50 p-2"><dt className="text-slate-500">{statusLabel(stage)}</dt><dd className="text-lg font-semibold">{count}</dd></div>)}</dl>
          <Link href="/applications" className="mt-3 inline-block text-sm text-brand-700 underline">Track applications</Link>
        </div>
        <div className={panelClass}>
          <h3 className="font-semibold">Companies with fresh hiring signals</h3>
          {today.fresh_hiring_companies?.length ? <ul className="mt-2 space-y-1 text-sm">{today.fresh_hiring_companies.map((company) => <li key={company.id} className="flex justify-between gap-2"><Link href={`/companies?company=${company.id}`} className="text-brand-700 underline">{company.name}</Link><span className="text-xs text-slate-500">{company.tier} · {company.fresh_roles} fresh role{company.fresh_roles === 1 ? "" : "s"}</span></li>)}</ul> : <p className="mt-2 text-sm text-slate-500">None in the freshness window.</p>}
        </div>
      </div>
    </div>
    <p className="text-xs text-slate-500"><Badge>Truth first</Badge> Tailored resumes only reorder, select and emphasize facts from your profile; every claim cites its source fact.</p>
  </section>;
}
