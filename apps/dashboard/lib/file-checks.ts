/**
 * Checks that read a merchant's imported files, as the Files tab shows
 * them: which files each needs, and a stored run turned into lines a
 * person can act on.
 *
 * Everything shown comes from the run the API stored: its summary and its
 * trimmed rows (a status, each side's reference, the amounts compared, a
 * date, and for a group the members it summed). No file is read here and
 * nothing is recomputed: a line says what the row says. The words for a
 * status are the dashboard's own.
 */
import type { ImportFile, Kind } from "./imports";
import { formatMinor } from "./money";

export const BATCHES_DEPOSITS = "batches_deposits";

/** The file-based checks the Files tab can run, and the kinds of file each reads. */
export const FILE_CHECKS: readonly { name: string; title: string; needs: readonly Kind[] }[] = [
  { name: BATCHES_DEPOSITS, title: "Batches against deposits", needs: ["settlement_batches", "bank_deposits"] },
];

export type Figures = { amount_minor: number | null; currency: string | null; when: string | null };

/** One row of a stored two-sided run (okwan_recon `trimmed_rows`). */
export type PairRow = {
  status:
    | "matched" | "matched_explained" | "matched_discrepant" | "unmatched_left" | "unmatched_right"
    | "ambiguous" | "unverifiable_left" | "unverifiable_right";
  discrepancy: number | null;
  explained_by: string | null;
  reason: string | null;
  candidates: number | null;
  members: number | null;
  left_ref: string | null;
  right_ref: string | null;
  evidence: {
    /** A record with no pair: the amount, currency and date it was read by. */
    record?: Figures;
    compared?: { left_minor: number; right_minor: number; currency: string | null };
    members?: { ref: string | null; amount_minor: number | null; when?: string | null }[];
    dated?: { left: string | null; right: string | null };
    groupings?: number[];
  } | null;
};

export type PairSummary = {
  matched: number;
  matched_with_explained_discrepancy: number;
  matched_with_unexplained_discrepancy: number;
  unmatched_left: number;
  unmatched_right: number;
  ambiguous: number;
  unverifiable_left: number;
  unverifiable_right: number;
  coverage: { left: Coverage | null; right: Coverage | null } | null;
};

/** What one side of a run read: how many records, between which days, and
 *  whether it stopped at its cap before the end. */
export type Coverage = {
  records: number;
  cap?: number | null;
  truncated?: boolean;
  span_start?: string | null;
  span_end?: string | null;
};

export type PairRun = {
  id: string;
  status: "ok" | "failed";
  /** When the run began to read. A file imported after this is not in it. */
  started_at: string;
  finished_at: string;
  summary: PairSummary | null;
  error: string | null;
  rows_total: number | null;
  rows?: PairRow[];
  has_more?: boolean;
};

export type Tone = "danger" | "warn" | "ok" | "neutral";

/**
 * One row as the page draws it. `title` and `detail` are the page's own
 * words and hold nothing a file said; what a file said (a batch id, a
 * statement reference) is in `refs` and `parts`, drawn apart as data.
 */
export type CheckLine = {
  key: string;
  tone: Tone;
  symbol: string;
  label: string;
  /** What it is, in the page's words: "Batch", "Deposit on 6 Oct 2026". */
  title: string;
  /** The file's own references for it, each already through `asWritten`. */
  refs: string[];
  amount: string;
  detail: string;
  /** The batches behind a deposit: each one's reference (through `asWritten`), amount and day. */
  parts: { ref: string; amount: string; when: string }[];
};

/** How much of a reference is drawn. The importer keeps at most 200
 *  characters of one, so a file's reference is drawn whole: cut short, two
 *  ids alike to that point would be drawn as one. */
export const REF_SHOWN = 200;

// A character a reader cannot see, or that changes the text around it:
// controls, format characters (zero-width, bidi overrides and isolates,
// soft hyphen), everything Unicode says to ignore when drawing (variation
// selectors, the combining grapheme joiner, the Hangul and Khmer fillers),
// unassigned and private-use code points, lone surrogates, line and
// paragraph separators, every space that is not the plain one, and the
// blank Braille pattern, which is a letter that draws as a space.
const UNSEEN = /^(?:[\p{Cc}\p{Cf}\p{Co}\p{Cn}\p{Cs}\p{Zl}\p{Zp}\p{Default_Ignorable_Code_Point}\u2800]|(?! )\p{Zs})$/u;
/** One character that cannot be seen, as merchants' and people's own text is checked for it. */
export const UNSEEN_CHAR = UNSEEN;

