// What the Files tab says about a check, held to what the stored run says
// (CI, `web (dashboard)`).
//
// lib/file-checks.ts turns a stored run's rows into the lines a dealer
// reads. These are the statements it must never get wrong: a batch that
// was not deposited is said to be, with its amount and date; "every batch
// was deposited" is said only when every batch read was paired and every
// batch was read; only a clean match is shown as paid; and a word from a
// merchant's file is never inside a sentence the page writes. The module
// is compiled here from its source with the TypeScript the dashboard
// already has.
//
// An attacker's pass broke the page six ways this script did not notice.
// Each is now a check that fails, and the headline is also held over every
// combination of counts rather than the handful someone thought of.
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
const {
  asWritten, batchLines, dayOf, drawn, FILE_CHECKS, instant, LINES_SHOWN, notRead, paidClean, reach, REF_SHOWN, resultOf, shownOf, verdict,
} = require(join(out, "file-checks.js"));

const STATUSES = ["matched", "matched_explained", "matched_discrepant", "unmatched_left", "unmatched_right",
  "ambiguous", "unverifiable_left", "unverifiable_right"];
const row = (status, over = {}) => ({
  status, discrepancy: null, explained_by: null, reason: null, candidates: null, members: null,
  left_ref: null, right_ref: null, evidence: null, ...over,
});
const summary = (over = {}) => ({
  matched: 0, matched_with_explained_discrepancy: 0, matched_with_unexplained_discrepancy: 0, unmatched_left: 0,
  unmatched_right: 0, ambiguous: 0, unverifiable_left: 0, unverifiable_right: 0,
  coverage: { left: side(4), right: side(2) }, ...over,
});
// What one side read: the 120 days to 14 Nov 2026, under its cap.
const side = (records, over = {}) => ({
  records, cap: 5000, truncated: false, span_start: "2026-07-17T00:00:00+00:00",
  span_end: "2026-11-14T23:59:59.999999+00:00", ...over,
});
const checks = [];
const check = (name, fn) => checks.push([name, fn]);
const EVERY = /Every batch .*was deposited/;

check("a batch that was not deposited is said to be, with its amount and the day it closed", () => {
  const { attention, paid } = batchLines([row("unmatched_left", {
    left_ref: "B-2004", evidence: { record: { amount_minor: 123827, currency: "USD", when: "2026-11-02" } } })]);
  assert.equal(paid.length, 0);
  assert.deepEqual(
    { label: attention[0].label, tone: attention[0].tone, title: attention[0].title, refs: attention[0].refs,
      amount: attention[0].amount, detail: attention[0].detail },
    { label: "Not deposited", tone: "danger", title: "Batch", refs: ["B-2004"], amount: "$1,238.27",
      detail: "Closed 2 Nov 2026. No deposit on the statement pays it." });
});

check("a deposit with no batch is named by its day and amount, and by its reference only when the bank gave one", () => {
  const record = { amount_minor: 31240, currency: "USD", when: "2026-10-16" };
  const [made] = batchLines([row("unmatched_right", { right_ref: "h_adf7e75f342097535391", evidence: { record } })]).attention;
  assert.deepEqual([made.title, made.refs], ["Deposit on 16 Oct 2026", []], "an id the importer made up is not shown");
  const [given] = batchLines([row("unmatched_right", { right_ref: "20261016001", evidence: { record } })]).attention;
  assert.deepEqual([given.title, given.refs], ["Deposit on 16 Oct 2026", ["20261016001"]]);
  assert.equal(given.amount, "$312.40");
  assert.equal(given.label, "No batch");
});

check("a deposit lists each batch it paid with its day and amount, one batch or several", () => {
  const { attention, paid } = batchLines([
    row("matched", { left_ref: "B-2001", right_ref: "h_9539fd812360df91eb71", discrepancy: 0, evidence: {
      compared: { left_minor: 178664, right_minor: 178664, currency: "USD" }, dated: { left: "2026-10-30", right: "2026-11-02" } } }),
    row("matched", { left_ref: "B-2002+B-2003", right_ref: null, members: 2, discrepancy: 0, evidence: {
      compared: { left_minor: 129192, right_minor: 129192, currency: "USD" }, dated: { left: null, right: "2026-11-03" },
      members: [{ ref: "B-2002", amount_minor: 89381, when: "2026-10-31" }, { ref: "B-2003", amount_minor: 39811, when: "2026-11-01" }] } }),
  ]);
  assert.equal(attention.length, 0);
  assert.deepEqual([paid[0].title, paid[0].refs, paid[0].amount, paid[0].detail],
    ["Deposit on 2 Nov 2026", [], "$1,786.64", "Paid 1 batch:"]);
  assert.deepEqual(paid[0].parts, [{ ref: "B-2001", amount: "$1,786.64", when: "30 Oct 2026" }]);
  assert.deepEqual([paid[1].title, paid[1].amount, paid[1].detail], ["Deposit on 3 Nov 2026", "$1,291.92", "Paid 2 batches:"]);
  assert.deepEqual(paid[1].parts, [
    { ref: "B-2002", amount: "$893.81", when: "31 Oct 2026" }, { ref: "B-2003", amount: "$398.11", when: "1 Nov 2026" }]);
});

