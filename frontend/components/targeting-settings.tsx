"use client";

import { FormEvent, useState } from "react";
import { apiRequest } from "@/lib/api";
import { errorMessage } from "@/lib/discovery";
import type { DiscoverySettings, RegistryEntry, TailoringRule } from "@/lib/types";
import { ErrorNotice, Field, inputClass, primaryClass } from "./discovery-shared";

const MODES: { value: TailoringRule["mode"]; label: string }[] = [
  { value: "all_relevant", label: "Every relevant job" },
  { value: "min_fit", label: "Fit at least…" },
  { value: "priority", label: "Priority classes…" },
  { value: "min_priority_score", label: "Priority score at least…" },
  { value: "off", label: "Never automatically" },
];

function registryText(entries: RegistryEntry[]): string {
  return entries.map((entry) => [entry.name, entry.aliases.join(", "), entry.career_urls.join(" ")].join(" | ")).join("\n");
}

function parseRegistry(text: string): RegistryEntry[] {
  return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const [name, aliases = "", urls = ""] = line.split("|").map((part) => part.trim());
    if (!name) throw new Error(`Registry line "${line}" needs a company name.`);
    const careerUrls = urls.split(/\s+/).filter(Boolean);
    for (const url of careerUrls) if (!url.startsWith("https://")) throw new Error(`Career URL must start with https:// (${url}).`);
    return { name, aliases: aliases.split(",").map((alias) => alias.trim()).filter(Boolean), career_urls: careerUrls };
  });
}

