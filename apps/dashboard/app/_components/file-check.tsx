import { BATCHES_DEPOSITS, dayOf, drawn, FILE_CHECKS, type PairRun, reach, unread, verdict } from "@/lib/file-checks";
import { type ImportFile, KIND_LABEL } from "@/lib/imports";
import { CheckResult, type Result } from "./check-result";
import { RunCheck } from "./run-check";
import { ButtonLink } from "./ui/button";
import { Card } from "./ui/card";
import { Section } from "./ui/page-header";

function when(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return `${dayOf(iso)}, ${time} UTC`;
}

const n = (count: number) => count.toLocaleString("en-US");

/**
 * The Files tab's check: each processor batch against the bank deposit
 * that paid it. Shows the newest stored run, worst first, and runs a new
 * one on a click. Nothing here reads a file or decides a match: the lines
 * are the stored run's rows, said in words.
 */
export function FileCheck({ tenantId, files, run }: {
  tenantId: string;
  files: ImportFile[];
  /** The newest stored run of the check, or null when it has never run. */
  run: PairRun | null;
}) {
  const check = FILE_CHECKS.find((c) => c.name === BATCHES_DEPOSITS)!;
  const have = new Set(files.map((f) => f.kind));
  const missing = check.needs.filter((k) => !have.has(k));
  const importHref = `/merchants/${encodeURIComponent(tenantId)}/import`;
  const summary = run?.status === "ok" ? run.summary : null;
  const result = run && summary ? resultOf(run, summary, files) : null;

  return (
    <Section
      className="mt-0"
      title={check.title}
      description="Each processor batch against the bank deposit that paid it."
      aside={missing.length === 0 ? <RunCheck tenantId={tenantId} name={check.name} again={run !== null} /> : undefined}
    >
      {missing.length > 0 ? (
        <Card as="div" className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 text-sm">
          <p className="min-w-0 flex-1 basis-64 text-ink-soft">
            This check needs {missing.map((k) => KIND_LABEL[k].from.toLowerCase()).join(" and ")} as well. Import{" "}
            {missing.length === 1 ? "it" : "them"} to see which batches were deposited.
          </p>
          <ButtonLink href={importHref}>Import a file</ButtonLink>
        </Card>
      ) : !run ? (
        <Card as="div" className="px-5 py-4 text-sm text-ink-soft">
          The batch report and the bank statement are both here. Run the check to see which batches were deposited and
          which were not. Each run counts toward your plan.
        </Card>
      ) : run.status === "failed" || !result ? (
        <Card as="div" className="px-5 py-4 text-sm">
          <p className="font-semibold text-danger">The last check did not finish.</p>
          <p className="mt-1 text-ink-soft break-words">{run.error ?? "No reason was recorded."}</p>
          <p className="mt-1 text-xs text-ink-soft">{when(run.finished_at)}</p>
        </Card>
      ) : (
        <CheckResult result={result} />
      )}
    </Section>
  );
}

/**
 * Everything the result card says, as words: the headline, what the run
 * read and did not, the first lines of each list, and the notes under
 * them. Written here, on the server, from the stored run and the files on
 * record; the card only draws it.
 */
function resultOf(run: PairRun, summary: NonNullable<PairRun["summary"]>, files: ImportFile[]): Result {
  const said = verdict(summary);
  const read = reach(summary);
  const { attention, paid, clean, shown, total } = drawn(run, summary);
  const stale = files.some((f) => Date.parse(f.created_at) > Date.parse(run.finished_at));
  const statementEnd = files.filter((f) => f.kind === "bank_deposits" && f.last_date).map((f) => f.last_date!).sort().at(-1);
  const earliest = (kind: string) => files.filter((f) => f.kind === kind && f.first_date).map((f) => f.first_date!).sort()[0];
  return {
    headline: said.headline,
    tone: said.tone,
    counts: said.counts.join(" · "),
    checked: `Checked ${when(run.finished_at)}${stale ? " · A file was imported after this check ran. Run it again to include it." : ""}`,
    cut: read.cut,
    older: [unread("batch report", earliest("settlement_batches"), read.from), unread("bank statement", earliest("bank_deposits"), read.from)].filter(Boolean),
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