check("a deposit of a different amount says both figures and how far apart they are", () => {
  const [line] = batchLines([row("matched_discrepant", { left_ref: "B-1", right_ref: "D-1", discrepancy: 50, evidence: {
    compared: { left_minor: 350, right_minor: 300, currency: "USD" }, dated: { left: "2026-11-02", right: "2026-11-03" } } })]).attention;
  assert.deepEqual([line.label, line.tone, line.title, line.refs, line.detail],
    ["Amounts differ", "danger", "Deposit on 3 Nov 2026", ["D-1"], "Its batch totals $3.50; the deposit is $3.00, $0.50 apart."]);
  assert.deepEqual(line.parts, [{ ref: "B-1", amount: "$3.50", when: "2 Nov 2026" }]);
  const [explained] = batchLines([row("matched_explained", { left_ref: "B-1", right_ref: "D-1", discrepancy: 50, explained_by: "fee",
    evidence: { compared: { left_minor: 350, right_minor: 300, currency: "USD" } } })]).attention;
  assert.deepEqual([explained.label, explained.tone], ["Explained", "neutral"]);
  assert.match(explained.detail, /Accounted for by fee\.$/);
});

check("only a clean match is shown as paid: every other result, known or not, is under what needs a look", () => {
  // Attacker's pass: with an ambiguous or an unverifiable row listed under
  // "Paid", this script still passed. Every status is now held to its list
  // and its label.
  const said = {
    matched_explained: "Explained", matched_discrepant: "Amounts differ", unmatched_left: "Not deposited",
    unmatched_right: "No batch", ambiguous: "More than one fit", unverifiable_left: "Could not be checked",
    unverifiable_right: "Could not be checked", settled_some_new_way: "Not read", "": "Not read",
  };
  const shapes = [
    { left_ref: "B-9", right_ref: null }, { left_ref: null, right_ref: "D-9" }, { left_ref: "B-9", right_ref: "D-9" },
    { left_ref: "B-9", right_ref: "D-9", evidence: { record: { amount_minor: 100, currency: "USD", when: "2026-11-03" },
      compared: { left_minor: 100, right_minor: 100, currency: "USD" }, dated: { left: "2026-11-02", right: "2026-11-03" } } },
  ];
  for (const [status, label] of Object.entries(said)) {
    for (const shape of shapes) {
      const { attention, paid } = batchLines([row(status, shape)]);
      assert.equal(paid.length, 0, `${status || "(empty)"} is listed as paid`);
      assert.deepEqual([attention.length, attention[0].label], [1, label], status);
      assert.notEqual(attention[0].tone, "ok", `${status} is drawn in the colour of a clean result`);
    }
  }
  const { attention, paid } = batchLines([row("matched", shapes[3])]);
  assert.deepEqual([attention.length, paid.length, paid[0].label, paid[0].tone], [0, 1, "Paid", "ok"]);
  assert.deepEqual(Object.keys(said).filter((k) => STATUSES.includes(k)).length, STATUSES.length - 1, "every status but matched is held");
});

check("the headline says a batch was not deposited before anything else", () => {
  const said = verdict(summary({ matched: 2, unmatched_left: 1, unmatched_right: 1 }));
  assert.deepEqual([said.headline, said.tone], ["1 batch was not deposited", "danger"]);
  assert.deepEqual(said.counts, ["2 deposits matched to their batches", "1 batch not deposited", "1 deposit with no batch"]);
});

check("a deposit of a different amount is the headline, and is not counted among the clean matches", () => {
  // Attacker's pass: with this left out of the headline, five matched and
  // one that differs read "Every batch was deposited", and this script passed.
  const s = summary({ matched: 6, matched_with_unexplained_discrepancy: 1 });
  assert.deepEqual([verdict(s).headline, verdict(s).tone], ["1 deposit does not match the batches behind it", "danger"]);
  assert.deepEqual(verdict(s).counts, ["5 deposits matched to their batches", "1 deposit of a different amount"]);
  const two = summary({ matched: 6, matched_with_unexplained_discrepancy: 2, matched_with_explained_discrepancy: 1 });
  assert.equal(verdict(two).headline, "2 deposits do not match the batches behind it");
  assert.deepEqual(verdict(two).counts,
    ["3 deposits matched to their batches", "2 deposits of a different amount", "1 difference accounted for"]);
  assert.equal(paidClean(two), 3);
});

check("every batch was deposited is said only when every batch read was paired", () => {
  assert.deepEqual(verdict(summary({ matched: 5 })).headline, "Every batch since 17 Jul 2026 was deposited");
  assert.equal(verdict(summary({ matched: 5 })).tone, "ok");
  assert.equal(verdict(summary({ matched: 5, unmatched_right: 1 })).headline,
    "Every batch since 17 Jul 2026 was deposited; 1 deposit has no batch");
  for (const unsure of [{ unverifiable_left: 1 }, { ambiguous: 1 }, { unverifiable_right: 2 }]) {
    const said = verdict(summary({ matched: 5, ...unsure }));
    assert.ok(!EVERY.test(said.headline), `${JSON.stringify(unsure)}: ${said.headline}`);
    assert.equal(said.tone, "warn");
  }
  assert.equal(verdict(summary({ matched: 5, unverifiable_left: 1, unmatched_right: 1 })).headline,
    "No missing deposit found; 2 lines need a look");
});

