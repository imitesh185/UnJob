// Phase 3 API contracts (candidate intelligence, tailoring, applications). Timestamps are UTC ISO.

export type PriorityClass = "P0" | "P1" | "P2" | "P3" | "REJECT";
export type ApplicationStatus =
  | "DISCOVERED" | "ANALYZED" | "RECOMMENDED" | "RESUME_GENERATED" | "USER_REVIEW" | "APPROVED"
  | "READY_TO_APPLY" | "APPLICATION_STARTED" | "APPLIED" | "OA" | "RECRUITER_SCREEN" | "TECHNICAL"
  | "FINAL" | "OFFER" | "REJECTED" | "WITHDRAWN";

export interface CandidateRead {
  id: string;
  full_name: string | null;
  email: string | null;
  phone: string | null;
  links: string[];
  headline: string | null;
  current_title: string | null;
  current_company: string | null;
  location: string | null;
  years_experience: number | null;
  years_experience_source: string | null;
  target_roles: string[];
  preferred_locations: string[];
  remote_preference: string | null;
  open_to_relocation: boolean | null;
  compensation_target: string | null;
  compensation_min: number | null;
  compensation_currency: string | null;
  notice_period: string | null;
  domains: string[];
  preference_sources: Record<string, string>;
  profile_version: number;
  updated_at: string | null;
}

export interface ResumeInfo {
  id: string;
  version: number;
  filename: string;
  parser: string;
  warnings: string[];
  section_order: string[];
  uploaded_at: string | null;
}

export interface FactRead {
  id: string;
  category: string;
  statement: string;
  label: string | null;
  source: string;
  experience_type: string;
  verified: boolean;
  technologies: string[];
  concepts: string[];
  metrics: string[];
  employer: string | null;
  role_title: string | null;
  start_date: string | null;
  end_date: string | null;
  active: boolean;
}

export interface SkillRead {
  fact_id: string;
  name: string;
  level: "STRONG" | "MODERATE" | "RUSTY" | "PROJECT" | "LISTED";
  level_source: "inferred" | "user";
  note: string | null;
  roles: string[];
  last_used: string | null;
  evidence_fact_ids: string[];
}

export interface ProfileResponse {
  candidate: CandidateRead | null;
  resume: ResumeInfo | null;
  facts: FactRead[];
  skills: SkillRead[];
  stats: { facts?: number; verified?: number; skills?: number; user_added?: number };
  import?: { unchanged: boolean; facts_created: number; warnings: string[]; analysis_run_id: string | null };
  analysis_run_id?: string | null;
}

export interface VariantSummary {
  id: string;
  job_id: string | null;
  version: number;
  status: "GENERATED" | "USER_REVIEW" | "APPROVED" | "SUPERSEDED" | "STALE" | "REJECTED";
  generator: string;
  trigger: string;
  alignment_score: number;
  alignment_breakdown: Record<string, number>;
  truth_passed: boolean;
  unsupported_claims: number;
  ats_status: string | null;
  positioning: { archetype?: string | null; signals?: string[]; sources?: string[] };
  generated_at: string | null;
  approved_at: string | null;
  job_title?: string | null;
  company?: string | null;
  tier?: string | null;
}

export interface Opportunity {
  job_id: string;
  title: string;
  company: string;
  company_id: string | null;
  tier: string | null;
  location: string;
  remote_status: string;
  ats: string;
  application_url: string;
  posted_at: string | null;
  posted_at_precision: string | null;
  freshness_status: string;
  age_hours: number | null;
  fit_score: number;
  company_target_score: number;
  priority_score: number;
  priority_class: PriorityClass;
  recommendation: string;
  gates: string[];
  why: string[];
  strong_matches: string[];
  gaps: string[];
  seniority_fit: string | null;
  location_fit: { status?: string; note?: string };
  effort_minutes: number | null;
  resume: VariantSummary | null;
  application: { id: string; status: ApplicationStatus } | null;
}

export interface TodayResponse {
  profile_ready: boolean;
  plan: Opportunity[];
  plan_minutes?: number;
  counts?: { P0: number; P1: number; P2: number };
  new_companies_today: number;
  fresh_hiring_companies?: { id: string; name: string; tier: string; fresh_roles: number }[];
  awaiting_review?: number;
  approved?: number;
  interview_pipeline?: Record<string, number>;
  interviews_active?: number;
  s_tier?: { opportunities: number; tailored: number };
}

export interface Requirement {
  id: string;
  text: string;
  kind: string;
  technologies: string[];
  concepts: string[];
  mode: string;
  years: number | null;
}

