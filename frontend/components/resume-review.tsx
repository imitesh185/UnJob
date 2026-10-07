"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { API_URL, apiRequest } from "@/lib/api";
import { errorMessage, isAbort } from "@/lib/discovery";
import { blockText, levelTone, score, statusLabel, type ClaimRead, type ResumeBlock, type VariantDetail } from "@/lib/intel";
import { Badge, buttonClass, ErrorNotice, inputClass, panelClass, primaryClass } from "./discovery-shared";

function Blocks({ blocks }: { blocks: ResumeBlock[] }) {
  return <div className="space-y-1 text-[13px] leading-relaxed">
    {blocks.map((block, index) => {
      if (block.kind === "name") return <p key={index} className="text-lg font-bold">{blockText(block)}</p>;
      if (block.kind === "contact") return <p key={index} className="text-xs text-slate-500">{blockText(block)}</p>;
      if (block.kind === "headline") return <p key={index} className="font-semibold text-brand-700">{blockText(block)}</p>;
      if (block.kind === "heading") return <p key={index} className="mt-3 border-b border-slate-300 pb-0.5 text-xs font-bold uppercase tracking-wider">{blockText(block)}</p>;
      if (block.kind === "entry") return <p key={index} className="mt-2 font-semibold">{blockText(block)}</p>;
      if (block.kind === "bullet") return <p key={index} className="pl-3">• {blockText(block)}</p>;
      return <p key={index}>{blockText(block)}</p>;
    })}
  </div>;
}

function CheckIcon({ status }: { status: string }) {
  const tone = status === "pass" ? "text-emerald-700" : status === "warn" ? "text-amber-700" : "text-rose-700";
  return <span className={`font-bold ${tone}`}>{status === "pass" ? "\u2713" : status === "warn" ? "!" : "\u2717"}</span>;
}

