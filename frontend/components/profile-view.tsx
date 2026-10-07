"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { API_URL, ApiError, apiRequest } from "@/lib/api";
import { dateLabel, errorMessage, isAbort } from "@/lib/discovery";
import { levelTone, statusLabel, type FactRead, type ProfileResponse, type SkillRead } from "@/lib/intel";
import type { RunRead } from "@/lib/types";
import { Badge, buttonClass, ErrorNotice, Field, inputClass, panelClass, PhaseShell, primaryClass, RunMonitor, useRunMonitor } from "./discovery-shared";

const LEVELS: SkillRead["level"][] = ["STRONG", "MODERATE", "RUSTY", "PROJECT", "LISTED"];
const CATEGORY_LABELS: Record<string, string> = {
  summary: "Summary statements", role: "Roles", experience: "Experience bullets", tech_stack: "Role tech stacks",
  project_entry: "Projects", project: "Project bullets", project_stack: "Project stacks", skill_group: "Skill lists",
  education: "Education", certification: "Certifications", achievement: "Achievements",
};

async function uploadResume(file: File): Promise<ProfileResponse> {
  const form = new FormData();
  form.append("file", file);
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/v1/profile/resume`, { method: "POST", body: form });
  } catch {
    throw new ApiError("Could not reach the API. Check your connection and try again.");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(typeof body.detail === "string" ? body.detail : `Upload failed (${response.status}).`, response.status);
  return body as ProfileResponse;
}

function listValue(values: string[]): string { return values.join("\n"); }
function parseList(value: string): string[] { return value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean); }

export function ProfileView() {
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const monitor = useRunMonitor(refresh);

  useEffect(() => {
    const controller = new AbortController();
    apiRequest<ProfileResponse>("/api/v1/profile", undefined, controller.signal)
      .then(setProfile)
      .catch((requestError) => { if (!isAbort(requestError)) setError(errorMessage(requestError)); });
    return () => controller.abort();
  }, [revision]);

  async function trackRun(runId: string | null | undefined) {
    if (!runId) return;
    try { monitor.track(await apiRequest<RunRead>(`/api/v1/discovery/runs/${runId}`)); } catch { /* status is optional */ }
  }

  async function onUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setNotice("");
    const input = event.currentTarget.elements.namedItem("resume") as HTMLInputElement | null;
    const file = input?.files?.[0];
    if (!file) { setError("Choose a PDF, DOCX or TXT resume."); return; }
    setBusy(true);
    try {
      const result = await uploadResume(file);
      setProfile(result);
      setNotice(result.import?.unchanged ? "This resume is already your master resume." : `Imported ${result.import?.facts_created ?? 0} facts. Re-analysing jobs in the background.`);
      await trackRun(result.import?.analysis_run_id);
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }

  const candidate = profile?.candidate ?? null;
  const facts = profile?.facts ?? [];
  const grouped = facts.reduce<Record<string, FactRead[]>>((groups, fact) => { (groups[fact.category] ??= []).push(fact); return groups; }, {});
  return <PhaseShell current="profile" title="Candidate profile" description="Your master resume as verifiable facts. Tailored resumes may only reorder, select and emphasize these facts — never invent new ones.">
    <RunMonitor monitor={monitor} />
    <section className={panelClass} aria-labelledby="resume-upload">
      <h2 id="resume-upload" className="text-lg font-semibold">Master resume</h2>
      {profile?.resume ? <p className="mt-1 text-sm text-slate-600">{profile.resume.filename} · version {profile.resume.version} · imported {dateLabel(profile.resume.uploaded_at)} · parser {profile.resume.parser}</p> : <p className="mt-1 text-sm text-slate-600">No master resume yet. Upload a text-based PDF, DOCX or TXT file. It stays on this machine.</p>}
      {profile?.resume?.warnings.length ? <ErrorNotice message={`Parser warnings: ${profile.resume.warnings.join(" ")}`} /> : null}
      <form onSubmit={onUpload} className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
        <Field label="Resume file"><input name="resume" type="file" accept=".pdf,.docx,.txt,.md" className={inputClass} /></Field>
        <button className={primaryClass} disabled={busy}>{busy ? "Importing…" : profile?.resume ? "Replace master resume" : "Import master resume"}</button>
      </form>
      <div className="mt-3" aria-live="polite">{notice && <p className="text-sm text-brand-700">{notice}</p>}<ErrorNotice message={error} /></div>
    </section>
    {candidate && <Preferences profile={profile!} onSaved={(result) => { setProfile(result); void trackRun(result.analysis_run_id); }} />}
    {candidate && <Skills skills={profile!.skills} onSaved={(runId) => { refresh(); void trackRun(runId); }} />}
    {candidate && <section className={panelClass} aria-labelledby="facts-title">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 id="facts-title" className="text-lg font-semibold">Facts ({profile!.stats.facts ?? 0}, {profile!.stats.verified ?? 0} verified)</h2><a className={buttonClass} href={`${API_URL}/api/v1/profile/master-resume`} target="_blank" rel="noopener noreferrer">Master resume (JSON/Markdown)</a></div>
      <p className="mt-1 text-xs text-slate-500">Resume facts are copied verbatim. Experience type controls how a fact may be presented: project or listed skills are never presented as production experience.</p>
      <div className="mt-4 space-y-4">{Object.entries(grouped).map(([category, items]) => <details key={category} open={category === "experience"} className="rounded-xl border border-slate-200 p-3">
        <summary className="cursor-pointer text-sm font-semibold">{CATEGORY_LABELS[category] ?? statusLabel(category)} ({items.length})</summary>
        <ul className="mt-2 space-y-2">{items.map((fact) => <FactRow key={fact.id} fact={fact} onSaved={(runId) => { refresh(); void trackRun(runId); }} />)}</ul>
      </details>)}</div>
      <AddFact onSaved={(runId) => { refresh(); void trackRun(runId); }} />
    </section>}
  </PhaseShell>;
}

function Preferences({ profile, onSaved }: { profile: ProfileResponse; onSaved: (profile: ProfileResponse) => void }) {
  const candidate = profile.candidate!;
  const [roles, setRoles] = useState(listValue(candidate.target_roles));
  const [locations, setLocations] = useState(listValue(candidate.preferred_locations));
  const [relocation, setRelocation] = useState(candidate.open_to_relocation === null ? "" : String(candidate.open_to_relocation));
  const [compensation, setCompensation] = useState(candidate.compensation_min ? String(candidate.compensation_min) : "");
  const [currency, setCurrency] = useState(candidate.compensation_currency ?? "INR");
  const [notice, setNotice] = useState(candidate.notice_period ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault(); setError(""); setBusy(true);
    const body: Record<string, unknown> = { target_roles: parseList(roles), preferred_locations: parseList(locations), notice_period: notice || null };
    if (relocation) body.open_to_relocation = relocation === "true";
    if (compensation.trim()) {
      const value = Number(compensation);
      if (!Number.isFinite(value) || value <= 0) { setError("Compensation must be a positive number (annual)."); setBusy(false); return; }
      body.compensation_min = value; body.compensation_currency = currency.toUpperCase();
    }
    try { onSaved(await apiRequest<ProfileResponse>("/api/v1/profile", { method: "PATCH", body: JSON.stringify(body) })); }
    catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }
  return <section className={panelClass} aria-labelledby="prefs-title">
    <h2 id="prefs-title" className="text-lg font-semibold">{candidate.full_name ?? "Candidate"}</h2>
    <p className="mt-1 text-sm text-slate-600">{candidate.current_title ?? "Title UNKNOWN"}{candidate.current_company ? ` at ${candidate.current_company}` : ""} · {candidate.location ?? "Location UNKNOWN"} · {candidate.years_experience ?? "UNKNOWN"} years <span className="text-xs text-slate-500">({candidate.years_experience_source ?? "not computed"})</span></p>
    <p className="mt-1 text-xs text-slate-500">Domains from your experience: {candidate.domains.join(", ") || "UNKNOWN"}</p>
    <form onSubmit={save} className="mt-4 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
      <Field label="Target roles (one per line)"><textarea rows={5} className={inputClass} value={roles} onChange={(event) => setRoles(event.target.value)} /></Field>
      <Field label="Preferred locations (one per line; e.g. Mumbai, India, Remote (India))"><textarea rows={5} className={inputClass} value={locations} onChange={(event) => setLocations(event.target.value)} /></Field>
      <div className="space-y-3">
        <Field label="Open to relocation"><select className={inputClass} value={relocation} onChange={(event) => setRelocation(event.target.value)}><option value="">UNKNOWN</option><option value="true">Yes</option><option value="false">No</option></select></Field>
        <Field label="Minimum annual compensation"><div className="flex gap-2"><input className={inputClass} inputMode="numeric" value={compensation} onChange={(event) => setCompensation(event.target.value)} placeholder="e.g. 4000000" /><input className={`${inputClass} w-24`} value={currency} onChange={(event) => setCurrency(event.target.value)} maxLength={3} /></div></Field>
        <Field label="Notice period"><input className={inputClass} value={notice} onChange={(event) => setNotice(event.target.value)} /></Field>
      </div>
      <div className="md:col-span-2 lg:col-span-3"><button className={primaryClass} disabled={busy}>{busy ? "Saving…" : "Save preferences and re-rank"}</button><ErrorNotice message={error} /></div>
    </form>
  </section>;
}

function Skills({ skills, onSaved }: { skills: SkillRead[]; onSaved: (runId: string | null) => void }) {
  const [error, setError] = useState("");
  async function setLevel(skill: SkillRead, level: string) {
    setError("");
    try {
      const result = await apiRequest<{ analysis_run_id: string | null }>(`/api/v1/profile/facts/${skill.fact_id}`, { method: "PATCH", body: JSON.stringify({ skill_level: level }) });
      onSaved(result.analysis_run_id);
    } catch (requestError) { setError(errorMessage(requestError)); }
  }
  return <section className={panelClass} aria-labelledby="skills-title">
    <h2 id="skills-title" className="text-lg font-semibold">Skills by evidence ({skills.length})</h2>
    <p className="mt-1 text-xs text-slate-500">Levels are inferred from where a skill appears: production roles (and how recently), projects, or only your skills list. Override a level if it is wrong; your choice is kept on re-import.</p>
    <ErrorNotice message={error} />
    <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[720px] text-left text-sm">
      <thead><tr className="border-b text-xs text-slate-500"><th className="py-2">Skill</th><th>Level</th><th>Evidence</th><th>Last used</th><th>Set level</th></tr></thead>
      <tbody>{skills.map((skill) => <tr key={skill.fact_id} className="border-b border-slate-100">
        <td className="py-2 font-medium">{skill.name}</td>
        <td><span className={`rounded px-2 py-0.5 text-xs font-semibold ${levelTone(skill.level)}`}>{skill.level}</span>{skill.level_source === "user" && <span className="ml-1 text-xs text-slate-500">(set by you)</span>}</td>
        <td className="text-xs text-slate-600">{skill.note}</td>
        <td className="text-xs">{skill.last_used ?? "—"}</td>
        <td><select className={inputClass} value={skill.level} onChange={(event) => void setLevel(skill, event.target.value)} aria-label={`Level for ${skill.name}`}>{LEVELS.map((level) => <option key={level}>{level}</option>)}</select></td>
      </tr>)}</tbody>
    </table></div>
  </section>;
}

function FactRow({ fact, onSaved }: { fact: FactRead; onSaved: (runId: string | null) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function patch(body: Record<string, unknown>) {
    setBusy(true); setError("");
    try { const result = await apiRequest<{ analysis_run_id: string | null }>(`/api/v1/profile/facts/${fact.id}`, { method: "PATCH", body: JSON.stringify(body) }); onSaved(result.analysis_run_id); }
    catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }
  return <li className="rounded-lg bg-slate-50 p-3 text-sm">
    <p>{fact.label ? <span className="font-medium">{fact.label}: </span> : null}{fact.statement}</p>
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-600">
      <Badge>{fact.experience_type}</Badge>
      <span>{fact.source === "user" ? "Added by you" : "From resume"}</span>
      {fact.employer && <span>{fact.employer}</span>}
      {fact.technologies.length > 0 && <span>Tech: {fact.technologies.join(", ")}</span>}
      {fact.metrics.length > 0 && <span>Metrics: {fact.metrics.join(", ")}</span>}
      {["experience", "project"].includes(fact.category) && <select className="rounded border border-slate-200 bg-white px-1 py-0.5" value={fact.experience_type} disabled={busy} onChange={(event) => void patch({ experience_type: event.target.value })} aria-label="Experience type"><option>PRODUCTION</option><option>PROJECT</option><option>ACADEMIC</option></select>}
      <button className="underline" disabled={busy} onClick={() => void patch({ active: false })}>Exclude from tailoring</button>
    </div>
    <ErrorNotice message={error} />
  </li>;
}

function AddFact({ onSaved }: { onSaved: (runId: string | null) => void }) {
  const [statement, setStatement] = useState("");
  const [category, setCategory] = useState("experience");
  const [type, setType] = useState("PRODUCTION");
  const [employer, setEmployer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      const result = await apiRequest<{ analysis_run_id: string | null }>("/api/v1/profile/facts", { method: "POST", body: JSON.stringify({ category, statement, experience_type: type, employer: employer || null }) });
      setStatement(""); onSaved(result.analysis_run_id);
    } catch (requestError) { setError(errorMessage(requestError)); }
    finally { setBusy(false); }
  }
  return <form onSubmit={submit} className="mt-6 grid gap-3 rounded-xl border border-dashed border-slate-300 p-4 md:grid-cols-4">
    <p className="text-sm font-semibold md:col-span-4">Add a fact you can stand behind</p>
    <Field label="Statement"><textarea className={inputClass} rows={3} required minLength={3} value={statement} onChange={(event) => setStatement(event.target.value)} /></Field>
    <Field label="Category"><select className={inputClass} value={category} onChange={(event) => setCategory(event.target.value)}><option value="experience">Experience</option><option value="project">Project</option><option value="achievement">Achievement</option><option value="certification">Certification</option></select></Field>
    <Field label="Experience type"><select className={inputClass} value={type} onChange={(event) => setType(event.target.value)}><option>PRODUCTION</option><option>PROJECT</option><option>ACADEMIC</option><option>CERTIFICATION</option></select></Field>
    <Field label="Employer (for experience)"><input className={inputClass} value={employer} onChange={(event) => setEmployer(event.target.value)} /></Field>
    <div className="md:col-span-4"><button className={primaryClass} disabled={busy}>Add fact</button><ErrorNotice message={error} /></div>
  </form>;
}