check("whatever the counts, the clean line and its green are said only of a run that earned them", () => {
  // Every combination, not the few above: each count at 0, 1 and 3, with
  // each thing a run can say about what it read. A change to the headline
  // that lets any of them through fails here.
  const reads = [
    ["read", { left: side(4), right: side(2) }], ["nothing said", null],
    ["no batch read", { left: side(0), right: side(2) }], ["no deposit read", { left: side(4), right: side(0) }],
    ["batches cut", { left: side(5000, { truncated: true }), right: side(2) }],
    ["deposits cut", { left: side(4), right: side(5000, { truncated: true }) }],
  ];
  const n = [0, 1, 3];
  let seen = 0;
  let clean = 0;
  for (const [how, coverage] of reads) for (const matched of n) for (const differs of n) for (const explained of n)
    for (const unmatched_left of n) for (const unmatched_right of n) for (const ambiguous of n)
      for (const unverifiable_left of n) for (const unverifiable_right of n) {
        const s = summary({ matched: matched + differs + explained, matched_with_unexplained_discrepancy: differs,
          matched_with_explained_discrepancy: explained, unmatched_left, unmatched_right, ambiguous, unverifiable_left,
          unverifiable_right, coverage });
        const said = verdict(s);
        const earned = unmatched_left === 0 && differs === 0 && ambiguous === 0 && unverifiable_left === 0
          && unverifiable_right === 0 && matched + explained > 0 && how !== "batches cut" && how !== "deposits cut"
          && how !== "no batch read" && how !== "no deposit read";
        const at = `${how}: ${JSON.stringify({ ...s, coverage: undefined })} -> "${said.headline}" (${said.tone})`;
        assert.equal(EVERY.test(said.headline), earned, at);
        assert.equal(said.tone === "ok", earned && unmatched_right === 0, at);
        // And with something on file the run did not read, never, whatever the counts.
        const short = verdict(s, true);
        assert.ok(!EVERY.test(short.headline) && short.tone !== "ok", `not everything read, ${at} -> "${short.headline}"`);
        assert.equal(short.tone === "danger", said.tone === "danger", at);
        // A batch with no deposit is the alarm, unless no deposit was read at all:
        // said by the run, or, where it says nothing, by there being none in any row.
        const deposits = how === "no deposit read" ? 0
          : how === "nothing said" ? matched + differs + explained + unmatched_right + unverifiable_right + ambiguous : 2;
        if (unmatched_left > 0) assert.equal(said.tone, deposits > 0 ? "danger" : "warn", at);
        seen += 1;
        clean += said.tone === "ok" ? 1 : 0;
      }
  assert.equal(seen, 6 * 3 ** 8);
  assert.ok(clean > 0 && clean < seen / 100, `${clean} of ${seen} are clean`);
});

check("every batch means every batch the run read: the days it read are said, never every batch there is", () => {
  // Attacker's pass: a batch older than the days the check reads is in the
  // file and in no row, and the page said "Every batch was deposited".
  const s = summary({ matched: 2 });
  assert.ok(!verdict(s).headline.startsWith("Every batch was deposited"));
  assert.equal(reach(s).read, "The check read 4 batches and 2 deposits dated 17 Jul 2026 to 14 Nov 2026. "
    + "Anything in the files outside those days is not in this result.");
  assert.equal(reach(s).cut, "");
  // With nothing said about what was read, the line still claims no more than the run.
  assert.equal(verdict(summary({ matched: 2, coverage: null })).headline, "Every batch the check read was deposited");
  // And a file that starts before the first day read is said to hold lines that were not checked.
  const file = (kind, over = {}) => ({ kind, created_at: "2026-11-10T09:00:00+00:00", first_date: "2026-08-01", last_date: "2026-11-10", skipped: 0, ...over });
  const run = { started_at: "2026-11-14T12:53:00+00:00" };
  const span = reach(s);
  assert.deepEqual([span.from, span.to], ["2026-07-17", "2026-11-14"]);
  assert.deepEqual(notRead([file("settlement_batches", { first_date: "2026-07-07" }), file("bank_deposits", { first_date: "2026-06-30" })], run, span), {
    notes: ["The batch report on file goes back to 7 Jul 2026. Batches before 17 Jul 2026 were not checked.",
      "The bank statement on file goes back to 30 Jun 2026. Deposits before 17 Jul 2026 were not checked."],
    missed: false });
  assert.deepEqual(notRead([file("settlement_batches", { first_date: "2026-07-17" }), file("bank_deposits", { first_date: null })], run, span),
    { notes: [], missed: false }, "files inside the days read need no such line");
});

