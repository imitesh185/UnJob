"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Menu, Sparkles, X } from "lucide-react";
import { apiRequest } from "@/lib/api";
import { dateLabel, errorMessage, isAbort, runOutcome, runSucceeded, runTerminal, safeUrl } from "@/lib/discovery";
import type { RunRead } from "@/lib/types";

export const panelClass = "rounded-2xl border border-black/[0.06] bg-white p-5 shadow-card";
// Variants must not stack conflicting utilities (e.g. bg-white + bg-brand-600): Tailwind
// resolves those by stylesheet order, not class order, which hid primary button text.
const buttonBase = "inline-flex items-center justify-center rounded-xl border px-4 py-2 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-50";
export const buttonClass = `${buttonBase} border-slate-200 bg-white hover:bg-slate-50`;
export const activeButtonClass = `${buttonBase} border-brand-600 bg-brand-50 text-brand-700`;
export const primaryClass = `${buttonBase} border-brand-600 bg-brand-600 text-white hover:bg-brand-700`;
export const inputClass = "w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm";

export type ShellPage = "companies" | "discovery" | "opportunities" | "profile" | "applications";

export function PhaseShell({ current, title, description, children }: { current: ShellPage; title: string; description: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const links = [{ href: "/", label: "Today" }, { href: "/opportunities", label: "Opportunities" }, { href: "/applications", label: "Applications" }, { href: "/profile", label: "Profile" }, { href: "/companies", label: "Companies" }, { href: "/discovery", label: "Discovery" }];
  return <div className="min-h-screen">
    <header className="sticky top-0 z-40 border-b border-black/[0.06] bg-canvas/90 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1480px] items-center justify-between px-4 sm:px-6 lg:px-8">
        <Link href="/" className="flex items-center gap-2.5"><span className="grid h-9 w-9 place-items-center rounded-xl bg-ink text-white"><Sparkles size={18} /></span><span className="font-bold">UnJob</span><span className="rounded-md bg-brand-100 px-2 py-0.5 text-[10px] font-bold text-brand-700">PHASE 3</span></Link>
        <nav aria-label="Main navigation" className="hidden gap-2 md:flex">{links.map((link) => <Link key={link.href} href={link.href} aria-current={link.href === `/${current}` ? "page" : undefined} className={link.href === `/${current}` ? activeButtonClass : buttonClass}>{link.label}</Link>)}</nav>
        <button className={`${buttonClass} md:hidden`} onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Toggle navigation">{open ? <X size={20} /> : <Menu size={20} />}</button>
      </div>
      {open && <nav aria-label="Mobile navigation" className="flex flex-col gap-2 border-t p-3 md:hidden">{links.map((link) => <Link key={link.href} href={link.href} onClick={() => setOpen(false)} aria-current={link.href === `/${current}` ? "page" : undefined} className={link.href === `/${current}` ? activeButtonClass : buttonClass}>{link.label}</Link>)}</nav>}
    </header>
    <main className="mx-auto max-w-[1480px] space-y-6 px-4 py-8 sm:px-6 lg:px-8">
      <div><p className="text-xs font-bold uppercase tracking-widest text-brand-600">Job-market intelligence</p><h1 className="mt-2 text-3xl font-semibold tracking-tight">{title}</h1><p className="mt-2 text-sm text-slate-600">{description}</p></div>
      {children}
    </main>
  </div>;
}

export function ErrorNotice({ message, retry }: { message: string; retry?: () => void }) {
  if (!message) return null;
  return <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{message}{retry && <button onClick={retry} className="ml-3 underline">Retry</button>}</div>;
}

export function ExternalLink({ url, children }: { url: string | null | undefined; children?: React.ReactNode }) {
  const href = safeUrl(url);
  return href ? <a href={href} target="_blank" rel="noopener noreferrer" className="break-all text-brand-700 underline">{children || url}</a> : <span className="text-slate-500">{url ? `${url} (unverified URL)` : "UNKNOWN"}</span>;
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1.5 text-xs font-semibold text-slate-600"><span>{label}</span>{children}</label>;
}

export function Badge({ children }: { children: React.ReactNode }) {
  return <span className="inline-block rounded-md bg-slate-100 px-2 py-1 text-xs font-semibold text-slate-700">{children}</span>;
}

export function RunCard({ run }: { run: RunRead }) {
  const notes = Array.isArray(run.progress?.notes) ? (run.progress.notes as unknown[]).filter((note): note is string => typeof note === "string") : [];
  const tone = runSucceeded(run) ? "text-emerald-700" : runTerminal(run) ? (run.status.toUpperCase() === "FAILED" ? "text-rose-700" : "text-amber-800") : "text-slate-600";
  return <article className="rounded-xl border border-slate-200 p-4 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold capitalize">{run.kind.replaceAll("_", " ")} run</h3><Badge>{run.status}</Badge></div>
    <p className={`mt-1 text-xs font-semibold ${tone}`}>{runOutcome(run)}</p>
    <p className="mt-1 break-all text-xs text-slate-500">ID: {run.id}{run.trigger ? ` · Trigger: ${run.trigger}` : ""}{run.attempts ? ` · Attempt ${run.attempts}` : ""}</p>
    <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-3"><div>Queued: {dateLabel(run.created_at)}</div><div>Started: {run.started_at ? dateLabel(run.started_at) : "Not yet"}</div><div>Finished: {run.finished_at ? dateLabel(run.finished_at) : "Not yet"}</div></dl>
    <p className="mt-3 text-slate-600">{run.companies_discovered ?? 0} companies discovered · {run.jobs_inspected ?? 0} jobs inspected · {run.jobs_created ?? 0} jobs created</p>
    {(run.error || run.errors?.length > 0) && <div className="mt-3"><ErrorNotice message={[run.error, ...(run.errors ?? [])].filter(Boolean).join(" · ")} /></div>}
    {notes.length > 0 && <details className="mt-3"><summary className="cursor-pointer text-xs font-medium">What the run did ({notes.length})</summary><ul className="mt-2 list-inside list-disc space-y-1 text-xs text-slate-600">{notes.map((note, index) => <li key={index} className="break-words">{note}</li>)}</ul></details>}
    {Object.keys(run.progress ?? {}).length > 0 && <details className="mt-3"><summary className="cursor-pointer text-xs font-medium">Progress details</summary><pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(run.progress, null, 2)}</pre></details>}
    {Object.keys(run.budgets ?? {}).length > 0 && <details className="mt-2"><summary className="cursor-pointer text-xs font-medium">Run budgets</summary><pre className="mt-2 overflow-x-auto text-xs">{JSON.stringify(run.budgets, null, 2)}</pre></details>}
  </article>;
}