export function TargetingSettings({ settings, onSaved }: { settings: DiscoverySettings; onSaved: (settings: DiscoverySettings) => void }) {
  const [sTier, setSTier] = useState((settings.s_tier_companies ?? []).join("\n"));
  const [policy, setPolicy] = useState<Record<string, TailoringRule>>(settings.tailoring_policy ?? {});
  const [registry, setRegistry] = useState(registryText(settings.company_registry ?? []));
  const [locationFit, setLocationFit] = useState(settings.tailoring_require_location_fit ?? true);
  const [maxVariants, setMaxVariants] = useState(String(settings.max_variants_per_run ?? 30));
  const [llmEnabled, setLlmEnabled] = useState(settings.llm_rewrite_enabled ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function updateRule(tier: string, patch: Partial<TailoringRule>) {
    setPolicy((current) => ({ ...current, [tier]: { ...(current[tier] ?? { mode: "off" }), ...patch } }));
  }

  async function save(event: FormEvent) {
    event.preventDefault(); setError(""); setNotice("");
    let body: Record<string, unknown>;
    try {
      const variants = Number(maxVariants);
      if (!Number.isInteger(variants) || variants < 0 || variants > 500) throw new Error("Max tailored resumes per run must be a whole number from 0 to 500.");
      const rules: Record<string, TailoringRule> = {};
      for (const [tier, rule] of Object.entries(policy)) {
        if ((rule.mode === "min_fit" || rule.mode === "min_priority_score") && (rule.threshold === null || rule.threshold === undefined || Number.isNaN(rule.threshold))) throw new Error(`Tier ${tier}: enter a threshold.`);
        if (rule.mode === "priority" && !(rule.classes ?? []).length) throw new Error(`Tier ${tier}: choose at least one priority class.`);
        rules[tier] = { mode: rule.mode, threshold: rule.threshold ?? null, classes: rule.classes ?? [] };
      }
      body = {
        s_tier_companies: sTier.split(/\r?\n/).map((name) => name.trim()).filter(Boolean),
        tailoring_policy: rules,
        company_registry: parseRegistry(registry),
        tailoring_require_location_fit: locationFit,
        max_variants_per_run: variants,
        llm_rewrite_enabled: llmEnabled,
      };
    } catch (validation) { setError(errorMessage(validation)); return; }
    setBusy(true);
    try {
      const next = await apiRequest<DiscoverySettings & { analysis_run_id?: string }>("/api/v1/discovery/settings", { method: "PATCH", body: JSON.stringify(body) });
      onSaved(next);
      setNotice(next.analysis_run_id ? "Saved. Tiers were re-applied and a re-analysis is queued." : "Saved.");
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }

  return <form onSubmit={save} className="mt-8 space-y-5 border-t pt-6" aria-labelledby="targeting-title">
    <div><h3 id="targeting-title" className="text-lg font-semibold">Targeting and resume tailoring</h3><p className="mt-1 text-sm text-slate-500">S-tier companies get a dedicated, JD-specific resume for every relevant job. Tiers stay metadata: they never hide a company or job.</p></div>
    <div className="grid gap-4 lg:grid-cols-2">
      <Field label="S-tier companies (one per line)"><textarea rows={10} className={inputClass} value={sTier} onChange={(event) => setSTier(event.target.value)} /></Field>
      <div className="space-y-3">
        <p className="text-sm font-semibold">Automatic tailoring policy by tier</p>
        {settings.tiers.map((tier) => {
          const rule = policy[tier] ?? { mode: "off" };
          return <div key={tier} className="grid grid-cols-[96px_1fr_1fr] items-center gap-2 text-sm">
            <span className="break-words font-semibold">{tier === "UNCATEGORIZED" ? "Uncategorized" : tier}</span>
            <select className={inputClass} value={rule.mode} onChange={(event) => updateRule(tier, { mode: event.target.value as TailoringRule["mode"] })} aria-label={`Tailoring mode for tier ${tier}`}>{MODES.map((mode) => <option key={mode.value} value={mode.value}>{mode.label}</option>)}</select>
            {rule.mode === "min_fit" || rule.mode === "min_priority_score"
              ? <input className={inputClass} type="number" min={0} max={100} value={rule.threshold ?? ""} onChange={(event) => updateRule(tier, { threshold: event.target.value === "" ? null : Number(event.target.value) })} aria-label={`Threshold for tier ${tier}`} />
              : rule.mode === "priority"
                ? <div className="flex gap-2 text-xs">{["P0", "P1", "P2", "P3"].map((value) => <label key={value} className="flex items-center gap-1"><input type="checkbox" checked={(rule.classes ?? []).includes(value)} onChange={(event) => updateRule(tier, { classes: event.target.checked ? [...(rule.classes ?? []), value] : (rule.classes ?? []).filter((item) => item !== value) })} />{value}</label>)}</div>
                : <span className="text-xs text-slate-500">{rule.mode === "all_relevant" ? "Relevant = target role, open, location fits" : "Tailor on request only"}</span>}
          </div>;
        })}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={locationFit} onChange={(event) => setLocationFit(event.target.checked)} />Only tailor jobs in my preferred locations</label>
        <Field label="Max tailored resumes per analysis run"><input className={inputClass} type="number" min={0} max={500} value={maxVariants} onChange={(event) => setMaxVariants(event.target.value)} /></Field>
      </div>
    </div>
    <Field label="Company registry: Name | aliases (comma separated) | verified career portal URLs (space separated)"><textarea rows={8} className={`${inputClass} font-mono text-xs`} value={registry} onChange={(event) => setRegistry(event.target.value)} /></Field>
    <p className="text-xs text-slate-500">Registry portals are used for seed resolution and confirmed only when a scan succeeds; a portal that fails is marked BLOCKED or NOT_FOUND rather than trusted.</p>
    <div className="rounded-xl bg-slate-50 p-4 text-sm">
      <p className="font-semibold">AI rewording</p>
      <p className="mt-1 text-slate-600">{settings.llm_status?.detail ?? "Status unavailable."}</p>
      <label className="mt-2 flex items-center gap-2"><input type="checkbox" checked={llmEnabled} disabled={!settings.llm_status?.configured} onChange={(event) => setLlmEnabled(event.target.checked)} />Use AI rewording when configured (every rewrite is verified; unsupported rewrites are discarded)</label>
    </div>
    <div aria-live="polite"><ErrorNotice message={error} />{notice && <p className="text-sm text-brand-700">{notice}</p>}</div>
    <button className={primaryClass} disabled={busy}>{busy ? "Saving…" : "Save targeting settings"}</button>
  </form>;
}