check("what is on file that the run did not read is said, and takes the clean line away", () => {
  // Second attacker's pass. Each of these sat under "Every batch … was
  // deposited", in green, with an unpaid batch on file: a batch report
  // imported while the check ran (the page compared the file's time with
  // the run's end, not its start); a batch in a row the importer skipped;
  // a batch dated after the days the check reads.
  const file = (kind, over = {}) => ({ kind, created_at: "2026-11-10T09:00:00+00:00", first_date: "2026-08-01", last_date: "2026-11-10", skipped: 0, ...over });
  const s = summary({ matched: 3 });
  const span = reach(s);
  const run = { started_at: "2026-11-14T12:53:00.349+00:00", finished_at: "2026-11-14T12:53:01.553+00:00" };
  const both = [file("settlement_batches"), file("bank_deposits")];
  assert.deepEqual(notRead(both, run, span), { notes: [], missed: false });

  const during = notRead([...both, file("settlement_batches", { created_at: "2026-11-14T12:53:00.507+00:00" })], run, span);
  assert.deepEqual(during, { notes: ["A file was imported after this check began. Run it again to include it."], missed: true });
  assert.equal(notRead([...both, file("bank_deposits", { created_at: "2026-11-15T08:00:00+00:00" })], run, span).missed, true);
  assert.equal(notRead([...both, file("bank_deposits", { created_at: "not a time" })], run, span).missed, true, "a time that cannot be read is not known to be before the run");
  assert.equal(notRead([file("settlement_batches", { created_at: run.started_at })], run, span).missed, false);

  assert.deepEqual(notRead([file("settlement_batches", { skipped: 1 }), file("bank_deposits")], run, span), {
    notes: ["1 row of the batch report on file could not be read when it was imported, and is in no line here."], missed: true });
  assert.deepEqual(notRead([file("settlement_batches", { skipped: 2 }), file("settlement_batches", { skipped: 3 }), file("bank_deposits", { skipped: 4 })], run, span), {
    notes: ["5 rows of the batch report on file could not be read when it was imported, and are in no line here.",
      "4 rows of the bank statement on file could not be read when it was imported, and are in no line here."], missed: true });
  // A skipped line of the statement can only be a deposit: a batch is not left unseen by it.
  assert.equal(notRead([file("settlement_batches"), file("bank_deposits", { skipped: 4 })], run, span).missed, false);

  assert.deepEqual(notRead([file("settlement_batches", { last_date: "2026-11-19" }), file("bank_deposits", { last_date: "2026-11-16" })], run, span), {
    notes: ["The batch report on file runs to 19 Nov 2026. Batches after 14 Nov 2026 were not checked.",
      "The bank statement on file runs to 16 Nov 2026. Deposits after 14 Nov 2026 were not checked."], missed: true });
  assert.equal(notRead([file("settlement_batches", { last_date: "2026-11-14" })], run, span).missed, false);

  // The clean line is not said then, and an unpaid batch is still the headline.
  assert.deepEqual([verdict(s).headline, verdict(s).tone], ["Every batch since 17 Jul 2026 was deposited", "ok"]);
  assert.deepEqual([verdict(s, true).headline, verdict(s, true).tone],
    ["No missing deposit among what was read, and not everything was read", "warn"]);
  assert.equal(verdict(summary({ matched: 3, unmatched_right: 1 }), true).headline, "No missing deposit among what was read, and not everything was read");
  assert.equal(verdict(summary({ matched: 3, unmatched_left: 1 }), true).headline, "1 batch was not deposited");
});

check("the result card is given every word: what was read, what was not, and the notes under the lines", () => {
  // Second attacker's pass: the line for a run that stopped at its cap, the
  // lines for what is on file and was not read, the footnotes and the
  // colours of the headline could each be taken out of the page with every
  // check green. They are written here, where this check reads them, and
  // the card's part is held below to drawing each one.
  const file = (kind, over = {}) => ({ kind, created_at: "2026-11-10T09:00:00+00:00", first_date: "2026-08-01", last_date: "2026-11-04", skipped: 0, ...over });
  const run = (over = {}) => ({ id: "run_1", status: "ok", started_at: "2026-11-14T12:53:00+00:00", finished_at: "2026-11-14T12:53:01+00:00",
    error: null, rows_total: 3, rows: [
      row("unmatched_left", { left_ref: "B-2004", evidence: { record: { amount_minor: 123827, currency: "USD", when: "2026-11-02" } } }),
      row("matched", { left_ref: "B-2001", right_ref: "D-1", evidence: { compared: { left_minor: 178664, right_minor: 178664, currency: "USD" }, dated: { left: "2026-10-30", right: "2026-11-02" } } }),
      row("matched", { left_ref: "B-2002", right_ref: "D-2", evidence: { compared: { left_minor: 1, right_minor: 1, currency: "USD" } } }),
    ], ...over });
  const s = summary({ matched: 2, unmatched_left: 1 });
  const got = resultOf(run(), s, [file("settlement_batches", { first_date: "2026-07-07" }), file("bank_deposits")]);
  assert.deepEqual({ ...got, attention: got.attention.length, paid: got.paid.length }, {
    headline: "1 batch was not deposited", tone: "danger",
    counts: "2 deposits matched to their batches · 1 batch not deposited",
    checked: "Checked 14 Nov 2026, 12:53 UTC", cut: "",
    notes: ["The batch report on file goes back to 7 Jul 2026. Batches before 17 Jul 2026 were not checked."],
    read: "The check read 4 batches and 2 deposits dated 17 Jul 2026 to 14 Nov 2026. Anything in the files outside those days is not in this result.",
    attention: 1, paid: 2, paidCount: "2 deposits matched to their batches", paidShown: "",
    footnotes: ["The newest statement on file runs to 4 Nov 2026. A batch closed in the last few days before that may simply not be deposited yet."],
  });
  // A run that stopped at its cap, with more lines than the page draws.
  const cut = summary({ matched: 5000, coverage: { left: side(5000, { truncated: true }), right: side(5000) } });
  const long = resultOf(run({ rows_total: 5000, rows: Array.from({ length: 1000 }, (_, n) => run().rows[1 + (n % 2)]) }), cut, [file("settlement_batches"), file("bank_deposits")]);
  assert.deepEqual([long.headline, long.tone, long.cut, long.paidCount, long.paidShown, long.footnotes, long.paid.length], [
    "No missing deposit among what was read, and not everything was read", "warn",
    "It stopped at 5,000 batches, oldest first: the files hold more in those days, and what was not read is in no line here.",
    "5,000 deposits matched to their batches", " · the first 100 listed",
    ["Showing 100 of 5,000 lines, the ones that need a look first. The API and MCP return the stored result."], 100]);
  // A clean run with a file imported after it began is not clean on the page.
  const clean = summary({ matched: 2 });
  const stale = resultOf(run({ rows: run().rows.slice(1), rows_total: 2 }), clean, [file("settlement_batches"), file("bank_deposits", { created_at: "2026-11-14T12:53:00.500+00:00" })]);
  assert.deepEqual([stale.headline, stale.tone, stale.notes], ["No missing deposit among what was read, and not everything was read", "warn",
    ["A file was imported after this check began. Run it again to include it."]]);
  const fresh = resultOf(run({ rows: run().rows.slice(1), rows_total: 2 }), clean, [file("settlement_batches"), file("bank_deposits")]);
  assert.deepEqual([fresh.headline, fresh.tone, fresh.notes, fresh.footnotes, fresh.paidCount], ["Every batch since 17 Jul 2026 was deposited", "ok", [], [], "2 deposits matched to their batches"]);
  assert.equal(instant("2026-11-14T23:59:59+00:00"), "14 Nov 2026, 23:59 UTC");
  assert.equal(instant("soon"), "");
});