export interface SkillAssessment {
  name: string;
  importance: "required" | "preferred" | "alternative";
  status: string;
  label: string;
  note: string;
  via: string | null;
  fact_ids: string[];
}

export interface StrategyItem { term: string; why: string }
export interface Concern { concern: string; handle: string }

export interface OpportunityDetail {
  job: {
    id: string; title: string; company: string; company_id: string | null; tier: string | null;
    location: string; remote_status: string; employment_type: string | null; salary: string | null;
    ats: string; application_url: string; posted_at: string | null; posted_at_precision: string | null;
    freshness_status: string; age_hours: number | null; listing_status: string; description: string;
    role_category: string; sources: { source: string; source_url: string; last_seen_at: string | null }[];
  };
  profile_ready: boolean;
  analysis: {
    seniority: string; years_min: number | null; years_max: number | null; requirements: Requirement[];
    required_skills: { name: string; weight: number }[]; preferred_skills: { name: string; weight: number }[];
    responsibilities: string[]; categories: Record<string, string[]>; domains: string[];
    leadership_signals: string[]; keywords: string[]; business_context: string | null;
    location_requirements: Record<string, unknown>; compensation: Record<string, unknown> | null;
    role_focus: string; warnings: string[];
  } | null;
  match: {
    fit_score: number; breakdown: Record<string, number>; seniority_fit: string;
    requirement_matches: { id: string; kind: string; text: string; status?: string; score?: number | null; terms?: { name: string; status: string }[]; note?: string }[];
    skill_assessments: SkillAssessment[]; strong_matches: string[]; partial_matches: string[];
    gaps: { name: string; importance: string; note: string }[]; explanation: string[];
    location_fit: { status?: string; note?: string }; compensation_fit: { status?: string; note?: string };
  } | null;
  company_target: { score: number; breakdown: Record<string, number>; reasons: string[] } | null;
  priority: {
    priority_class: PriorityClass; priority_score: number; fit_score: number; company_target_score: number;
    freshness_score: number; career_value_score: number; gates: string[]; reasons: string[];
    recommendation: string;
    strategy: {
      why_this_role: string[]; emphasize: StrategyItem[]; de_emphasize: string[]; concerns: Concern[];
      positioning: { archetype: string | null; signals: string[]; sources: string[] }; effort_minutes?: number;
    } | null;
  } | null;
  resume: VariantSummary | null;
  variants: VariantSummary[];
  application: (ApplicationRead & { events: ApplicationEventRead[] }) | null;
}

export interface ClaimRead {
  id: string;
  section: string;
  entry_key: string | null;
  position: number;
  label: string | null;
  text: string;
  original_text: string | null;
  changed: boolean;
  source_facts: { id: string; statement: string | null; category: string | null; experience_type: string | null }[];
  jd_requirement_ids: string[];
  tailoring_reason: string;
  relevance: number;
  verified: boolean;
  verification_notes: string[];
  included: boolean;
  rewritten_by: string | null;
}

export interface ResumeBlock { kind: "name" | "contact" | "headline" | "heading" | "paragraph" | "entry" | "bullet"; value: string | { title: string; org: string; meta: string } }

export interface VariantChanges {
  headline_added?: string;
  summary_changed?: boolean;
  summary_before?: string[];
  summary_after?: string[];
  added_emphasis?: { term: string; where: string }[];
  reordered?: { section: string; entry: string; label: string; from: number; to: number; direction: "up" | "down" }[];
  de_emphasized?: { section: string; text: string; reason: string }[];
  skills_reordered?: { group: string; before: string[]; after: string[] }[];
  terminology?: { before: string; after: string }[];
  section_order?: { before: string[]; after: string[]; reason: string };
  rewrites?: { label?: string; before?: string; after?: string; accepted: boolean; notes: string[] }[];
  unsupported_claims?: number;
  user_edits?: number;
}

export interface QualityCheck { check: string; status: "pass" | "warn" | "fail"; message: string }

export interface VariantDetail extends VariantSummary {
  job_title: string | null;
  company: string | null;
  blocks: ResumeBlock[];
  master_blocks: ResumeBlock[];
  claims: ClaimRead[];
  changes: VariantChanges;
  quality_checks: QualityCheck[];
  truth_validation: { passed?: boolean; total_claims?: number; verified_claims?: number; unsupported?: { section: string; text: string; notes: string[] }[] };
  source_fact_count: number;
  downloads: string[];
}