function ClaimRow({ claim, busy, onSave }: { claim: ClaimRead; busy: boolean; onSave: (claim: ClaimRead, body: { included?: boolean; text?: string }) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(claim.text);
  useEffect(() => setText(claim.text), [claim.text]);
  function submit(event: FormEvent) { event.preventDefault(); onSave(claim, { text }); setEditing(false); }
  return <li className={`rounded-xl border p-3 ${claim.verified ? "border-slate-200" : "border-rose-300 bg-rose-50"} ${claim.included ? "" : "opacity-60"}`}>
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <Badge>{claim.section}</Badge>
      {claim.label && <span className="font-medium">{claim.label}</span>}
      <span className={claim.verified ? "text-emerald-700" : "font-semibold text-rose-700"}>{claim.verified ? "\u2713 supported" : "\u2717 unsupported"}</span>
      {claim.rewritten_by && <span className="text-slate-500">edited by {claim.rewritten_by}</span>}
      {!claim.included && <span className="text-slate-500">excluded</span>}
      {claim.jd_requirement_ids.length > 0 && <span className="text-slate-500">JD: {claim.jd_requirement_ids.slice(0, 6).join(", ")}</span>}
    </div>
    {editing ? <form onSubmit={submit} className="mt-2 space-y-2"><textarea className={inputClass} rows={3} value={text} onChange={(event) => setText(event.target.value)} /><div className="flex gap-2"><button className={primaryClass} disabled={busy}>Save and re-verify</button><button type="button" className={buttonClass} onClick={() => { setEditing(false); setText(claim.text); }}>Cancel</button></div></form>
      : <p className="mt-2 text-sm">{claim.text}</p>}
    {claim.changed && claim.original_text && <p className="mt-1 text-xs text-slate-500">Master wording: {claim.original_text}</p>}
    <p className="mt-1 text-xs text-slate-500">{claim.tailoring_reason}</p>
    {claim.verification_notes.length > 0 && <p className={`mt-1 text-xs ${claim.verified ? "text-slate-500" : "text-rose-700"}`}>{claim.verification_notes.join(" ")}</p>}
    <details className="mt-2 text-xs"><summary className="cursor-pointer text-slate-600">Source facts ({claim.source_facts.length})</summary><ul className="mt-1 space-y-1">{claim.source_facts.map((fact) => <li key={fact.id} className="rounded bg-slate-50 p-2"><span className="font-medium">{fact.category} · {fact.experience_type}</span>: {fact.statement ?? "(fact no longer active)"}</li>)}</ul></details>
    {["experience", "project", "summary", "certification"].includes(claim.section) && <div className="mt-2 flex gap-2">
      <button className={buttonClass} disabled={busy} onClick={() => onSave(claim, { included: !claim.included })}>{claim.included ? "Exclude" : "Include"}</button>
      {!editing && <button className={buttonClass} disabled={busy} onClick={() => setEditing(true)}>Edit wording</button>}
    </div>}
  </li>;
}

export function ResumeReview({ variantId, onChanged }: { variantId: string; onChanged: () => void }) {
  const [variant, setVariant] = useState<VariantDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<"compare" | "claims">("compare");
  const load = useCallback(async (signal?: AbortSignal) => {
    setError("");
    try { setVariant(await apiRequest<VariantDetail>(`/api/v1/resume-variants/${variantId}`, undefined, signal)); }
    catch (requestError) { if (!isAbort(requestError)) setError(errorMessage(requestError)); }
  }, [variantId]);
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [load]);

  async function act(path: string, init?: RequestInit) {
    setBusy(true); setError("");
    try { setVariant(await apiRequest<VariantDetail>(path, init)); onChanged(); }
    catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }
  function saveClaim(claim: ClaimRead, body: { included?: boolean; text?: string }) {
    void act(`/api/v1/resume-variants/${variantId}/claims/${claim.id}`, { method: "PATCH", body: JSON.stringify(body) });
  }

  if (error && !variant) return <ErrorNotice message={error} retry={() => void load()} />;
  if (!variant) return <p role="status" className="text-sm text-slate-500">Loading tailored resume…</p>;
  const changes = variant.changes ?? {};
  const unsupported = variant.truth_validation?.unsupported ?? [];
  return <div className="space-y-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p className="text-sm font-semibold">{variant.status === "APPROVED" ? "\u2713 Approved for your application" : variant.truth_passed ? "Tailored resume ready for your review" : "Needs fixes before approval"}</p>
        <p className="text-xs text-slate-500">v{variant.version} · {variant.generator} · trigger: {variant.trigger} · {variant.source_fact_count} source facts · positioning: {variant.positioning?.archetype ?? "generic"}{variant.positioning?.signals?.length ? ` (${variant.positioning.signals.slice(0, 4).join(", ")})` : ""}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button className={primaryClass} disabled={busy || !variant.truth_passed || variant.status === "APPROVED"} onClick={() => void act(`/api/v1/resume-variants/${variantId}/approve`, { method: "POST" })}>Approve application resume</button>
        <button className={buttonClass} disabled={busy || variant.status === "REJECTED"} onClick={() => void act(`/api/v1/resume-variants/${variantId}/reject`, { method: "POST" })}>Reject</button>
        {["docx", "html", "md", "txt"].map((format) => <a key={format} className={buttonClass} href={`${API_URL}/api/v1/resume-variants/${variantId}/download?format=${format}`}>.{format}</a>)}
      </div>
    </div>
    <ErrorNotice message={error} />
    <div className="grid gap-3 md:grid-cols-[1fr_2fr]">
      <div className="rounded-xl bg-slate-50 p-4">
        <p className="text-xs text-slate-500">Resume alignment</p>
        <p className="text-3xl font-semibold">{score(variant.alignment_score)}</p>
        <dl className="mt-2 space-y-1 text-xs">{Object.entries(variant.alignment_breakdown).map(([key, value]) => <div key={key} className="flex justify-between"><dt>{statusLabel(key)}</dt><dd className={key === "truth_confidence" && value < 100 ? "font-bold text-rose-700" : ""}>{score(value)}</dd></div>)}</dl>
        <p className="mt-2 text-[11px] text-slate-500">Truth confidence is never traded for alignment: unsupported claims block approval.</p>
      </div>
      <div className="rounded-xl border border-slate-200 p-4">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Quality gate</p>
        <ul className="mt-2 space-y-1 text-sm">{variant.quality_checks.map((check) => <li key={check.check} className="flex gap-2"><CheckIcon status={check.status} /><span><span className="font-medium">{statusLabel(check.check)}:</span> {check.message}</span></li>)}</ul>
        {unsupported.length > 0 && <div className="mt-3"><ErrorNotice message={`Unsupported claims: ${unsupported.map((item) => `"${item.text.slice(0, 80)}" (${item.notes.join(" ")})`).join(" · ")}`} /></div>}
      </div>
    </div>
    <div className="rounded-xl border border-slate-200 p-4 text-sm">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Tailoring changes (master vs this resume)</p>
      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <div>
          <p className="font-medium">Added emphasis</p>
          <ul className="mt-1 text-xs text-emerald-800">{(changes.added_emphasis ?? []).slice(0, 10).map((item, index) => <li key={index}>+ {item.term} <span className="text-slate-500">({item.where})</span></li>)}</ul>
          {changes.headline_added && <p className="mt-2 text-xs">Headline: <span className="font-medium">{changes.headline_added}</span></p>}
          <p className="mt-2 text-xs">Summary changed: <span className="font-medium">{changes.summary_changed ? "Yes" : "No"}</span></p>
          <p className="mt-1 text-xs">Unsupported claims: <span className={`font-medium ${(changes.unsupported_claims ?? 0) > 0 ? "text-rose-700" : ""}`}>{changes.unsupported_claims ?? 0}</span></p>
          {changes.section_order && <p className="mt-1 text-xs">Section order changed: {changes.section_order.reason}</p>}
        </div>
        <div>
          <p className="font-medium">Reordered</p>
          <ul className="mt-1 text-xs">{(changes.reordered ?? []).slice(0, 10).map((item, index) => <li key={index}>{item.direction === "up" ? "\u2191" : "\u2193"} {item.label} <span className="text-slate-500">({item.entry}: {item.from} → {item.to})</span></li>)}{(changes.reordered ?? []).length === 0 && <li className="text-slate-500">Bullet order already matched this JD.</li>}</ul>
          <p className="mt-2 font-medium">De-emphasized</p>
          <ul className="mt-1 text-xs text-slate-600">{(changes.de_emphasized ?? []).slice(0, 8).map((item, index) => <li key={index}>{"\u2193"} {item.text} <span className="text-slate-500">— {item.reason}</span></li>)}{(changes.de_emphasized ?? []).length === 0 && <li className="text-slate-500">Nothing omitted.</li>}</ul>
        </div>
      </div>
      {(changes.skills_reordered ?? []).length > 0 && <details className="mt-3 text-xs"><summary className="cursor-pointer">Skills reordered ({changes.skills_reordered?.length})</summary><ul className="mt-1 space-y-1">{changes.skills_reordered?.map((item) => <li key={item.group}><span className="font-medium">{item.group}:</span> {item.after.join(", ")}</li>)}</ul></details>}
      {(changes.terminology ?? []).length > 0 && <p className="mt-2 text-xs">Terminology normalised: {changes.terminology?.map((item) => `${item.before} → ${item.after}`).join("; ")}</p>}
      {(changes.rewrites ?? []).length > 0 && <details className="mt-2 text-xs"><summary className="cursor-pointer">AI rewording ({changes.rewrites?.filter((item) => item.accepted).length} accepted, {changes.rewrites?.filter((item) => !item.accepted).length} discarded)</summary><ul className="mt-1 space-y-1">{changes.rewrites?.map((item, index) => <li key={index} className={item.accepted ? "text-emerald-800" : "text-rose-700"}>{item.accepted ? "\u2713" : "\u2717"} {item.after ?? ""} {item.notes.join(" ")}</li>)}</ul></details>}
    </div>
    <div className="flex gap-2"><button className={view === "compare" ? primaryClass : buttonClass} onClick={() => setView("compare")}>Master vs tailored</button><button className={view === "claims" ? primaryClass : buttonClass} onClick={() => setView("claims")}>Claims & sources ({variant.claims.length})</button></div>
    {view === "compare" ? <div className="grid gap-4 lg:grid-cols-2">
      <div className="rounded-xl border border-slate-200 p-4"><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">Master resume</p><Blocks blocks={variant.master_blocks} /></div>
      <div className="rounded-xl border border-brand-600/40 p-4"><p className="mb-2 text-xs font-semibold uppercase tracking-wider text-brand-700">Tailored for {variant.company} — {variant.job_title}</p><Blocks blocks={variant.blocks} /></div>
    </div> : <ul className="space-y-2">{variant.claims.filter((claim) => claim.section !== "role").map((claim) => <ClaimRow key={claim.id} claim={claim} busy={busy} onSave={saveClaim} />)}</ul>}
    <p className="text-xs text-slate-500">Edits are re-verified against the cited facts. To claim something new, add it to your profile first; the resume will then cite it. <span className={levelTone("STRONG") + " rounded px-1"}>No unsupported claim can be approved.</span></p>
  </div>;
}