check("a run that stopped at its cap never says every batch was deposited", () => {
  // Attacker's pass: with more than 5,000 batches in the days read, the
  // rest were in no row and the page was green.
  const cutLeft = summary({ matched: 5000, coverage: { left: side(5000, { truncated: true }), right: side(5000) } });
  const said = verdict(cutLeft);
  assert.deepEqual([said.headline, said.tone],
    ["No missing deposit among what was read, and not everything was read", "warn"]);
  assert.equal(reach(cutLeft).cut, "It stopped at 5,000 batches, oldest first: the files hold more in those days, "
    + "and what was not read is in no line here.");
  const cutRight = summary({ matched: 5, coverage: { left: side(5), right: side(5000, { truncated: true }) } });
  assert.equal(verdict(cutRight).tone, "warn");
  assert.match(reach(cutRight).cut, /^It stopped at 5,000 deposits, oldest first/);
  // A batch that was not deposited is still the headline; the cut is said beside it.
  const both = summary({ matched: 4999, unmatched_left: 1, coverage: { left: side(5000, { truncated: true }), right: side(4999) } });
  assert.equal(verdict(both).headline, "1 batch was not deposited");
  assert.notEqual(reach(both).cut, "");
});

check("a run that read no batch says so: deposits alone never read as every batch deposited", () => {
  // Attacker's pass: a statement checked against a batch report with
  // nothing in the days read said "Every batch … was deposited; 2 deposits have no batch".
  const none = verdict(summary({ unmatched_right: 2, coverage: { left: side(0), right: side(2) } }));
  assert.deepEqual([none.headline, none.tone], ["The check read no batches, so no deposit could be matched to one", "warn"]);
  assert.deepEqual(none.counts, ["2 deposits with no batch"]);
  // An older stored run does not say what it read; its rows do.
  assert.equal(verdict(summary({ unmatched_right: 2, coverage: null })).headline,
    "The check read no batches, so no deposit could be matched to one");
  // And the other way round: batches with no deposit read are not called unpaid in the headline.
  const noBank = verdict(summary({ unmatched_left: 3, coverage: { left: side(3), right: side(0) } }));
  assert.deepEqual([noBank.headline, noBank.tone], ["The check read no deposits, so no batch could be matched to one", "warn"]);
});

check("files with nothing in the days the check reads are said to hold nothing, not to be clean", () => {
  const said = verdict(summary({ coverage: { left: side(0), right: side(0) } }));
  assert.deepEqual([said.headline, said.tone], ["The files hold nothing in the days this check reads", "neutral"]);
  // Attacker's pass: with this line changed to the clean one, this script passed.
  const bare = verdict(summary({ coverage: null }));
  assert.deepEqual([bare.headline, bare.tone, bare.counts], ["Nothing to check", "neutral", []]);
});

