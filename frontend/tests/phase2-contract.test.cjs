const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

function loadTypeScript(file, extra = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", file), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports, URL, DOMException, process: { env: { NEXT_PUBLIC_API_URL: "http://localhost:8000/" } }, ...extra });
  return exports;
}

const discovery = loadTypeScript("lib\\discovery.ts");

test("job sources use actual source records and ATS fallback, not legacy source", () => {
  const record = (source, external_id) => ({ source, external_id, source_url: `https://example.com/${external_id}`, career_source_id: null, last_seen_at: null, listing_status: "OPEN" });
  assert.equal(discovery.jobSources({ sources: [record("greenhouse", "1"), record("greenhouse", "2"), record("lever", "3")], ats: "unknown", source: "invented" }).join(","), "greenhouse,lever");
  assert.equal(discovery.jobSources({ sources: [], ats: "ashby", source: "invented" }).join(","), "ashby");
  assert.equal(discovery.jobSources({ source: "invented" }).length, 0);
});

test("missing or invalid timestamps remain unknown", () => {
  assert.equal(discovery.dateLabel(null), "UNKNOWN");
  assert.equal(discovery.dateLabel("not-a-date"), "UNKNOWN");
  assert.equal(discovery.postedLabel(null, "date"), "UNKNOWN");
});

test("date-only postings are not given an invented time of day", () => {
  const label = discovery.postedLabel("2026-10-07T00:00:00Z", "date");
  assert.match(label, /\(date only\)$/);
  assert.doesNotMatch(label, /:\d\d/);
  assert.match(discovery.postedLabel("2026-10-07T09:30:00", "local_datetime"), /source time zone unknown/);
});

test("freshness labels never treat undated jobs as fresh", () => {
  assert.equal(discovery.freshnessLabel({ posted_at: null, freshness_status: "fresh" }), "Posting date UNKNOWN — not fresh");
  assert.match(discovery.freshnessLabel({ posted_at: "2026-10-07T00:00:00Z", freshness_status: "fresh" }), /^Fresh/);
  assert.equal(discovery.freshnessLabel({ posted_at: "2026-10-07T00:00:00Z", freshness_status: "odd" }), "Freshness UNKNOWN");
});

test("budgets follow server bounds: zero disables work, maxima are enforced", () => {
  const draft = { max_new_companies: "0", max_search_queries: "0", max_pages: "5000", max_results_per_query: "50", max_company_expansion: "1", max_depth: "5" };
  assert.deepEqual({ ...discovery.validatedBudgets(draft) }, { max_new_companies: 0, max_search_queries: 0, max_pages: 5000, max_results_per_query: 50, max_company_expansion: 1, max_depth: 5 });
  assert.throws(() => discovery.validatedBudgets({ ...draft, max_depth: "6" }), /Depth must be a whole number from 0 to 5/);
  assert.throws(() => discovery.validatedBudgets({ ...draft, max_pages: "-1" }), /Pages/);
  assert.throws(() => discovery.validatedBudgets({ ...draft, max_pages: "1.5" }), /Pages/);
  assert.throws(() => discovery.validatedBudgets({ ...draft, max_pages: " " }), /Pages/);
});

test("links reject non-web protocols", () => {
  assert.equal(discovery.safeUrl("javascript:alert(1)"), undefined);
  assert.equal(discovery.safeUrl("file:///C:/private"), undefined);
  assert.equal(discovery.safeUrl("https://example.com/careers"), "https://example.com/careers");
});

test("queued and partial runs are never reported as successful completion", () => {
  assert.equal(discovery.runTerminal({ status: "QUEUED" }), false);
  assert.equal(discovery.runSucceeded({ status: "QUEUED" }), false);
  assert.equal(discovery.runTerminal({ status: "FAILED" }), true);
  assert.equal(discovery.runSucceeded({ status: "PARTIAL", errors: [] }), false);
  assert.equal(discovery.runSucceeded({ status: "COMPLETED", errors: ["Source blocked"] }), false);
  assert.equal(discovery.runSucceeded({ status: "COMPLETED", errors: [], error: null }), true);
  assert.equal(discovery.runOutcome({ status: "QUEUED", errors: [] }), "Queued — not yet started");
  assert.equal(discovery.runOutcome({ status: "RUNNING", errors: [] }), "Running — not yet complete");
  assert.equal(discovery.runOutcome({ status: "PARTIAL", errors: ["SOURCE_BLOCKED duckduckgo"] }), "Finished with errors — results are partial");
  assert.equal(discovery.runOutcome({ status: "FAILED", errors: [] }), "Failed");
});

test("FastAPI validation arrays produce readable field errors", async () => {
  const api = loadTypeScript("lib\\api.ts", { fetch: async () => ({ ok: false, status: 422, json: async () => ({ detail: [{ loc: ["body", "budgets", "max_pages"], msg: "Must be greater than zero" }] }) }) });
  await assert.rejects(api.apiRequest("/api/v1/discovery/runs"), (error) => error.message === "budgets.max_pages: Must be greater than zero" && error.status === 422);
});

test("HTTP 202 returns queued response without marking it completed", async () => {
  const run = { id: "test-run", kind: "greenhouse", status: "QUEUED" };
  const api = loadTypeScript("lib\\api.ts", { fetch: async (url) => {
    assert.equal(url, "http://localhost:8000/api/v1/ingestion/greenhouse");
    return { ok: true, status: 202, json: async () => run };
  } });
  assert.equal((await api.apiRequest("/api/v1/ingestion/greenhouse", { method: "POST" })).status, "QUEUED");
});

test("unreachable API reports failure instead of success", async () => {
  const api = loadTypeScript("lib\\api.ts", { fetch: async () => { throw new Error("network"); } });
  await assert.rejects(api.apiRequest("/api/v1/companies"), /Could not reach the API/);
});
