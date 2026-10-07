"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import { dateLabel, errorMessage, isAbort, safeUrl } from "@/lib/discovery";
import { APPLICATION_FLOW, freshnessText, levelTone, score, statusLabel, tierLabel, yearsText, type OpportunityDetail } from "@/lib/intel";
import type { RunRead } from "@/lib/types";
import { Badge, buttonClass, ErrorNotice, Field, inputClass, panelClass, PhaseShell, primaryClass, RunMonitor, useRunMonitor } from "./discovery-shared";
import { ResumeReview } from "./resume-review";
import { PriorityBadge } from "./today-targets";

function Breakdown({ values }: { values: Record<string, number> }) {
  return <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">{Object.entries(values).map(([key, value]) => <div key={key} className="flex justify-between gap-2"><dt className="text-slate-500">{statusLabel(key)}</dt><dd className="font-medium">{score(value)}</dd></div>)}</dl>;
}

function ApplicationTracker({ detail, onChanged }: { detail: OpportunityDetail; onChanged: () => void }) {
  const application = detail.application;
  const [status, setStatus] = useState(application?.status ?? "");
  const [note, setNote] = useState("");
  const [nextAction, setNextAction] = useState(application?.next_action ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setStatus(application?.status ?? ""); setNextAction(application?.next_action ?? ""); }, [application?.status, application?.next_action]);

  async function track() {
    setBusy(true); setError("");
    try { await apiRequest("/api/v1/applications", { method: "POST", body: JSON.stringify({ job_id: detail.job.id }) }); onChanged(); }
    catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!application) return;
    setBusy(true); setError("");
    const body: Record<string, unknown> = { next_action: nextAction || null };
    if (status && status !== application.status) body.status = status;
    if (note.trim()) body.note = note.trim();
    try { await apiRequest(`/api/v1/applications/${application.id}`, { method: "PATCH", body: JSON.stringify(body) }); setNote(""); onChanged(); }
    catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }
  if (!application) {
    return <div><p className="text-sm text-slate-600">Not tracked yet.</p><button className={`${primaryClass} mt-3`} disabled={busy || !detail.profile_ready} onClick={() => void track()}>Track this application</button><ErrorNotice message={error} /></div>;
  }
  return <div className="space-y-3">
    <p className="text-sm">Status: <Badge>{statusLabel(application.status)}</Badge>{application.applied_at && <span className="ml-2 text-xs text-slate-500">Applied {dateLabel(application.applied_at)}</span>}</p>
    <p className="text-xs text-slate-500">UnJob never submits applications. Approve the resume, apply yourself on the company&apos;s site, then record each outcome here so the analytics can learn.</p>
    <form onSubmit={save} className="grid gap-3 md:grid-cols-3">
      <Field label="Move to"><select className={inputClass} value={status} onChange={(event) => setStatus(event.target.value)}>{APPLICATION_FLOW.concat(APPLICATION_FLOW.includes(application.status) ? [] : [application.status]).map((value) => <option key={value} value={value}>{statusLabel(value)}</option>)}</select></Field>
      <Field label="Note (optional)"><input className={inputClass} value={note} onChange={(event) => setNote(event.target.value)} placeholder="e.g. Recruiter replied" /></Field>
      <Field label="Next action"><input className={inputClass} value={nextAction} onChange={(event) => setNextAction(event.target.value)} /></Field>
      <button className={primaryClass} disabled={busy}>Save</button>
    </form>
    <ErrorNotice message={error} />
    <ol className="space-y-1 border-l border-slate-200 pl-4 text-xs">{application.events.map((event) => <li key={event.id}><span className="font-medium">{statusLabel(event.to_status)}</span> · {dateLabel(event.created_at)} · {event.actor}{event.note ? ` — ${event.note}` : ""}</li>)}</ol>
  </div>;
}