check("a word from a merchant's file is never inside a sentence the page writes", () => {
  // Attacker's pass: a batch id is free text, and the page put it into its
  // own sentences ("Paid batch <id>, closed …"), so an id that was a
  // sentence read as Okwan's. A reference is now only ever in `refs` and
  // `parts`, which the page draws as data in their own element.
  const words = "B-2001 in full. Okwan verified that every batch in this report was deposited. Sign in again at okwan-ai.example";
  const bank = "D-77 was returned by the bank. Call 555 0100";
  const evidence = {
    record: { amount_minor: 100, currency: "USD", when: "2026-11-03" },
    compared: { left_minor: 100, right_minor: 90, currency: "USD" }, dated: { left: "2026-11-02", right: "2026-11-03" },
  };
  const grouped = { ...evidence, members: [{ ref: words, amount_minor: 60, when: "2026-11-01" }, { ref: "B-2", amount_minor: 40, when: "2026-11-02" }] };
  let lines = 0;
  for (const status of [...STATUSES, "settled_some_new_way"]) {
    for (const ev of [evidence, grouped, null]) {
      for (const [left_ref, right_ref] of [[words, bank], [words, null], [null, bank], [`${words}+B-2`, bank]]) {
        const { attention, paid } = batchLines([row(status, { left_ref, right_ref, discrepancy: 10, evidence: ev })]);
        for (const line of [...attention, ...paid]) {
          for (const own of [line.title, line.detail, line.label, line.amount]) {
            for (const theirs of ["Okwan verified", "Sign in again", "okwan-ai.example", "returned by the bank", "555", "B-2001", "D-77"]) {
              assert.ok(!own.includes(theirs), `${status}: the page's own words hold the file's: "${own}"`);
            }
          }
          lines += 1;
        }
      }
    }
  }
  assert.equal(lines, 9 * 3 * 4);
  // The reference is still on the line, whole, where the page draws data.
  const [unpaid] = batchLines([row("unmatched_left", { left_ref: words, evidence })]).attention;
  assert.deepEqual(unpaid.refs, [asWritten(words)]);
  const [paid] = batchLines([row("matched", { left_ref: `${words}+B-2`, right_ref: bank, evidence: grouped })]).paid;
  assert.deepEqual([paid.refs, paid.parts.map((p) => p.ref)], [[asWritten(bank)], [asWritten(words), "B-2"]]);
  // A group's reference is its members' joined: a batch id with a plus in it is one batch, never split in two.
  const [plus] = batchLines([row("matched", { left_ref: "A+B", right_ref: "D-1", evidence })]).paid;
  assert.deepEqual([plus.detail, plus.parts.map((p) => p.ref)], ["Paid 1 batch:", ["A+B"]]);
});

