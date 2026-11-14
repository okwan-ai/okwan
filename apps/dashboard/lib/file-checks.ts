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
import type { Kind } from "./imports";
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
  finished_at: string;
  summary: PairSummary | null;
  error: string | null;
  rows_total: number | null;
  rows?: PairRow[];
  has_more?: boolean;
};

export type Tone = "danger" | "warn" | "ok" | "neutral";

/** One row as the page draws it. */
export type CheckLine = {
  key: string;
  tone: Tone;
  symbol: string;
  label: string;
  /** What it is: "Batch B-2004", "Deposit on 6 Oct 2026". */
  title: string;
  amount: string;
  detail: string;
  /** The batches a deposit paid, when it paid more than one. */
  parts: { ref: string; amount: string; when: string }[];
};

/** "6 Oct 2026" from a day ("2026-10-06") or an instant; "" when there is none. */
export function dayOf(when: string | null | undefined): string {
  if (!when) return "";
  const at = new Date(/^\d{4}-\d{2}-\d{2}$/.test(when) ? `${when}T00:00:00Z` : when);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

/** A statement line with no reference column gets an id the importer
 *  makes up ("h_…"): nothing a person would recognise, so it is not shown. */
function shownRef(ref: string | null): string {
  return ref && !/^h_[0-9a-f]{20}$/.test(ref) ? ref : "";
}

function deposit(when: string | null | undefined, ref: string | null): string {
  const day = dayOf(when);
  const named = shownRef(ref);
  return `Deposit${day ? ` on ${day}` : ""}${named ? ` (${named})` : ""}`;
}

function closed(when: string | null | undefined): string {
  const day = dayOf(when);
  return day ? `Closed ${day}. ` : "";
}

/**
 * The rows of a batches-against-deposits run as lines: what needs a look
 * first, in the order the run stored them (worst first), then what was
 * paid. Batches are the left side, deposits the right.
 */
export function batchLines(rows: PairRow[]): { attention: CheckLine[]; paid: CheckLine[] } {
  const attention: CheckLine[] = [];
  const paid: CheckLine[] = [];
  rows.forEach((row, n) => {
    const ev = row.evidence ?? {};
    const rec = ev.record;
    const cur = ev.compared?.currency ?? rec?.currency ?? null;
    const key = `${n}:${row.status}:${row.left_ref ?? ""}:${row.right_ref ?? ""}`;
    const parts = (ev.members ?? []).map((m) => ({
      ref: m.ref ?? "(no batch id)", amount: formatMinor(m.amount_minor, cur), when: dayOf(m.when),
    }));
    const refs = row.left_ref ? row.left_ref.split("+").join(", ") : "";
    const batches = refs ? `Batch${parts.length > 1 ? "es" : ""} ${refs}` : "A batch";
    const line = (tone: Tone, symbol: string, label: string, title: string, amount: string, detail: string): CheckLine =>
      ({ key, tone, symbol, label, title, amount, detail, parts: parts.length > 1 ? parts : [] });

    switch (row.status) {
      case "unmatched_left":
        attention.push(line("danger", "✕", "Not deposited", batches, formatMinor(rec?.amount_minor, cur),
          `${closed(rec?.when)}No deposit on the statement pays it.`));
        break;
      case "unmatched_right":
        attention.push(line("warn", "?", "No batch", deposit(rec?.when, row.right_ref), formatMinor(rec?.amount_minor, cur),
          "Money in that no batch, and no group of batches, adds up to."));
        break;
      case "ambiguous": {
        const isDeposit = row.right_ref !== null || row.left_ref === null;
        attention.push(line("warn", "?", "More than one fit",
          isDeposit ? deposit(rec?.when, row.right_ref) : batches, formatMinor(rec?.amount_minor, cur),
          row.reason ?? "More than one grouping of batches adds up to it. Okwan does not pick one."));
        break;
      }
      case "matched_discrepant":
      case "matched_explained": {
        const c = ev.compared;
        const apart = formatMinor(Math.abs(row.discrepancy ?? 0), cur);
        const said = c ? `${batches} total ${formatMinor(c.left_minor, cur)}; the deposit is ${formatMinor(c.right_minor, cur)}, ${apart} apart.` : `${apart} apart.`;
        attention.push(row.status === "matched_explained"
          ? line("neutral", "≈", "Explained", deposit(ev.dated?.right, row.right_ref), formatMinor(c?.right_minor, cur),
              `${said} Accounted for by ${row.explained_by ?? "a recorded adjustment"}.`)
          : line("danger", "≠", "Amounts differ", deposit(ev.dated?.right, row.right_ref), formatMinor(c?.right_minor, cur), said));
        break;
      }
      case "unverifiable_left":
      case "unverifiable_right":
        attention.push(line("neutral", "…", "Could not be checked",
          row.status === "unverifiable_left" ? batches : deposit(rec?.when, row.right_ref), formatMinor(rec?.amount_minor, cur),
          row.reason ?? "The other file does not reach this date."));
        break;
      case "matched": {
        const one = parts.length > 1
          ? `Paid ${parts.length} batches:`
          : `Paid ${refs ? `batch ${refs}` : "one batch"}${ev.dated?.left ? `, closed ${dayOf(ev.dated.left)}` : ""}.`;
        paid.push(line("ok", "✓", "Paid", deposit(ev.dated?.right, row.right_ref), formatMinor(ev.compared?.right_minor, cur), one));
        break;
      }
      default:
        // A status this page does not know is never shown as paid.
        attention.push(line("neutral", "…", "Not read", row.left_ref ? batches : deposit(rec?.when, row.right_ref),
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
export function reach(s: PairSummary): { read: string; since: string; from: string; cut: string } {
  const left = s.coverage?.left ?? null;
  const right = s.coverage?.right ?? null;
  if (!left && !right) return { read: "", since: "", from: "", cut: "" };
  const from = (left?.span_start ?? right?.span_start ?? "").slice(0, 10);
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
  return { read, since, from, cut };
}

/**
 * Said when a file on record starts before the first day the check read:
 * its earlier lines were not checked, and the page says so in as many
 * words. `first` is the earliest day of the merchant's files of that kind.
 */
export function unread(what: "batch report" | "bank statement", first: string | null | undefined, from: string): string {
  if (!first || !from || first >= from) return "";
  const lines = what === "batch report" ? "Batches" : "Deposits";
  return `The ${what} on file goes back to ${dayOf(first)}. ${lines} before ${dayOf(from)} were not checked.`;
}

/** The run in one sentence, and whether anything in it needs a look. */
export function verdict(s: PairSummary): { headline: string; tone: Tone; counts: string[] } {
  const read = (s.coverage?.left?.records ?? 0) + (s.coverage?.right?.records ?? 0);
  const { since, cut } = reach(s);
  // Every batch the run read, never every batch there is.
  const every = since ? `Every batch since ${since} was deposited` : "Every batch the check read was deposited";
  const total = s.matched + s.unmatched_left + s.unmatched_right + s.ambiguous + s.unverifiable_left + s.unverifiable_right;
  const counts = [
    s.matched ? count(s.matched, "deposit matched to its batches", "deposits matched to their batches") : "",
    s.unmatched_left ? count(s.unmatched_left, "batch not deposited", "batches not deposited") : "",
    s.unmatched_right ? count(s.unmatched_right, "deposit with no batch", "deposits with no batch") : "",
    s.matched_with_unexplained_discrepancy ? count(s.matched_with_unexplained_discrepancy, "deposit of a different amount", "deposits of a different amount") : "",
    s.ambiguous ? count(s.ambiguous, "with more than one fit", "with more than one fit") : "",
    s.unverifiable_left + s.unverifiable_right
      ? count(s.unverifiable_left + s.unverifiable_right, "could not be checked", "could not be checked") : "",
  ].filter(Boolean);
  if (total === 0) {
    return {
      headline: read === 0 && s.coverage ? "The files hold nothing in the days this check reads" : "Nothing to check",
      tone: "neutral", counts,
    };
  }
  const breaks = s.unmatched_left + s.matched_with_unexplained_discrepancy;
  if (breaks) {
    return {
      headline: s.unmatched_left
        ? `${count(s.unmatched_left, "batch was", "batches were")} not deposited`
        : `${count(breaks, "deposit does", "deposits do")} not match the batches behind it`,
      tone: "danger", counts,
    };
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
  // A run that stopped at its cap did not read every batch: never the clean line.
  if (cut) return { headline: "No missing deposit among what was read, and not everything was read", tone: "warn", counts };
  if (s.unmatched_right) {
    return { headline: `${every}; ${count(s.unmatched_right, "deposit has", "deposits have")} no batch`, tone: "warn", counts };
  }
  return { headline: every, tone: "ok", counts };
}
