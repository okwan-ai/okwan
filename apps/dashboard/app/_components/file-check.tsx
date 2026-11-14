import { batchLines, BATCHES_DEPOSITS, type CheckLine, dayOf, FILE_CHECKS, type PairRun, reach, unread, verdict } from "@/lib/file-checks";
import { type ImportFile, KIND_LABEL } from "@/lib/imports";
import { RunCheck } from "./run-check";
import { Badge } from "./ui/badge";
import { ButtonLink } from "./ui/button";
import { Card } from "./ui/card";
import { Section } from "./ui/page-header";

const HEADLINE = { danger: "text-danger", warn: "text-ink", ok: "text-ok", neutral: "text-ink-soft" } as const;

function when(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
  return `${dayOf(iso)}, ${time} UTC`;
}

function Line({ line }: { line: CheckLine }) {
  return (
    <li className="px-5 py-3.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
        <Badge tone={line.tone} symbol={line.symbol}>{line.label}</Badge>
        <p className="min-w-0 flex-1 basis-48 font-semibold break-words">{line.title}</p>
        <p className="font-semibold tabular-nums">{line.amount}</p>
      </div>
      <p className="mt-1.5 text-sm text-ink-soft">{line.detail}</p>
      {line.parts.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm text-ink-soft">
          {line.parts.map((p, n) => (
            <li key={n} className="flex flex-wrap items-baseline gap-x-4 gap-y-0.5">
              <span className="min-w-24 font-medium text-ink break-words">{p.ref}</span>
              {p.when && <span className="whitespace-nowrap">closed {p.when}</span>}
              <span className="whitespace-nowrap tabular-nums">{p.amount}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

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
  const said = summary ? verdict(summary) : null;
  const read = summary ? reach(summary) : null;
  const { attention, paid } = summary ? batchLines(run?.rows ?? []) : { attention: [], paid: [] };
  const stale = run ? files.some((f) => Date.parse(f.created_at) > Date.parse(run.finished_at)) : false;
  const statementEnd = files.filter((f) => f.kind === "bank_deposits" && f.last_date).map((f) => f.last_date!).sort().at(-1);
  const earliest = (kind: string) => files.filter((f) => f.kind === kind && f.first_date).map((f) => f.first_date!).sort()[0];
  const older = read
    ? [unread("batch report", earliest("settlement_batches"), read.from), unread("bank statement", earliest("bank_deposits"), read.from)].filter(Boolean)
    : [];
  const shown = attention.length + paid.length;

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
      ) : run.status === "failed" || !said ? (
        <Card as="div" className="px-5 py-4 text-sm">
          <p className="font-semibold text-danger">The last check did not finish.</p>
          <p className="mt-1 text-ink-soft break-words">{run.error ?? "No reason was recorded."}</p>
          <p className="mt-1 text-xs text-ink-soft">{when(run.finished_at)}</p>
        </Card>
      ) : (
        <Card flush>
          <div className="border-b border-line px-5 py-4">
            <p className={`text-lg font-bold ${HEADLINE[said.tone]}`}>{said.headline}</p>
            {said.counts.length > 0 && <p className="mt-1 text-sm text-ink-soft">{said.counts.join(" · ")}</p>}
            <p className="mt-1 text-xs text-ink-soft">
              Checked {when(run.finished_at)}
              {stale && <> · A file was imported after this check ran. Run it again to include it.</>}
            </p>
            {read?.cut && <p className="mt-2 text-sm font-semibold text-danger">{read.cut}</p>}
            {older.map((line) => <p key={line} className="mt-2 text-sm font-semibold">{line}</p>)}
            {read?.read && <p className="mt-1 text-xs text-ink-soft">{read.read}</p>}
          </div>
          {attention.length > 0 && <ul className="divide-y divide-line">{attention.map((l) => <Line key={l.key} line={l} />)}</ul>}
          {paid.length > 0 && (
            <details className={attention.length > 0 ? "border-t border-line" : ""} open={attention.length === 0}>
              <summary className="cursor-pointer px-5 py-3.5 text-sm font-semibold">
                {paid.length === 1 ? "1 deposit matched to its batches" : `${paid.length} deposits matched to their batches`}
              </summary>
              <ul className="divide-y divide-line border-t border-line">{paid.map((l) => <Line key={l.key} line={l} />)}</ul>
            </details>
          )}
          {(run.has_more || (summary?.unmatched_left ?? 0) > 0) && (
            <div className="space-y-1 border-t border-line px-5 py-3 text-xs text-ink-soft">
              {run.has_more && run.rows_total !== null && (
                <p>Showing the first {shown.toLocaleString("en-US")} of {run.rows_total.toLocaleString("en-US")} lines, the ones that need a look first.</p>
              )}
              {(summary?.unmatched_left ?? 0) > 0 && statementEnd && (
                <p>
                  The newest statement on file runs to {dayOf(statementEnd)}. A batch closed in the last few days before
                  that may simply not be deposited yet.
                </p>
              )}
            </div>
          )}
        </Card>
      )}
    </Section>
  );
}
