import { BATCHES_DEPOSITS, FILE_CHECKS, type Shown } from "@/lib/file-checks";
import { type ImportFile, KIND_LABEL } from "@/lib/imports";
import { CheckResult } from "./check-result";
import { RunCheck } from "./run-check";
import { ButtonLink } from "./ui/button";
import { Card } from "./ui/card";
import { Section } from "./ui/page-header";

/**
 * The Files tab's check: each processor batch against the bank deposit
 * that paid it. Shows the newest stored run, worst first, and runs a new
 * one on a click. Nothing here reads a file, decides a match or writes a
 * word of the result: `shown` is the stored run already put into words on
 * the server (lib/file-checks.ts), and this draws it.
 */
export function FileCheck({ tenantId, files, shown }: {
  tenantId: string;
  files: ImportFile[];
  shown: Shown;
}) {
  const check = FILE_CHECKS.find((c) => c.name === BATCHES_DEPOSITS)!;
  const have = new Set(files.map((f) => f.kind));
  const missing = check.needs.filter((k) => !have.has(k));
  const importHref = `/merchants/${encodeURIComponent(tenantId)}/import`;

  return (
    <Section
      className="mt-0"
      title={check.title}
      description="Each processor batch against the bank deposit that paid it."
      aside={missing.length === 0 ? <RunCheck tenantId={tenantId} name={check.name} again={shown.state !== "never"} /> : undefined}
    >
      {missing.length > 0 ? (
        <Card as="div" className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 text-sm">
          <p className="min-w-0 flex-1 basis-64 text-ink-soft">
            This check needs {missing.map((k) => KIND_LABEL[k].from.toLowerCase()).join(" and ")} as well. Import{" "}
            {missing.length === 1 ? "it" : "them"} to see which batches were deposited.
          </p>
          <ButtonLink href={importHref}>Import a file</ButtonLink>
        </Card>
      ) : shown.state === "never" ? (
        <Card as="div" className="px-5 py-4 text-sm text-ink-soft">
          The batch report and the bank statement are both here. Run the check to see which batches were deposited and
          which were not. Each run counts toward your plan.
        </Card>
      ) : shown.state === "failed" ? (
        <Card as="div" className="px-5 py-4 text-sm">
          <p className="font-semibold text-danger">The last check did not finish.</p>
          <p className="mt-1 text-ink-soft break-words">{shown.error}</p>
          <p className="mt-1 text-xs text-ink-soft">{shown.at}</p>
        </Card>
      ) : (
        <CheckResult result={shown.result} />
      )}
    </Section>
  );
}
