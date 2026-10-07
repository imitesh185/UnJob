const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const ts = require("typescript");

function loadTypeScript(file) {
  const source = fs.readFileSync(path.join(__dirname, "..", file), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports = {};
  vm.runInNewContext(compiled, { exports });
  return exports;
}

const intel = loadTypeScript("lib\\intel.ts");

test("status labels keep acronyms readable", () => {
  assert.equal(intel.statusLabel("OA"), "OA");
  assert.equal(intel.statusLabel("RECRUITER_SCREEN"), "Recruiter screen");
  assert.equal(intel.statusLabel("READY_TO_APPLY"), "Ready to apply");
  assert.equal(intel.statusLabel(null), "UNKNOWN");
});

test("years ranges are shown as ranges, open minimums with a plus", () => {
  assert.equal(intel.yearsText(1, 2), "1–2 years");
  assert.equal(intel.yearsText(5, null), "5+ years");
  assert.equal(intel.yearsText(null, null), "");
});

test("tier labels read naturally", () => {
  assert.equal(intel.tierLabel("S"), "S-tier");
  assert.equal(intel.tierLabel("UNCATEGORIZED"), "Uncategorized");
  assert.equal(intel.tierLabel(null), "Tier unknown");
});

test("undated postings are never labelled fresh", () => {
  assert.equal(intel.freshnessText("unknown", 3), "Posting date UNKNOWN");
  assert.equal(intel.freshnessText("fresh", null), "Posting date UNKNOWN");
  assert.equal(intel.freshnessText("fresh", 6), "Fresh · ≤6h old");
  assert.equal(intel.freshnessText("recent", 52), "Recent · ≤2d old");
});

test("every priority class has a distinct badge tone and missing scores stay unknown", () => {
  const tones = ["P0", "P1", "P2", "P3", "REJECT"].map((value) => intel.priorityTone(value));
  assert.equal(new Set(tones).size, tones.length);
  assert.equal(intel.score(null), "—");
  assert.equal(intel.score(81.6), "82");
});
