"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { dateLabel, errorMessage, isAbort } from "@/lib/discovery";
import { APPLICATION_FLOW, score, statusLabel, tierLabel, type AnalyticsGroup, type AnalyticsResponse, type ApplicationRead, type ApplicationStatus } from "@/lib/intel";
import { Badge, buttonClass, ErrorNotice, Field, inputClass, panelClass, PhaseShell } from "./discovery-shared";
import { PriorityBadge } from "./today-targets";

const STAGES: { label: string; statuses: ApplicationStatus[] }[] = [
  { label: "To review", statuses: ["DISCOVERED", "ANALYZED", "RECOMMENDED", "RESUME_GENERATED", "USER_REVIEW"] },
  { label: "Approved", statuses: ["APPROVED", "READY_TO_APPLY", "APPLICATION_STARTED"] },
  { label: "Applied", statuses: ["APPLIED", "OA"] },
  { label: "Interviewing", statuses: ["RECRUITER_SCREEN", "TECHNICAL", "FINAL"] },
  { label: "Closed", statuses: ["OFFER", "REJECTED", "WITHDRAWN"] },
];
const PRIORITY_ORDER: Record<string, number> = { P0: 0, P1: 1, P2: 2, P3: 3, REJECT: 4 };
const STAGE_PREVIEW = 12;

function byPriority(left: ApplicationRead, right: ApplicationRead): number {
  const rank = (item: ApplicationRead) => PRIORITY_ORDER[item.priority_class ?? ""] ?? 5;
  return rank(left) - rank(right) || (right.fit_score ?? 0) - (left.fit_score ?? 0);
}

function GroupTable({ title, groups }: { title: string; groups: AnalyticsGroup[] }) {
  if (!groups.length) return null;
  return <div className="rounded-xl border border-slate-200 p-3">
    <p className="text-sm font-semibold">{title}</p>
    <table className="mt-2 w-full text-left text-xs"><thead><tr className="text-slate-500"><th>Value</th><th>Applied</th><th>Responses</th><th>Interviews</th><th>Rate</th></tr></thead>
      <tbody>{groups.slice(0, 8).map((group) => <tr key={group.value} className="border-t border-slate-100"><td className="py-1">{group.value}</td><td>{group.applications}</td><td>{group.responses}</td><td>{group.interviews}</td><td>{group.interview_rate === null ? "—" : `${group.interview_rate}%`}{group.low_sample && <span className="ml-1 text-amber-700" title="Too few applications to draw conclusions">*</span>}</td></tr>)}</tbody></table>
  </div>;
}

export function ApplicationsView() {
  const [items, setItems] = useState<ApplicationRead[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsResponse | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    apiRequest<{ items: ApplicationRead[] }>("/api/v1/applications", undefined, controller.signal)
      .then((data) => setItems(data.items))
      .catch((requestError) => { if (!isAbort(requestError)) setError(errorMessage(requestError)); });
    apiRequest<AnalyticsResponse>("/api/v1/applications/analytics", undefined, controller.signal)
      .then(setAnalytics)
      .catch(() => setAnalytics(null));
    return () => controller.abort();
  }, [revision]);

  async function move(application: ApplicationRead, status: string) {
    setBusy(application.id); setError("");
    try { await apiRequest(`/api/v1/applications/${application.id}`, { method: "PATCH", body: JSON.stringify({ status }) }); refresh(); }
    catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(null); }
  }

  const totals = analytics?.totals;
  return <PhaseShell current="applications" title="Applications" description="Track every application from recommendation to offer. You approve and submit; UnJob records outcomes and learns what works.">
    <ErrorNotice message={error} retry={refresh} />
    {totals && <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">{([
      ["Tracked", totals.tracked], ["Applied", totals.applied], ["Responses", totals.responses], ["Interviews", totals.interviews],
      ["Offers", totals.offers], ["Interview rate", totals.interview_rate === null ? "—" : `${totals.interview_rate}%`], ["Resume versions", totals.resume_versions], ["Companies", totals.companies],
    ] as const).map(([label, value]) => <div key={label} className={panelClass}><p className="text-xs text-slate-500">{label}</p><p className="mt-2 text-2xl font-semibold">{value}</p></div>)}</div>}
    <section className="grid gap-4 xl:grid-cols-5">{STAGES.map((stage) => {
      const stageItems = items.filter((item) => stage.statuses.includes(item.status)).sort(byPriority);
      const shown = expanded[stage.label] ? stageItems : stageItems.slice(0, STAGE_PREVIEW);
      return <div key={stage.label} className={panelClass}>
        <p className="text-sm font-semibold">{stage.label} <span className="text-slate-500">({stageItems.length})</span></p>
        <ul className="mt-3 space-y-3">{shown.map((item) => <li key={item.id} className="rounded-xl border border-slate-200 p-3 text-sm">
          <div className="flex items-start justify-between gap-2"><Link className="font-medium text-brand-700 underline" href={`/opportunities/${item.job_id}`}>{item.company}</Link><PriorityBadge value={item.priority_class} /></div>
          <p className="text-xs text-slate-600">{item.job_title}</p>
          <p className="mt-1 text-xs text-slate-500">{statusLabel(item.status)} · fit {score(item.fit_score)}{item.tier ? ` · ${tierLabel(item.tier)}` : ""}</p>
          {item.resume && item.status !== "WITHDRAWN" && <p className="mt-1 text-xs">{item.resume.status === "APPROVED" ? "\u2713 Resume approved" : "Resume ready for review"} · alignment {score(item.resume.alignment_score)}</p>}
          {item.next_action && <p className="mt-1 text-xs text-amber-800">Next: {item.next_action}</p>}
          <Field label="Move to"><select className={inputClass} value={item.status} disabled={busy === item.id} onChange={(event) => void move(item, event.target.value)}>{APPLICATION_FLOW.concat(APPLICATION_FLOW.includes(item.status) ? [] : [item.status]).map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></Field>
          <p className="mt-1 text-[11px] text-slate-400">Updated {dateLabel(item.updated_at)}</p>
        </li>)}{stageItems.length === 0 && <li className="text-xs text-slate-500">Nothing here.</li>}</ul>
        {stageItems.length > STAGE_PREVIEW && <button className={`${buttonClass} mt-3 w-full`} onClick={() => setExpanded((current) => ({ ...current, [stage.label]: !current[stage.label] }))}>{expanded[stage.label] ? "Show fewer" : `Show all ${stageItems.length}`}</button>}
      </div>;
    })}</section>
    {analytics && <section className={panelClass} aria-labelledby="learning-title">
      <div className="flex items-center justify-between"><h2 id="learning-title" className="text-lg font-semibold">What is working</h2><button className={buttonClass} onClick={refresh}>Refresh</button></div>
      <p className="mt-1 text-xs text-slate-500">{analytics.note} <Badge>* low sample</Badge></p>
      <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <GroupTable title="By company" groups={analytics.by_company} />
        <GroupTable title="By tier" groups={analytics.by_tier} />
        <GroupTable title="By role type" groups={analytics.by_role} />
        <GroupTable title="By required technology" groups={analytics.by_technology} />
        <GroupTable title="By resume positioning" groups={analytics.by_positioning} />
        <GroupTable title="By industry" groups={analytics.by_industry} />
        <GroupTable title="By company size" groups={analytics.by_company_size} />
        <GroupTable title="By application source" groups={analytics.by_source} />
      </div>
      {analytics.totals.applied === 0 && <p className="mt-3 text-sm text-slate-500">No applications recorded as applied yet; outcome analytics start once you mark applications as applied and record responses.</p>}
    </section>}
  </PhaseShell>;
}