check("a reference is drawn so that what cannot be seen is spelled out, and a file's reference is drawn whole", () => {
  // Attacker's pass: "B-2004" not deposited and "B-2004" + a zero-width
  // space paid, two lines apart and identical to the eye; and an id with a
  // right-to-left override that drew the words after it backwards.
  const at = (code) => String.fromCodePoint(code);
  const spelled = (code) => `[U+${code.toString(16).toUpperCase().padStart(4, "0")}]`;
  assert.equal(asWritten("B-2004"), "B-2004");
  assert.equal(asWritten(`B-2004${at(0x200b)}`), "B-2004[U+200B]");
  assert.equal(asWritten(`B-7${at(0x202e)}diap ton saw`), "B-7[U+202E]diap ton saw");
  const unseen = [0x0, 0x7, 0xa, 0xd, 0x9, 0x7f, 0x85, 0xa0, 0xad, 0x61c, 0x180e, 0x2000, 0x200b, 0x200c, 0x200d, 0x200e, 0x200f,
    0x2028, 0x2029, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x202f, 0x2060, 0x2066, 0x2067, 0x2068, 0x2069, 0x3000, 0xfeff, 0xfff9,
    0xe0001, 0xe0041, 0xe000, 0xd800,
    // Second attacker's pass: thirteen more that drew as nothing, or as a
    // blank, and were not spelled out. Nine of them left "B-2004" looking
    // exactly like "B-2004". Variation selectors, the combining grapheme
    // joiner, the Hangul and Khmer fillers, and the blank Braille pattern.
    0xfe00, 0x34f, 0x115f, 0x3164, 0x17b4, 0x17b5, 0x180b, 0xe0100, 0xe01ef, 0xfe0f, 0x1160, 0xffa0, 0x2800];
  assert.equal(new Set(unseen).size, 50);
  for (const code of unseen) {
    assert.equal(asWritten(`A${at(code)}B`), `A${spelled(code)}B`, `${spelled(code)} is drawn as the file has it`);
    assert.notEqual(asWritten(`B-2004${at(code)}`), asWritten("B-2004"));
  }
  for (const kept of ["Batch 12", "Lot #4/A_b-c.d", "Café-Ünïcode-批次-٣", "A+B", "\u{1f4b3}7"]) assert.equal(asWritten(kept), kept);
  // The importer keeps at most 200 characters of a reference, and all of them are drawn: cut at 64, two ids alike
  // to that point, one paid and one not, were drawn as the same text (second attacker's pass).
  assert.equal(REF_SHOWN, 200);
  const alike = "LOT-2026-11-" + "0".repeat(52);
  assert.equal(alike.length, 64);
  assert.notEqual(asWritten(`${alike}A`), asWritten(`${alike}B`));
  assert.equal(asWritten("X".repeat(200)), "X".repeat(200));
  assert.equal(asWritten("X".repeat(201)), `${"X".repeat(200)}… (201 characters)`);
  assert.ok(asWritten(at(0x202e).repeat(200)).length <= 200 * 8, "a reference of nothing but hidden characters is bounded");
  // Every reference on a line has been through it.
  const [line] = batchLines([row("matched", { left_ref: `a${at(0x200b)}+b`, right_ref: `d${at(0x202e)}`, evidence: {
    compared: { left_minor: 2, right_minor: 2, currency: "USD" },
    members: [{ ref: `a${at(0x200b)}`, amount_minor: 1, when: null }, { ref: null, amount_minor: 1, when: null }] } })]).paid;
  assert.deepEqual([line.refs, line.parts.map((p) => p.ref)], [["d[U+202E]"], ["a[U+200B]", "(no batch id)"]]);
  // And the card draws each in its own element, in its own direction, and nowhere else.
  const card = readFileSync(new URL("../app/_components/check-result.tsx", import.meta.url), "utf8");
  assert.match(card, /<bdi dir="ltr"[^>]*data-file-ref/);
  assert.equal(card.match(/\{line\.refs\.map\(/g)?.length, 1);
  assert.equal(card.match(/<Ref value=\{(?:ref|p\.ref)\} \/>/g)?.length, 2);
  assert.ok(!/\{p\.ref\}|\{ref\}/.test(card.replace(/<Ref value=\{(?:ref|p\.ref)\} \/>/g, "")), "a reference is drawn outside its element");
});

check("the page draws the first lines of a long run and counts the rest from the run's summary", () => {
  // Attacker's pass: a stored run of 1,000 lines with 200-character ids was
  // a 4.8 MB page that failed to hydrate about one load in four; and beside
  // "5,000 deposits matched" the page counted the 1,000 rows it was handed.
  const paid = (n) => row("matched", { left_ref: `B-${n}`, right_ref: `D-${n}`, evidence: { compared: { left_minor: 1, right_minor: 1, currency: "USD" } } });
  const unpaid = (n) => row("unmatched_left", { left_ref: `U-${n}` });
  const rows = [...Array.from({ length: 300 }, (_, n) => unpaid(n)), ...Array.from({ length: 700 }, (_, n) => paid(n))];
  const s = summary({ matched: 5000, unmatched_left: 300 });
  const got = drawn({ rows, rows_total: 5300 }, s);
  assert.equal(LINES_SHOWN, 100);
  assert.deepEqual([got.attention.length, got.paid.length, got.shown, got.total, got.clean], [100, 100, 200, 5300, 5000]);
  assert.deepEqual([got.attention[0].refs, got.attention[99].refs, got.paid[0].parts[0].ref], [["U-0"], ["U-99"], "B-0"],
    "the first of each list, in the order the run stored them");
  const small = drawn({ rows: [unpaid(1), paid(1), paid(2)], rows_total: 3 }, summary({ matched: 2, unmatched_left: 1 }));
  assert.deepEqual([small.attention.length, small.paid.length, small.shown, small.total, small.clean], [1, 2, 3, 3, 2]);
  assert.deepEqual(drawn({ rows_total: null }, summary()), { attention: [], paid: [], clean: 0, shown: 0, total: 0 });
});

check("the tab is handed its words by the server and drawn from data, and works nothing out", () => {
  const read = (path) => readFileSync(new URL(`../app/${path}`, import.meta.url), "utf8");
  const [page, panel, section, card] = [read("merchants/[id]/page.tsx"), read("_components/files-panel.tsx"),
    read("_components/file-check.tsx"), read("_components/check-result.tsx")];
  // What the tab shows of a check is decided on the server, by the library this check holds.
  const run = { id: "run_1", status: "ok", started_at: "2026-11-14T12:53:00+00:00", finished_at: "2026-11-14T12:53:01+00:00",
    error: null, rows_total: 0, rows: [], summary: summary() };
  assert.deepEqual(shownOf(null, []), { state: "never" });
  assert.deepEqual(shownOf({ ...run, status: "failed", summary: null, error: "stripe answered 401" }, []),
    { state: "failed", error: "stripe answered 401", at: "14 Nov 2026, 12:53 UTC" });
  assert.deepEqual(shownOf({ ...run, summary: null }, []), { state: "failed", error: "No reason was recorded.", at: "14 Nov 2026, 12:53 UTC" });
  const ok = shownOf(run, []);
  assert.deepEqual([ok.state, ok.result.headline, ok.result], ["ok", "Nothing to check", resultOf(run, run.summary, [])]);
  assert.match(page, /check=\{shownOf\(check, files\.ok \? files\.data\.imports : \[\]\)\}/);

  // The tab is a client component drawn from data, as the Findings and Connections tabs are. A page reaches the
  // browser in pieces. Drawn on the server, parts of this tab were sent in later pieces than the elements that
  // hold them; on a slow link the browser took such an element over before its inside had arrived, took it again
  // when it had, and React threw the page away and drew it again. First attacker's pass: a long result, one load
  // in four. Second: any result on a slow link, one load in seven. Handed its data, nothing in the tab can arrive
  // after what holds it (the layout check loads it through a slowed document to hold that in a browser).
  assert.ok(panel.startsWith('"use client";'), "the Files tab is drawn on the server again");
  assert.ok(card.startsWith('"use client";'));
  assert.ok(!/Suspense|server-only|await /.test(panel + section + card), "something in the tab waits on the server");
  assert.match(panel, /\{checkable && <FileCheck tenantId=\{tenantId\} files=\{files\} shown=\{check\} \/>\}/);
  // ... and none of it works a result out: the only things taken from the library are its list of checks and the drawing of a name.
  const taken = (source) => [...source.matchAll(/^import (.*) from "@\/lib\/file-checks";$/gm)].map((m) => m[1]);
  assert.deepEqual(taken(panel), ["{ asWritten, type Shown }"]);
  assert.deepEqual(taken(section), ["{ BATCHES_DEPOSITS, FILE_CHECKS, type Shown }"]);
  assert.deepEqual(taken(card), ["type { CheckLine, Result, Tone }"]);
  assert.deepEqual(card.match(/^import .*$/gm).slice(1), ['import { Badge } from "./ui/badge";', 'import { Card } from "./ui/card";']);
  assert.ok(!/toLocale|new Date|Date\.|Math\.|\.slice\(|\.filter\(|\.sort\(/.test(card), "the card works something out itself");
  assert.ok(!/resultOf|shownOf|verdict\(|notRead\(|drawn\(|batchLines|reach\(|instant\(|PairRun|\.summary\b/.test(panel + section + card),
    "the tab writes a word of the result itself");
  assert.ok(!/<li\b|<ul\b|<details\b|<bdi\b/.test(section), "a line is drawn outside the card");
  for (const drawn of ["<CheckResult result={shown.result} />", "{shown.error}", "{shown.at}",
    'again={shown.state !== "never"}']) assert.ok(section.includes(drawn), `the section no longer draws ${drawn}`);

  // Second attacker's pass: each of these could be taken out of the page with
  // every check green. The words are held above; here, that the card draws them.
  assert.ok(card.includes('const HEADLINE: Record<Tone, string> = { danger: "text-danger", warn: "text-ink", ok: "text-ok", neutral: "text-ink-soft" };'),
    "the headline's colours changed: an unpaid batch is drawn in the colour of a fault, and only a clean result in green");
  for (const drawn of [
    "<p className={`text-lg font-bold ${HEADLINE[r.tone]}`}>{r.headline}</p>",
    '{r.counts && <p className="mt-1 text-sm text-ink-soft">{r.counts}</p>}',
    '<p className="mt-1 text-xs text-ink-soft">{r.checked}</p>',
    '{r.cut && <p className="mt-2 text-sm font-semibold text-danger">{r.cut}</p>}',
    '{r.notes.map((note) => <p key={note} className="mt-2 text-sm font-semibold">{note}</p>)}',
    '{r.read && <p className="mt-1 text-xs text-ink-soft">{r.read}</p>}',
    "{r.attention.map((l) => <Line key={l.key} line={l} />)}",
    "{r.paid.map((l) => <Line key={l.key} line={l} />)}",
    "{r.paidCount}",
    '{r.paidShown && <span className="font-normal text-ink-soft">{r.paidShown}</span>}',
    "{r.footnotes.map((note) => <p key={note}>{note}</p>)}",
    "<Badge tone={line.tone} symbol={line.symbol}>{line.label}</Badge>",
    "{line.title}", "{line.amount}", "{line.detail}",
  ]) assert.ok(card.includes(drawn), `the card no longer draws ${drawn}`);

  // And a name a merchant chose (a file's, a mapping's) is drawn in an element and a direction of its own.
  assert.match(panel, /return <bdi dir="ltr" data-merchant-name>\{asWritten\(value\)\}<\/bdi>;/);
  assert.ok(!/>\{f\.filename\}<|>\{f\.layout\}<|>\{l\.name\}<|mapping \$\{l\.name\}|what=\{f\.filename\}|title=\{f\.filename\}/.test(panel),
    "a merchant's name is drawn as the page's own text");
  assert.equal(panel.match(/<Named value=\{(?:f\.filename|f\.layout|l\.name)\} \/>/g)?.length, 3);
});

check("a day reads as a day, an instant as its day in UTC, and anything else as nothing", () => {
  assert.equal(dayOf("2026-10-06"), "6 Oct 2026");
  assert.equal(dayOf("2026-10-13T18:03:22+00:00"), "13 Oct 2026");
  assert.equal(dayOf("next Tuesday"), "");
  assert.equal(dayOf(null), "");
});

check("the route runs only the checks this list names, and answers with the stored run's id and nothing of the result", () => {
  // Attacker's pass: `find(…) ?? { name }` ran any name, and `{ ...r.data }`
  // sent the whole result to the browser; this script passed on both. The
  // layout check holds the same two in a browser, against a running API.
  assert.deepEqual(FILE_CHECKS.map((c) => c.name), ["batches_deposits"]);
  const handler = readFileSync(new URL("../app/api/merchants/[id]/checks/[name]/route.ts", import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\/|^\s*\/\/.*$/gm, "");
  const lines = handler.split("\n").map((l) => l.trim()).filter(Boolean);
  const found = lines.indexOf("const check = FILE_CHECKS.find((c) => c.name === name);");
  assert.ok(found > 0, "the check is looked up in the list, and nothing stands in for one that is not there");
  assert.equal(lines[found + 1], 'if (!check) return fail("no such check", 404);');
  assert.equal(handler.match(/(?<![.\w])name\b/g).length, 3,
    "the request's name is declared, taken from the path and compared with the list, and used nowhere else");
  assert.match(handler, /reconciliations\/\$\{encodeURIComponent\(check\.name\)\}\?limit=1`/, "the name sent on is the list's own");
  assert.ok(lines.includes("return r.ok ? json({ run_id: r.data.run_id, at: Date.now() }) : fail(r.detail, r.status);"));
  assert.equal(handler.match(/json\(/g).length, 1, "one answer on success");
  assert.ok(!/\.\.\.|r\.data(?!\.run_id)/.test(handler), "nothing else of the API's answer goes to the browser");
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