/**
 * A reference from a file, made safe to read beside the page's own words.
 * The file's characters are kept, except that one a reader cannot see is
 * spelled out ("B-2004[U+200B]"), so two ids that differ never look the
 * same and no id redraws the text around it. One longer than a file can
 * hold is cut, with its length said. The page draws the result in its own
 * element, never inside a sentence.
 */
export function asWritten(ref: string): string {
  const chars = Array.from(ref);
  const spelled = chars.slice(0, REF_SHOWN)
    .map((c) => (UNSEEN.test(c) ? `[U+${c.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}]` : c))
    .join("");
  return chars.length > REF_SHOWN ? `${spelled}… (${chars.length.toLocaleString("en-US")} characters)` : spelled;
}

/** "6 Oct 2026" from a day ("2026-10-06") or an instant; "" when there is none. */
export function dayOf(when: string | null | undefined): string {
  if (!when) return "";
  const at = new Date(/^\d{4}-\d{2}-\d{2}$/.test(when) ? `${when}T00:00:00Z` : when);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

/** A statement line with no reference column gets an id the importer
 *  makes up ("h_…"): nothing a person would recognise, so it is not shown. */
function shownRef(ref: string | null): string[] {
  return ref && !/^h_[0-9a-f]{20}$/.test(ref) ? [asWritten(ref)] : [];
}

function deposit(when: string | null | undefined): string {
  const day = dayOf(when);
  return `Deposit${day ? ` on ${day}` : ""}`;
}

function closed(when: string | null | undefined): string {
  const day = dayOf(when);
  return day ? `Closed ${day}. ` : "";
}

/**
 * The rows of a batches-against-deposits run as lines: what needs a look
 * first, in the order the run stored them (worst first), then what was
 * paid. Batches are the left side, deposits the right.
 *
 * A batch id and a statement reference are free text from a merchant's
 * file. Neither is ever put inside a sentence here: a sentence on this
 * page is the page's own, start to end.
 */
export function batchLines(rows: PairRow[]): { attention: CheckLine[]; paid: CheckLine[] } {
  const attention: CheckLine[] = [];
  const paid: CheckLine[] = [];
  rows.forEach((row, n) => {
    const ev = row.evidence ?? {};
    const rec = ev.record;
    const cur = ev.compared?.currency ?? rec?.currency ?? null;
    const key = `${n}:${row.status}`;
    // The batches behind the row: a group's members, or the one batch. A
    // group's reference is its members' joined, and is never split back.
    const members = ev.members ?? [];
    const behind = members.length > 0
      ? members.map((m) => ({ ref: m.ref === null ? "(no batch id)" : asWritten(m.ref), amount: formatMinor(m.amount_minor, cur), when: dayOf(m.when) }))
      : row.left_ref !== null && ev.compared
        ? [{ ref: asWritten(row.left_ref), amount: formatMinor(ev.compared.left_minor, cur), when: dayOf(ev.dated?.left) }]
        : [];
    const batch = { title: row.left_ref === null ? "A batch with no id" : "Batch", refs: row.left_ref === null ? [] : [asWritten(row.left_ref)] };
    const line = (tone: Tone, symbol: string, label: string, what: { title: string; refs: string[] }, amount: string,
      detail: string, parts: CheckLine["parts"] = []): CheckLine => ({ key, tone, symbol, label, ...what, amount, detail, parts });
    const paidBy = (when: string | null | undefined) => ({ title: deposit(when), refs: shownRef(row.right_ref) });
    const batches = behind.length === 1 ? "1 batch" : `${behind.length} batches`;

    switch (row.status) {
      case "unmatched_left":
        attention.push(line("danger", "✕", "Not deposited", batch, formatMinor(rec?.amount_minor, cur),
          `${closed(rec?.when)}No deposit on the statement pays it.`));
        break;
      case "unmatched_right":
        attention.push(line("warn", "?", "No batch", paidBy(rec?.when), formatMinor(rec?.amount_minor, cur),
          "Money in that no batch, and no group of batches, adds up to."));
        break;
      case "ambiguous":
        attention.push(line("warn", "?", "More than one fit",
          row.right_ref !== null || row.left_ref === null ? paidBy(rec?.when) : batch, formatMinor(rec?.amount_minor, cur),
          row.reason ?? "More than one grouping of batches adds up to it. Okwan does not pick one."));
        break;
      case "matched_discrepant":
      case "matched_explained": {
        const c = ev.compared;
        const apart = formatMinor(Math.abs(row.discrepancy ?? 0), cur);
        const said = c
          ? `${behind.length > 1 ? `Its ${batches} total` : "Its batch totals"} ${formatMinor(c.left_minor, cur)}; the deposit is ${formatMinor(c.right_minor, cur)}, ${apart} apart.`
          : `${apart} apart.`;
        attention.push(row.status === "matched_explained"
          ? line("neutral", "≈", "Explained", paidBy(ev.dated?.right), formatMinor(c?.right_minor, cur),
              `${said} Accounted for by ${row.explained_by ?? "a recorded adjustment"}.`, behind)
          : line("danger", "≠", "Amounts differ", paidBy(ev.dated?.right), formatMinor(c?.right_minor, cur), said, behind));
        break;
      }
      case "unverifiable_left":
      case "unverifiable_right":
        attention.push(line("neutral", "…", "Could not be checked",
          row.status === "unverifiable_left" ? batch : paidBy(rec?.when), formatMinor(rec?.amount_minor, cur),
          row.reason ?? "The other file does not reach this date."));
        break;
      case "matched":
        paid.push(line("ok", "✓", "Paid", paidBy(ev.dated?.right), formatMinor(ev.compared?.right_minor, cur),
          behind.length > 0 ? `Paid ${batches}:` : "Paid one batch.", behind));
        break;
      default:
        // A status this page does not know is never shown as paid.
        attention.push(line("neutral", "…", "Not read", row.left_ref !== null ? batch : paidBy(rec?.when),
          formatMinor(rec?.amount_minor, cur), "This page does not know this result yet. The API and MCP return it in full."));
    }
  });
  return { attention, paid };
}

function count(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/**
 * What the run read, said beside every result: a check reads a fixed
 * number of days and at most so many records, so "every batch" means
 * every batch it read and nothing wider. `cut` when either side stopped
 * at its cap: records past it are in no row, so nothing is known of them.
 */
export function reach(s: PairSummary): { read: string; since: string; from: string; to: string; cut: string } {
  const left = s.coverage?.left ?? null;
  const right = s.coverage?.right ?? null;
  if (!left && !right) return { read: "", since: "", from: "", to: "", cut: "" };
  const from = (left?.span_start ?? right?.span_start ?? "").slice(0, 10);
  const to = (left?.span_end ?? right?.span_end ?? "").slice(0, 10);
  const since = dayOf(left?.span_start ?? right?.span_start);
  const until = dayOf(left?.span_end ?? right?.span_end);
  const dated = since && until ? ` dated ${since} to ${until}` : since ? ` dated from ${since}` : "";
  const read = `The check read ${count(left?.records ?? 0, "batch", "batches")} and `
    + `${count(right?.records ?? 0, "deposit", "deposits")}${dated}. Anything in the files outside those days is not in this result.`;
  const stopped = [left?.truncated ? "batches" : "", right?.truncated ? "deposits" : ""].filter(Boolean);
  const cap = (left?.truncated ? left.cap : right?.cap) ?? null;
  const cut = stopped.length
    ? `It stopped at ${cap ? cap.toLocaleString("en-US") : "its limit of"} ${stopped.join(" and ")}, oldest first: `
      + "the files hold more in those days, and what was not read is in no line here."
    : "";
  return { read, since, from, to, cut };
}

/** What the page needs to know of a file on record to say what a run did not read of it. */
export type OnFile = Pick<ImportFile, "kind" | "created_at" | "first_date" | "last_date" | "skipped">;

/**
 * What the files on record hold that the run did not read, each said in
 * words: a file imported after the run began, lines older or newer than
 * the days it read, and rows the importer could not read and skipped.
 * `missed` when any of it could be a batch the run never saw: then the
 * clean line is not said, whatever the run's own counts are.
 */
export function notRead(
  files: readonly OnFile[], run: { started_at: string }, read: { from: string; to: string },
): { notes: string[]; missed: boolean } {
  const notes: string[] = [];
  const began = Date.parse(run.started_at);
  // An unreadable time on either side is not known to be before the run.
  const later = files.some((f) => !(Date.parse(f.created_at) <= began));
  if (later) notes.push("A file was imported after this check began. Run it again to include it.");
  let missed = later;
  for (const [kind, what, lines] of [
    ["settlement_batches", "batch report", "Batches"], ["bank_deposits", "bank statement", "Deposits"],
  ] as const) {
    const mine = files.filter((f) => f.kind === kind);
    const first = mine.map((f) => f.first_date).filter((d): d is string => Boolean(d)).sort()[0];
    const last = mine.map((f) => f.last_date).filter((d): d is string => Boolean(d)).sort().at(-1);
    const skipped = mine.reduce((n, f) => n + (f.skipped > 0 ? f.skipped : 0), 0);
    if (first && read.from && first < read.from) {
      notes.push(`The ${what} on file goes back to ${dayOf(first)}. ${lines} before ${dayOf(read.from)} were not checked.`);
    }
    if (last && read.to && last > read.to) {
      notes.push(`The ${what} on file runs to ${dayOf(last)}. ${lines} after ${dayOf(read.to)} were not checked.`);
      missed ||= kind === "settlement_batches";
    }
    if (skipped) {
      notes.push(`${count(skipped, "row", "rows")} of the ${what} on file could not be read when it was imported, `
        + `and ${skipped === 1 ? "is" : "are"} in no line here.`);
      missed ||= kind === "settlement_batches";
    }
  }
  return { notes, missed };
}

/** Deposits whose batches add up to them exactly: a match that needs no look. */
export function paidClean(s: PairSummary): number {
  return Math.max(0, s.matched - s.matched_with_explained_discrepancy - s.matched_with_unexplained_discrepancy);
}

/** The run in one sentence, and whether anything in it needs a look. */
export function verdict(s: PairSummary, missed = false): { headline: string; tone: Tone; counts: string[] } {
  const left = s.coverage?.left ?? null;
  const right = s.coverage?.right ?? null;
  const { since, cut } = reach(s);
  // Every batch the run read, never every batch there is.
  const every = since ? `Every batch since ${since} was deposited` : "Every batch the check read was deposited";
  const total = s.matched + s.unmatched_left + s.unmatched_right + s.ambiguous + s.unverifiable_left + s.unverifiable_right;
  const clean = paidClean(s);
  const counts = [
    clean ? count(clean, "deposit matched to its batches", "deposits matched to their batches") : "",
    s.unmatched_left ? count(s.unmatched_left, "batch not deposited", "batches not deposited") : "",
    s.unmatched_right ? count(s.unmatched_right, "deposit with no batch", "deposits with no batch") : "",
    s.matched_with_unexplained_discrepancy ? count(s.matched_with_unexplained_discrepancy, "deposit of a different amount", "deposits of a different amount") : "",
    s.matched_with_explained_discrepancy ? count(s.matched_with_explained_discrepancy, "difference accounted for", "differences accounted for") : "",
    s.ambiguous ? count(s.ambiguous, "with more than one fit", "with more than one fit") : "",
    s.unverifiable_left + s.unverifiable_right
      ? count(s.unverifiable_left + s.unverifiable_right, "could not be checked", "could not be checked") : "",
  ].filter(Boolean);
  if (total === 0) {
    return {
      headline: s.coverage && (left?.records ?? 0) + (right?.records ?? 0) === 0
        ? "The files hold nothing in the days this check reads" : "Nothing to check",
      tone: "neutral", counts,
    };
  }
  // A side the run read nothing of. With no batch read, no batch was
  // deposited or not; with no deposit read, no batch could have been paired.
  // Where an older stored run does not say what it read, its rows do.
  // (A row that fits more than one way may be of either side.)
  const batches = left ? left.records : s.matched + s.unmatched_left + s.unverifiable_left + s.ambiguous;
  const deposits = right ? right.records : s.matched + s.unmatched_right + s.unverifiable_right + s.ambiguous;
  if (deposits === 0) {
    return { headline: "The check read no deposits, so no batch could be matched to one", tone: "warn", counts };
  }
  // A batch with no deposit comes before everything else that can be said.
  const breaks = s.unmatched_left + s.matched_with_unexplained_discrepancy;
  if (breaks) {
    return {
      headline: s.unmatched_left
        ? `${count(s.unmatched_left, "batch was", "batches were")} not deposited`
        : `${count(breaks, "deposit does", "deposits do")} not match the batches behind it`,
      tone: "danger", counts,
    };
  }
  if (batches === 0) {
    return { headline: "The check read no batches, so no deposit could be matched to one", tone: "warn", counts };
  }
  // "Every batch … was deposited" only when every batch read was paired: one
  // that could not be checked, or that fits more than one way, is not known to be.
  const unsure = s.ambiguous + s.unverifiable_left + s.unverifiable_right;
  if (unsure) {
    return {
      headline: `No missing deposit found; ${count(unsure + s.unmatched_right, "line needs", "lines need")} a look`,
      tone: "warn", counts,
    };
  }
  // A run that stopped at its cap did not read every batch, and neither did
  // one whose files hold a batch it never saw (`notRead`): never the clean line.
  if (cut || missed) return { headline: "No missing deposit among what was read, and not everything was read", tone: "warn", counts };
  // Nor is it said of a run in which no batch was paired with anything.
  if (s.matched === 0) return { headline: "No batch was matched to a deposit", tone: "warn", counts };
  if (s.unmatched_right) {
    return { headline: `${every}; ${count(s.unmatched_right, "deposit has", "deposits have")} no batch`, tone: "warn", counts };
  }
  return { headline: every, tone: "ok", counts };
}

/** How many lines of each list the page draws. A stored run holds up to
 *  1,000; the page draws the first of them and says how many it left out. */
export const LINES_SHOWN = 100;

/**
 * What the page draws of a run: the first lines of each list, and the
 * figures said beside them. `clean` and `total` come from the run's own
 * summary, never from the lines on hand, which are a cut of it.
 */
export function drawn(run: { rows?: PairRow[]; rows_total: number | null }, s: PairSummary):
  { attention: CheckLine[]; paid: CheckLine[]; clean: number; shown: number; total: number } {
  const lines = batchLines(run.rows ?? []);
  const attention = lines.attention.slice(0, LINES_SHOWN);
  const paid = lines.paid.slice(0, LINES_SHOWN);
  return {
    attention, paid, clean: paidClean(s), shown: attention.length + paid.length,
    total: Math.max(run.rows_total ?? 0, lines.attention.length + lines.paid.length),
  };
}

/** A stored run as the Files tab draws it: every word of the result card. */
export type Result = {
  headline: string;
  tone: Tone;
  counts: string;
  /** "Checked 14 Nov 2026, 12:53 UTC". */
  checked: string;
  /** Said in the colour of a fault: the run stopped before it had read everything. */
  cut: string;
  /** Said in bold: what is on file that the run did not read. */
  notes: string[];
  read: string;
  attention: CheckLine[];
  paid: CheckLine[];
  /** "5,000 deposits matched to their batches", from the run's summary. */
  paidCount: string;
  /** " · the first 100 listed", when the page draws fewer than that. */
  paidShown: string;
  footnotes: string[];
};

/** "14 Nov 2026, 12:53 UTC" from an instant; "" when it is not one. */
export function instant(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return `${dayOf(iso)}, ${time} UTC`;
}

/**
 * Everything the result card says, as words: the headline, what the run
 * read and did not, the first lines of each list, and the notes under
 * them. Written from the stored run and the files on record, on the
 * server; the card draws it and works nothing out.
 */
export function resultOf(run: PairRun, summary: PairSummary, files: readonly OnFile[]): Result {
  const read = reach(summary);
  const { notes, missed } = notRead(files, run, read);
  const said = verdict(summary, missed);
  const { attention, paid, clean, shown, total } = drawn(run, summary);
  const n = (some: number) => some.toLocaleString("en-US");
  const statementEnd = files.filter((f) => f.kind === "bank_deposits" && f.last_date).map((f) => f.last_date!).sort().at(-1);
  return {
    headline: said.headline,
    tone: said.tone,
    counts: said.counts.join(" · "),
    checked: `Checked ${instant(run.finished_at)}`,
    cut: read.cut,
    notes,
    read: read.read,
    attention,
    paid,
    paidCount: clean === 1 ? "1 deposit matched to its batches" : `${n(clean)} deposits matched to their batches`,
    paidShown: paid.length < clean ? ` · the first ${n(paid.length)} listed` : "",
    footnotes: [
      shown < total
        ? `Showing ${n(shown)} of ${n(total)} lines, the ones that need a look first. The API and MCP return the stored result.` : "",
      summary.unmatched_left > 0 && statementEnd
        ? `The newest statement on file runs to ${dayOf(statementEnd)}. A batch closed in the last few days before that may simply not be deposited yet.` : "",
    ].filter(Boolean),
  };
}

/** What the tab shows of a check: nothing yet, a run that did not finish, or a result in words. */
export type Shown =
  | { state: "never" }
  | { state: "failed"; error: string; at: string }
  | { state: "ok"; result: Result };

/**
 * The newest stored run of a check as the tab shows it. Called on the
 * server: the tab is handed this, and draws it. A run that is not "ok",
 * or has no summary, is one that did not finish.
 */
export function shownOf(run: PairRun | null, files: readonly OnFile[]): Shown {
  if (!run) return { state: "never" };
  if (run.status !== "ok" || !run.summary) {
    return { state: "failed", error: run.error ?? "No reason was recorded.", at: instant(run.finished_at) };
  }
  return { state: "ok", result: resultOf(run, run.summary, files) };
}
