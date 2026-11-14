// What the Files tab says about a check, held to what the stored run says
// (CI, `web (dashboard)`).
//
// lib/file-checks.ts turns a stored run's rows into the lines a dealer
// reads. These are the statements it must never get wrong: a batch that
// was not deposited is said to be, with its amount and date; "every batch
// was deposited" is said only when every batch was paired; and a result
// this page does not know is never shown as paid. The module is compiled
// here from its source with the TypeScript the dashboard already has.
//
//   node scripts/check-file-checks.mjs
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const out = mkdtempSync(join(tmpdir(), "file-checks-"));
for (const name of ["money", "file-checks"]) {
  const source = readFileSync(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  writeFileSync(join(out, `${name}.js`), js.outputText);
}
const { batchLines, dayOf, FILE_CHECKS, verdict } = require(join(out, "file-checks.js"));

const row = (status, over = {}) => ({
  status, discrepancy: null, explained_by: null, reason: null, candidates: null, members: null,
  left_ref: null, right_ref: null, evidence: null, ...over,
});
const summary = (over = {}) => ({
  matched: 0, matched_with_explained_discrepancy: 0, matched_with_unexplained_discrepancy: 0, unmatched_left: 0,
  unmatched_right: 0, ambiguous: 0, unverifiable_left: 0, unverifiable_right: 0,
  coverage: { left: { records: 4 }, right: { records: 2 } }, ...over,
});
const checks = [];
const check = (name, fn) => checks.push([name, fn]);

check("a batch that was not deposited is said to be, with its amount and the day it closed", () => {
  const { attention, paid } = batchLines([row("unmatched_left", {
    left_ref: "B-2004", evidence: { record: { amount_minor: 123827, currency: "USD", when: "2026-11-02" } } })]);
  assert.equal(paid.length, 0);
  assert.deepEqual(
    { label: attention[0].label, tone: attention[0].tone, title: attention[0].title, amount: attention[0].amount, detail: attention[0].detail },
    { label: "Not deposited", tone: "danger", title: "Batch B-2004", amount: "$1,238.27",
      detail: "Closed 2 Nov 2026. No deposit on the statement pays it." });
});

check("a deposit with no batch is named by its day and amount, and by its reference only when the bank gave one", () => {
  const record = { amount_minor: 31240, currency: "USD", when: "2026-10-16" };
  const [made] = batchLines([row("unmatched_right", { right_ref: "h_adf7e75f342097535391", evidence: { record } })]).attention;
  assert.equal(made.title, "Deposit on 16 Oct 2026", "an id the importer made up is not shown");
  const [given] = batchLines([row("unmatched_right", { right_ref: "20261016001", evidence: { record } })]).attention;
  assert.equal(given.title, "Deposit on 16 Oct 2026 (20261016001)");
  assert.equal(given.amount, "$312.40");
  assert.equal(given.label, "No batch");
});

check("a deposit that paid several batches lists each one with its day and amount", () => {
  const { attention, paid } = batchLines([
    row("matched", { left_ref: "B-2001", right_ref: "h_9539fd812360df91eb71", discrepancy: 0, evidence: {
      compared: { left_minor: 178664, right_minor: 178664, currency: "USD" }, dated: { left: "2026-10-30", right: "2026-11-02" } } }),
    row("matched", { left_ref: "B-2002+B-2003", right_ref: null, members: 2, discrepancy: 0, evidence: {
      compared: { left_minor: 129192, right_minor: 129192, currency: "USD" }, dated: { left: null, right: "2026-11-03" },
      members: [{ ref: "B-2002", amount_minor: 89381, when: "2026-10-31" }, { ref: "B-2003", amount_minor: 39811, when: "2026-11-01" }] } }),
  ]);
  assert.equal(attention.length, 0);
  assert.deepEqual([paid[0].title, paid[0].amount, paid[0].detail, paid[0].parts],
    ["Deposit on 2 Nov 2026", "$1,786.64", "Paid batch B-2001, closed 30 Oct 2026.", []]);
  assert.deepEqual([paid[1].title, paid[1].amount, paid[1].detail], ["Deposit on 3 Nov 2026", "$1,291.92", "Paid 2 batches:"]);
  assert.deepEqual(paid[1].parts, [
    { ref: "B-2002", amount: "$893.81", when: "31 Oct 2026" }, { ref: "B-2003", amount: "$398.11", when: "1 Nov 2026" }]);
});

check("a deposit of a different amount says both figures and how far apart they are", () => {
  const [line] = batchLines([row("matched_discrepant", { left_ref: "B-1", right_ref: "D-1", discrepancy: 50, evidence: {
    compared: { left_minor: 350, right_minor: 300, currency: "USD" }, dated: { left: "2026-11-02", right: "2026-11-03" } } })]).attention;
  assert.deepEqual([line.label, line.tone, line.detail],
    ["Amounts differ", "danger", "Batch B-1 total $3.50; the deposit is $3.00, $0.50 apart."]);
});

check("a result this page does not know is never shown as paid", () => {
  const { attention, paid } = batchLines([row("settled_some_new_way", { left_ref: "B-9" })]);
  assert.equal(paid.length, 0);
  assert.equal(attention[0].label, "Not read");
});

check("the headline says a batch was not deposited before anything else", () => {
  const said = verdict(summary({ matched: 2, unmatched_left: 1, unmatched_right: 1 }));
  assert.deepEqual([said.headline, said.tone], ["1 batch was not deposited", "danger"]);
  assert.deepEqual(said.counts, ["2 deposits matched to their batches", "1 batch not deposited", "1 deposit with no batch"]);
});

check("every batch was deposited is said only when every batch was paired", () => {
  assert.deepEqual(verdict(summary({ matched: 5 })).headline, "Every batch was deposited");
  assert.equal(verdict(summary({ matched: 5 })).tone, "ok");
  assert.equal(verdict(summary({ matched: 5, unmatched_right: 1 })).headline, "Every batch was deposited; 1 deposit has no batch");
  for (const unsure of [{ unverifiable_left: 1 }, { ambiguous: 1 }, { unverifiable_right: 2 }]) {
    const said = verdict(summary({ matched: 5, ...unsure }));
    assert.ok(!said.headline.includes("Every batch was deposited"), `${JSON.stringify(unsure)}: ${said.headline}`);
    assert.equal(said.tone, "warn");
  }
  assert.equal(verdict(summary({ matched: 5, unverifiable_left: 1, unmatched_right: 1 })).headline,
    "No missing deposit found; 2 lines need a look");
});

check("files with nothing in the days the check reads are said to hold nothing, not to be clean", () => {
  const said = verdict(summary({ coverage: { left: { records: 0 }, right: { records: 0 } } }));
  assert.deepEqual([said.headline, said.tone], ["The files hold nothing in the days this check reads", "neutral"]);
});

check("a day reads as a day, an instant as its day in UTC, and anything else as nothing", () => {
  assert.equal(dayOf("2026-10-06"), "6 Oct 2026");
  assert.equal(dayOf("2026-10-13T18:03:22+00:00"), "13 Oct 2026");
  assert.equal(dayOf("next Tuesday"), "");
  assert.equal(dayOf(null), "");
});

check("the route can run only the checks this list names", () => {
  assert.deepEqual(FILE_CHECKS.map((c) => c.name), ["batches_deposits"]);
  const handler = readFileSync(new URL("../app/api/merchants/[id]/checks/[name]/route.ts", import.meta.url), "utf8");
  assert.match(handler, /FILE_CHECKS\.find\(\(c\) => c\.name === name\)/);
  assert.match(handler, /reconciliations\/\$\{encodeURIComponent\(check\.name\)\}/, "the name sent on is the list's own, not the request's");
});

let failed = 0;
for (const [name, fn] of checks) {
  try {
    fn();
    console.log(`ok    ${name}`);
  } catch (e) {
    failed += 1;
    console.log(`FAIL  ${name}\n      ${String(e.message).split("\n").join("\n      ")}`);
  }
}
console.log(`\n${checks.length - failed} of ${checks.length} hold`);
process.exit(failed ? 1 : 0);
