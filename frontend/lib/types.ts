export type ScoreBreakdown = Record<string, number | string | null>;

export type PostedAtPrecision = "datetime" | "local_datetime" | "date";
export type FreshnessStatus = "fresh" | "recent" | "stale" | "unknown";

export interface Job {
  id: string | number;
  company: string;
  title: string;
  location: string | null;
  remote_status: string | null;
  source?: string | null;
  sources?: JobSource[];
  company_id?: string | null;
  role_category?: string;
  role_relevance?: number;
  freshness_status?: FreshnessStatus | string;
  ats: string | null;
  posted_at: string | null;
  posted_at_precision?: PostedAtPrecision | null;
  source_updated_at?: string | null;
  listing_status?: string;
  last_seen_at?: string | null;
  closed_at?: string | null;
  discovered_at: string;
  age_hours: number | null;
  overall_score: number | null;
  score_breakdown: ScoreBreakdown;
  score_reasons: string[];
  status: string;
  application_url: string | null;
}

export interface JobsResponse {
  items: Job[];
  total: number;
}

export interface DashboardSummary {
  jobs_discovered_today: number;
  high_fit_jobs: number;
  applications_prepared: number;
  applications_submitted: number;
  blocked_applications: number;
  human_reviews: number;
  outreach_opportunities: number;
  interviews: number;
}

export type IngestionProvider = "greenhouse" | "lever";

/** One provenance record per place a job was observed (backend JobSourceRead). */
export interface JobSource {
  source: string;
  external_id: string;
  source_url: string;
  career_source_id: string | null;
  last_seen_at: string | null;
  listing_status: string;
}

export interface PageResponse<T> {
  items: T[];
  total: number;
}

export interface CompanyRead {
  id: string;
  name: string;
  domain: string | null;
  careers_url: string | null;
  tier: string;
  status: string;
  review_status: "PENDING" | "ACCEPTED" | "IGNORED" | string;
  is_seed: boolean;
  discovery_score: number;
  confidence: number;
  discovery_reason: string;
  industry: string | null;
  company_stage: string | null;
  headquarters: string | null;
  india_presence: boolean | null;
  remote_presence: boolean | null;
  known_technologies: string[];
  score_breakdown: Record<string, number>;
  score_reasons: string[];
  jobs_count: number;
  data_roles_count: number;
  fresh_data_roles_count: number;
  platform_roles_count: number;
  target_score?: number | null;
  target_breakdown?: Record<string, number>;
  target_reasons?: string[];
  tier_source?: string;
  last_scanned_at: string | null;
  last_explored_at?: string | null;
  discovery_source: string;
  created_at: string;
  updated_at: string;
}

export interface CareerSourceRead {
  id: string;
  company_id: string;
  url: string;
  platform: string;
  platform_confidence: number;
  platform_identifier?: Record<string, string>;
  active: boolean;
  discovered_via: string;
  evidence: string | null;
  scan_status: string;
  error: string | null;
  jobs_found: number;
  relevant_jobs_found: number;
  last_scanned_at: string | null;
  last_success_at: string | null;
  last_complete_scan_at: string | null;
  created_at: string;
}

/** Returned by POST /companies/{id}/sources; scan_run_id is set when a scan was queued. */
export interface CareerSourceCreated extends CareerSourceRead {
  scan_run_id: string | null;
}

export interface CompanyRelationship {
  id: string;
  source_company_id: string;
  source_company_name: string;
  target_company_id: string;
  target_company_name: string;
  relationship_type: string;
  confidence: number;
  evidence: string;
  evidence_url: string | null;
}

export interface CompanyDetail extends CompanyRead {
  sources: CareerSourceRead[];
  events: EventRead[];
  relationships: CompanyRelationship[];
}

export interface EventRead {
  id: string;
  company_id: string | null;
  company_name: string;
  source: string;
  source_url: string | null;
  reason: string;
  evidence: string;
  evidence_data?: Record<string, unknown>;
  confidence: number;
  discovery_score: number;
  status: "PENDING" | "ACCEPTED" | "IGNORED";
  observation_count: number;
  discovered_at: string;
  last_seen_at: string;
}

export interface ExplorationBudgets {
  max_new_companies: number;
  max_search_queries: number;
  max_pages: number;
  max_results_per_query: number;
  max_company_expansion: number;
  max_depth: number;
}

export interface ProviderStatus {
  name: string;
  enabled: boolean;
  requires_key: boolean;
  detail: string;
}

export interface RegistryEntry { name: string; aliases: string[]; career_urls: string[] }
export interface TailoringRule { mode: "all_relevant" | "min_fit" | "priority" | "min_priority_score" | "off"; threshold?: number | null; classes?: string[] }

export interface DiscoverySettings {
  tiers: string[];
  default_tier?: string;
  freshness_hours: number;
  secondary_freshness_hours: number;
  max_job_age_hours?: number;
  provider_status: ProviderStatus[];
  providers: Record<string, boolean>;
  budgets: ExplorationBudgets;
  company_score_weights: Record<string, number>;
  company_priority_weights: Record<string, number>;
  scan_interval_hours: number;
  exploration_interval_hours: number;
  reconciliation_interval_hours: number;
  frontier_interval_hours: number;
  scheduler_enabled: boolean;
  s_tier_companies?: string[];
  company_registry?: RegistryEntry[];
  tailoring_policy?: Record<string, TailoringRule>;
  tailoring_require_location_fit?: boolean;
  max_variants_per_run?: number;
  llm_rewrite_enabled?: boolean;
  llm_status?: { configured: boolean; provider: string | null; model: string | null; enabled: boolean; detail: string };
  target_score_weights?: Record<string, number>;
  fit_weights?: Record<string, number>;
  application_priority_weights?: Record<string, number>;
  application_priority_thresholds?: Record<string, number>;
  tier_values?: Record<string, number>;
}

/** Fields accepted by PATCH /discovery/settings (all optional server-side). */
export type DiscoverySettingsPatch = Partial<Omit<DiscoverySettings, "provider_status">>;

export interface RunRead {
  id: string;
  kind: string;
  status: string;
  trigger?: string;
  params?: Record<string, unknown>;
  attempts?: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  companies_discovered: number;
  jobs_inspected: number;
  jobs_created: number;
  error: string | null;
  errors: string[];
  budgets: Partial<ExplorationBudgets>;
  progress: Record<string, unknown>;
}

export interface FrontierRead {
  id: string;
  company_id: string;
  company_name: string;
  reason: string;
  priority: number;
  priority_breakdown?: Record<string, number>;
  status: string;
  depth: number;
  error?: string | null;
  created_at: string;
}

export interface DiscoverySummary {
  companies_total: number;
  companies_discovered_today: number;
  high_confidence: number;
  medium_confidence: number;
  low_confidence: number;
  frontier_pending: number;
  active_runs: number;
  fresh_data_jobs: number;
  pending_reviews: number;
}
