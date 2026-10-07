import type { ExplorationBudgets, Job, PostedAtPrecision, RunRead } from "./types";

export const companyStatuses = ["DISCOVERED", "UNVERIFIED", "VERIFIED", "ACTIVE", "INACTIVE"];
export const budgetLabels = {
  max_new_companies: "New companies",
  max_search_queries: "Search queries",
  max_pages: "Pages",
  max_results_per_query: "Results per query",
  max_company_expansion: "Company expansions",
  max_depth: "Depth",
} as const;
/** Server-side maxima (backend ExplorationBudgets); 0 disables that kind of work for a run. */
export const budgetLimits: Record<keyof ExplorationBudgets, number> = {
  max_new_companies: 1000,
  max_search_queries: 200,
  max_pages: 5000,
  max_results_per_query: 50,
  max_company_expansion: 200,
  max_depth: 5,
};

export function validatedBudgets(draft: Record<keyof ExplorationBudgets, string>): ExplorationBudgets {
  const result = {} as ExplorationBudgets;
  for (const key of Object.keys(budgetLimits) as (keyof ExplorationBudgets)[]) {
    const raw = (draft[key] ?? "").trim();
    const value = Number(raw);
    if (!raw || !Number.isSafeInteger(value) || value < 0 || value > budgetLimits[key]) {
      throw new Error(`${budgetLabels[key]} must be a whole number from 0 to ${budgetLimits[key].toLocaleString()}.`);
    }
    result[key] = value;
  }
  return result;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "The request failed. Please retry.";
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function dateLabel(value: string | null | undefined): string {
  if (!value) return "UNKNOWN";
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : "UNKNOWN";
}

/** A posting date shown at the precision the source published; date-only values get no invented time. */
export function postedLabel(value: string | null | undefined, precision?: PostedAtPrecision | null): string {
  if (!value) return "UNKNOWN";
  if (precision === "date") {
    const day = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
    if (!day) return "UNKNOWN";
    const date = new Date(Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3])));
    return Number.isFinite(date.getTime()) ? `${date.toLocaleDateString(undefined, { timeZone: "UTC" })} (date only)` : "UNKNOWN";
  }
  if (precision === "local_datetime") {
    const date = new Date(value.replace(/(Z|[+-]\d{2}:?\d{2})$/, ""));
    return Number.isFinite(date.getTime()) ? `${date.toLocaleString()} (source time zone unknown)` : "UNKNOWN";
  }
  return dateLabel(value);
}

const freshnessText: Record<string, string> = {
  fresh: "Fresh — inside the primary window",
  recent: "Recent — secondary window",
  stale: "Older than the secondary window",
};

export function freshnessLabel(job: Pick<Job, "posted_at" | "freshness_status">): string {
  if (!job.posted_at) return "Posting date UNKNOWN — not fresh";
  return freshnessText[job.freshness_status ?? ""] ?? "Freshness UNKNOWN";
}

export function safeUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const parsed = new URL(value);
    return ["https:", "http:"].includes(parsed.protocol) ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}

export function jobSources(job: Job): string[] {
  const names = job.sources?.map((source) => source.source).filter((value): value is string => Boolean(value)) ?? [];
  return [...new Set(names.length ? names : job.ats ? [job.ats] : [])];
}

export function runTerminal(run: RunRead): boolean {
  return ["COMPLETED", "SUCCEEDED", "SUCCESS", "FAILED", "CANCELLED", "CANCELED", "PARTIAL", "PARTIAL_SUCCESS", "COMPLETED_WITH_ERRORS"].includes(run.status.toUpperCase());
}

export function runSucceeded(run: RunRead): boolean {
  return ["COMPLETED", "SUCCEEDED", "SUCCESS"].includes(run.status.toUpperCase()) && !run.error && !run.errors?.length;
}

/** Human-readable state; PARTIAL means work finished but some sources failed or were blocked. */
export function runOutcome(run: RunRead): string {
  const status = run.status.toUpperCase();
  if (!runTerminal(run)) return status === "RUNNING" ? "Running — not yet complete" : "Queued — not yet started";
  if (runSucceeded(run)) return "Completed";
  if (status === "FAILED") return "Failed";
  if (status === "CANCELLED" || status === "CANCELED") return "Cancelled";
  return "Finished with errors — results are partial";
}