export function OpportunityDetailView({ jobId }: { jobId: string }) {
  const [detail, setDetail] = useState<OpportunityDetail | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [variantId, setVariantId] = useState<string | null>(null);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const monitor = useRunMonitor(refresh);

  useEffect(() => {
    const controller = new AbortController();
    setError("");
    apiRequest<OpportunityDetail>(`/api/v1/opportunities/${jobId}`, undefined, controller.signal)
      .then((data) => { setDetail(data); setVariantId((current) => current && data.variants.some((variant) => variant.id === current) ? current : (data.resume?.id ?? data.variants[0]?.id ?? null)); })
      .catch((requestError) => { if (!isAbort(requestError)) setError(errorMessage(requestError)); });
    return () => controller.abort();
  }, [jobId, revision]);

  async function tailor(force: boolean) {
    setError("");
    try { monitor.track(await apiRequest<RunRead>(`/api/v1/opportunities/${jobId}/tailor`, { method: "POST", body: JSON.stringify({ force }) })); }
    catch (requestError) { setError(errorMessage(requestError)); }
  }

  if (error && !detail) return <PhaseShell current="opportunities" title="Opportunity" description=""><ErrorNotice message={error} retry={refresh} /></PhaseShell>;
  if (!detail) return <PhaseShell current="opportunities" title="Opportunity" description=""><p role="status" className="text-sm text-slate-500">Loading…</p></PhaseShell>;
  const { job, analysis, match, priority, company_target: target } = detail;
  const strategy = priority?.strategy;
  const posting = safeUrl(job.application_url);
  return <PhaseShell current="opportunities" title={job.title} description={`${job.company}${job.tier ? ` · ${tierLabel(job.tier)}` : ""} · ${job.location} · ${freshnessText(job.freshness_status, job.age_hours)}`}>
    <RunMonitor monitor={monitor} />
    <ErrorNotice message={error} />
    <section className={panelClass} aria-label="Priority">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><PriorityBadge value={priority?.priority_class} /><p className="text-lg font-semibold">{priority?.recommendation ?? "Not analysed yet"}</p></div>
          {priority?.gates.length ? <ul className="mt-2 text-sm text-rose-700">{priority.gates.map((gate) => <li key={gate}>{gate}</li>)}</ul> : null}
          {priority?.reasons.map((reason) => <p key={reason} className="mt-1 text-xs text-slate-500">{reason}</p>)}
        </div>
        <div className="flex flex-wrap gap-2">
          {posting && <a className={buttonClass} href={posting} target="_blank" rel="noopener noreferrer">Open job posting</a>}
          {job.company_id && <Link className={buttonClass} href={`/companies?company=${job.company_id}`}>Company evidence</Link>}
        </div>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-3 text-center sm:grid-cols-5">{([
        ["Fit", match?.fit_score], ["Company target", target?.score], ["Freshness", priority?.freshness_score], ["Career value", priority?.career_value_score], ["Priority", priority?.priority_score],
      ] as const).map(([label, value]) => <div key={label} className="rounded-xl bg-slate-50 p-3"><dt className="text-xs text-slate-500">{label}</dt><dd className="text-2xl font-semibold">{score(value)}</dd></div>)}</dl>
    </section>

    {match && <section className={panelClass} aria-labelledby="fit-title">
      <h2 id="fit-title" className="text-lg font-semibold">Why this fits — Fit {score(match.fit_score)}</h2>
      <Breakdown values={match.breakdown} />
      <div className="mt-4 grid gap-4 md:grid-cols-3">
        <div><p className="text-sm font-semibold text-emerald-800">Strong matches</p><ul className="mt-1 space-y-1 text-xs">{match.strong_matches.map((line) => <li key={line}>+ {line}</li>)}</ul></div>
        <div><p className="text-sm font-semibold text-amber-800">Partial matches</p><ul className="mt-1 space-y-1 text-xs">{match.partial_matches.map((line) => <li key={line}>~ {line}</li>)}{match.partial_matches.length === 0 && <li className="text-slate-500">None</li>}</ul></div>
        <div><p className="text-sm font-semibold text-rose-800">Potential gaps</p><ul className="mt-1 space-y-1 text-xs">{match.gaps.map((gap) => <li key={gap.name}>- {gap.name} <span className="text-slate-500">({gap.importance === "alternative" ? "an accepted alternative is met" : gap.importance})</span></li>)}{match.gaps.length === 0 && <li className="text-slate-500">None found</li>}</ul></div>
      </div>
      <ul className="mt-4 list-inside list-disc space-y-1 text-sm text-slate-700">{match.explanation.map((line) => <li key={line}>{line}</li>)}</ul>
      <details className="mt-4"><summary className="cursor-pointer text-sm font-medium">JD skill vs your evidence ({match.skill_assessments.length})</summary>
        <table className="mt-2 w-full text-left text-xs"><thead><tr className="border-b text-slate-500"><th className="py-1">Skill</th><th>Needed</th><th>Your evidence</th><th>Detail</th></tr></thead>
          <tbody>{match.skill_assessments.map((item) => <tr key={`${item.importance}-${item.name}`} className="border-b border-slate-100"><td className="py-1 font-medium">{item.name}</td><td>{item.importance}</td><td><span className={`rounded px-1.5 py-0.5 ${levelTone(item.status)}`}>{item.label}</span></td><td className="text-slate-600">{item.note}</td></tr>)}</tbody></table>
      </details>
    </section>}

    {strategy && <section className={panelClass} aria-labelledby="strategy-title">
      <h2 id="strategy-title" className="text-lg font-semibold">Application strategy</h2>
      <p className="mt-1 text-xs text-slate-500">Positioning: {strategy.positioning.archetype ?? "generic"}{strategy.positioning.signals.length ? ` — ${strategy.positioning.signals.join(", ")}` : ""}{strategy.positioning.sources.length ? ` (${strategy.positioning.sources.join("; ")})` : ""}</p>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div><p className="text-sm font-semibold">Emphasize (genuine evidence)</p><ol className="mt-1 list-inside list-decimal space-y-1 text-xs">{strategy.emphasize.map((item) => <li key={item.term}><span className="font-medium">{item.term}</span> — {item.why}</li>)}</ol>
          {strategy.de_emphasize.length > 0 && <p className="mt-2 text-xs text-slate-600">De-emphasize: {strategy.de_emphasize.join(", ")}</p>}</div>
        <div><p className="text-sm font-semibold">Concerns and how to handle them honestly</p><ul className="mt-1 space-y-2 text-xs">{strategy.concerns.map((item) => <li key={item.concern}><p className="font-medium text-amber-800">{item.concern}</p><p className="text-slate-600">{item.handle}</p></li>)}{strategy.concerns.length === 0 && <li className="text-slate-500">No concerns found.</li>}</ul></div>
      </div>
    </section>}

    <section id="resume" className={`${panelClass} scroll-mt-24`} aria-labelledby="resume-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="resume-title" className="text-lg font-semibold">Tailored resume</h2>
        <div className="flex flex-wrap items-center gap-2">
          {detail.variants.length > 1 && <select className={inputClass} value={variantId ?? ""} onChange={(event) => setVariantId(event.target.value)} aria-label="Resume version">{detail.variants.map((variant) => <option key={variant.id} value={variant.id}>v{variant.version} · {statusLabel(variant.status)} · alignment {score(variant.alignment_score)}</option>)}</select>}
          <button className={buttonClass} disabled={!detail.profile_ready} onClick={() => void tailor(Boolean(variantId))}>{variantId ? "Regenerate" : "Tailor resume now"}</button>
        </div>
      </div>
      <div className="mt-4">{variantId ? <ResumeReview key={variantId} variantId={variantId} onChanged={refresh} /> : <p className="text-sm text-slate-500">{job.tier === "S" ? "Relevant S-tier jobs are tailored automatically after analysis; this one is not tailored yet (for example, the location does not match your preferences)." : "Not tailored automatically under your tier policy. You can tailor it now."}</p>}</div>
    </section>

    <section className={panelClass} aria-labelledby="tracking-title">
      <h2 id="tracking-title" className="text-lg font-semibold">Application tracking</h2>
      <div className="mt-3"><ApplicationTracker detail={detail} onChanged={refresh} /></div>
    </section>

    {analysis && <section className={panelClass} aria-labelledby="jd-title">
      <h2 id="jd-title" className="text-lg font-semibold">JD analysis</h2>
      <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div><dt className="text-xs text-slate-500">Seniority</dt><dd>{statusLabel(analysis.seniority)}{analysis.years_min ? ` · ${yearsText(analysis.years_min, analysis.years_max)}` : ""}</dd></div>
        <div><dt className="text-xs text-slate-500">Role focus</dt><dd>{statusLabel(analysis.role_focus)}</dd></div>
        <div><dt className="text-xs text-slate-500">Domains</dt><dd>{analysis.domains.join(", ") || "UNKNOWN"}</dd></div>
        <div><dt className="text-xs text-slate-500">Leadership signals</dt><dd>{analysis.leadership_signals.join(", ") || "None"}</dd></div>
        <div><dt className="text-xs text-slate-500">Location</dt><dd>{String((analysis.location_requirements as { mode?: string }).mode ?? "unknown")}</dd></div>
        <div><dt className="text-xs text-slate-500">Compensation</dt><dd>{analysis.compensation ? String((analysis.compensation as { text?: string }).text) : "Not published"}</dd></div>
        <div className="sm:col-span-2"><dt className="text-xs text-slate-500">Keywords</dt><dd className="text-xs">{analysis.keywords.join(", ")}</dd></div>
      </dl>
      {analysis.business_context && <p className="mt-3 text-sm text-slate-600">{analysis.business_context}</p>}
      <table className="mt-4 w-full text-left text-xs"><thead><tr className="border-b text-slate-500"><th className="py-1">ID</th><th>Kind</th><th>Requirement</th><th>Your status</th></tr></thead>
        <tbody>{analysis.requirements.map((requirement) => {
          const matched = match?.requirement_matches.find((item) => item.id === requirement.id);
          return <tr key={requirement.id} className="border-b border-slate-100 align-top"><td className="py-1 font-mono">{requirement.id}</td><td>{requirement.kind}{requirement.mode === "any" ? " (any of)" : ""}</td><td className="pr-2">{requirement.text}</td><td>{matched?.status ? <span className={`rounded px-1.5 py-0.5 ${matched.status === "MET" ? levelTone("STRONG") : matched.status === "PARTIAL" ? levelTone("MODERATE") : matched.status === "GAP" ? levelTone("GAP") : levelTone("LISTED")}`}>{matched.status}</span> : "—"}</td></tr>;
        })}</tbody></table>
      <details className="mt-4"><summary className="cursor-pointer text-sm font-medium">Full job description</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-xs text-slate-700">{job.description}</pre></details>
    </section>}

    {target && <section className={panelClass} aria-labelledby="target-title">
      <h2 id="target-title" className="text-lg font-semibold">Company target score — {score(target.score)}</h2>
      <Breakdown values={target.breakdown} />
      <ul className="mt-3 list-inside list-disc text-xs text-slate-600">{target.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
    </section>}
  </PhaseShell>;
}