/** Tracks one queued run with bounded polling; `onSettled` fires once when it reaches any terminal state. */
export function useRunMonitor(onSettled: () => void) {
  const [run, setRun] = useState<RunRead | null>(null);
  const [pollError, setPollError] = useState("");
  const [paused, setPaused] = useState(false);
  const [revision, setRevision] = useState(0);
  const settledRef = useRef(onSettled);
  settledRef.current = onSettled;
  const notified = useRef<string | null>(null);
  const track = useCallback((next: RunRead) => { setRun(next); setPollError(""); setPaused(false); setRevision((value) => value + 1); }, []);

  useEffect(() => {
    if (!run) return;
    if (runTerminal(run)) {
      // Partial and failed runs may still have saved companies or jobs, so refresh either way.
      if (notified.current !== run.id) { notified.current = run.id; settledRef.current(); }
      return;
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let attempts = 0;
    const poll = async () => {
      try {
        const next = await apiRequest<RunRead>(`/api/v1/discovery/runs/${encodeURIComponent(run.id)}`, undefined, controller.signal);
        if (controller.signal.aborted) return;
        setRun(next);
        if (runTerminal(next)) return;
        attempts += 1;
        if (attempts >= 60) { setPaused(true); return; }
        timer = setTimeout(poll, 3000);
      } catch (error) {
        if (!isAbort(error)) { setPollError(errorMessage(error)); setPaused(true); }
      }
    };
    timer = setTimeout(poll, 1500);
    return () => { controller.abort(); clearTimeout(timer); };
    // Polling is bounded per selected run, not reset on every progress update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id, run ? runTerminal(run) : false, revision]);

  return { run, track, pollError, paused, resume: () => { setPaused(false); setPollError(""); setRevision((value) => value + 1); } };
}

export function RunMonitor({ monitor }: { monitor: ReturnType<typeof useRunMonitor> }) {
  if (!monitor.run) return null;
  return <section className={panelClass} aria-label="Tracked background run" aria-live="polite">
    <p className="mb-3 text-sm font-semibold">{runTerminal(monitor.run) ? `Background run result: ${runOutcome(monitor.run)}` : "Queued work — completion is not yet confirmed"}</p>
    <RunCard run={monitor.run} />
    <ErrorNotice message={monitor.pollError} />
    {monitor.paused && <div className="mt-3 text-sm text-amber-800">Automatic polling stopped. The worker may still be running. <button className={buttonClass} onClick={monitor.resume}>Resume status checks</button></div>}
    <a href="/discovery#runs" className="mt-3 inline-block text-xs text-brand-700 underline">View all runs</a>
  </section>;
}