export interface ApplicationEventRead { id: string; from_status: string | null; to_status: string; note: string | null; actor: string; created_at: string | null }

export interface ApplicationRead {
  id: string;
  job_id: string;
  company_id: string | null;
  status: ApplicationStatus;
  source: string | null;
  priority_class: PriorityClass | null;
  priority_score: number | null;
  fit_score: number | null;
  applied_at: string | null;
  recruiter: string | null;
  hiring_manager: string | null;
  notes: string | null;
  next_action: string | null;
  job_title: string | null;
  company: string | null;
  tier: string | null;
  application_url: string | null;
  location: string | null;
  resume: VariantSummary | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AnalyticsGroup { value: string; applications: number; responses: number; interviews: number; interview_rate: number | null; low_sample: boolean }

export interface AnalyticsResponse {
  totals: { tracked: number; applied: number; responses: number; interviews: number; offers: number; interview_rate: number | null; resume_versions: number; companies: number };
  status_counts: Record<string, number>;
  by_company: AnalyticsGroup[];
  by_tier: AnalyticsGroup[];
  by_role: AnalyticsGroup[];
  by_technology: AnalyticsGroup[];
  by_positioning: AnalyticsGroup[];
  by_industry: AnalyticsGroup[];
  by_company_size: AnalyticsGroup[];
  by_source: AnalyticsGroup[];
  minimum_sample: number;
  note: string;
}

export const APPLICATION_FLOW: ApplicationStatus[] = [
  "RECOMMENDED", "RESUME_GENERATED", "USER_REVIEW", "APPROVED", "READY_TO_APPLY", "APPLICATION_STARTED",
  "APPLIED", "OA", "RECRUITER_SCREEN", "TECHNICAL", "FINAL", "OFFER", "REJECTED", "WITHDRAWN",
];

export function priorityTone(value: string | null | undefined): string {
  switch (value) {
    case "P0": return "bg-emerald-600 text-white";
    case "P1": return "bg-emerald-100 text-emerald-800";
    case "P2": return "bg-amber-100 text-amber-800";
    case "P3": return "bg-slate-100 text-slate-700";
    default: return "bg-rose-50 text-rose-700";
  }
}

export function levelTone(value: string | null | undefined): string {
  switch (value) {
    case "STRONG": return "bg-emerald-100 text-emerald-800";
    case "MODERATE": return "bg-sky-100 text-sky-800";
    case "TRANSFERABLE": return "bg-violet-100 text-violet-800";
    case "HISTORICAL": case "RUSTY": return "bg-amber-100 text-amber-800";
    case "PROJECT": return "bg-indigo-100 text-indigo-800";
    case "LISTED": case "SELF_DESCRIBED": return "bg-slate-100 text-slate-700";
    case "GAP": return "bg-rose-100 text-rose-800";
    default: return "bg-slate-100 text-slate-700";
  }
}

const ACRONYMS = new Set(["oa", "ats", "jd", "llm", "ic", "vp"]);

export function statusLabel(value: string | null | undefined): string {
  if (!value) return "UNKNOWN";
  return value.split("_").map((word, index) => {
    const lower = word.toLowerCase();
    if (ACRONYMS.has(lower)) return lower.toUpperCase();
    return index === 0 ? lower.charAt(0).toUpperCase() + lower.slice(1) : lower;
  }).join(" ");
}

export function yearsText(min: number | null | undefined, max: number | null | undefined): string {
  if (min === null || min === undefined) return "";
  return max !== null && max !== undefined && max > min ? `${min}–${max} years` : `${min}+ years`;
}

export function tierLabel(tier: string | null | undefined): string {
  if (!tier) return "Tier unknown";
  return tier === "UNCATEGORIZED" ? "Uncategorized" : `${tier}-tier`;
}

export function score(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : String(Math.round(value));
}

export function freshnessText(status: string | null | undefined, ageHours: number | null | undefined): string {
  if (ageHours === null || ageHours === undefined || status === "unknown") return "Posting date UNKNOWN";
  const age = ageHours < 24 ? `${Math.round(ageHours)}h` : `${Math.round(ageHours / 24)}d`;
  const label = status === "fresh" ? "Fresh" : status === "recent" ? "Recent" : "Older";
  return `${label} · ≤${age} old`;
}

export function blockText(block: ResumeBlock): string {
  if (typeof block.value === "string") return block.value;
  return [block.value.title, block.value.org].filter(Boolean).join(" — ") + (block.value.meta ? ` (${block.value.meta})` : "");
}
