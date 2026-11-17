"use client";

import { asWritten, type Shown } from "@/lib/file-checks";
import { day, kindLabel, type ImportFile, type Layout, span } from "@/lib/imports";
import { DeleteButton } from "./delete-button";
import { FileCheck } from "./file-check";
import { Badge } from "./ui/badge";
import { ButtonLink } from "./ui/button";
import { Card, CardBody } from "./ui/card";
import { EmptyState } from "./ui/empty-state";
import { HowItWorks } from "./ui/how-it-works";
import { IconFileUp, IconSheet } from "./ui/icons";
import { Section } from "./ui/page-header";

const FORMAT: Record<string, string> = { csv: "CSV", xlsx: "Excel", ofx: "OFX" };

/**
 * A file's name, or a mapping's, as the merchant gave it. The API holds
 * both to plain visible text now; names stored before it did are drawn
 * the same way as a reference from a file: in an element and a direction
 * of their own, with any character that cannot be seen spelled out, so
 * none of them can turn the page's own words around or pass for them.
 */
function Named({ value }: { value: string }) {
  return <bdi dir="ltr" data-merchant-name>{asWritten(value)}</bdi>;
}

/**
 * A merchant's Files tab: what its files show (each batch against the
 * deposit that paid it), the files imported for it, and the column
 * mappings saved for the next one. A file holds only the columns its
 * mapping named, and goes 13 months after upload; deleting it deletes its
 * rows at once, and with them every stored result of a check that read
 * this merchant's files.
 *
 * A client component, drawn from data, like the Findings and Connections
 * tabs, and for a reason two attacker's passes found between them. A page
 * reaches the browser in pieces. Drawn on the server, the tab's later parts
 * were sent in later pieces than the elements that hold them, and on a slow
 * link the browser took such an element over before its inside had
 * arrived, took it again when it had, and React threw the whole page away
 * and drew it again (one load in seven at 400 kbit/s). Handed its data,
 * the tab is one piece: nothing in it can arrive after what holds it.
 */
export function FilesPanel({ tenantId, files, layouts, unavailable, check }: {
  tenantId: string;
  files: ImportFile[];
  layouts: Layout[];
  /** The listings didn't load: say so instead of claiming none. */
  unavailable: boolean;
  /** The newest stored run of the batches-against-deposits check, put into words on the server. */
  check: Shown;
}) {
  const id = encodeURIComponent(tenantId);
  const importHref = `/merchants/${id}/import`;
  // With no file yet the empty state below says what to import; the check
  // has nothing to say until there is one.
  const checkable = !unavailable && files.length > 0;
  return (
    <div>
      {checkable && <FileCheck tenantId={tenantId} files={files} shown={check} />}
      <Section
        className={checkable ? undefined : "mt-0"}
        title="Imported files"
        description="Settlement reports, delivery platform reports and bank statements, read for this merchant's checks."
        aside={<ButtonLink variant="primary" href={importHref}><IconFileUp className="h-4 w-4" />Import a file</ButtonLink>}
      >
        {unavailable ? (
          <Card as="div" className="px-5 py-4 text-sm text-ink-soft">The Okwan API didn&apos;t answer. Try again in a moment.</Card>
        ) : files.length === 0 ? (
          <EmptyState
            icon={<IconSheet className="h-5 w-5" />}
            title="No files imported yet"
            benefits={[
              "A processor's batch report or transaction export, a delivery platform's report, or a bank statement",
              "CSV, Excel (.xlsx) or OFX, up to 8 MB",
              "Only the columns you map are kept, for 13 months",
            ]}
            action={<ButtonLink variant="primary" href={importHref}>Import a file</ButtonLink>}
          >
            Batches against the deposits that paid them, once both are here.
          </EmptyState>
        ) : (
          <Card flush>
            <CardBody list>
              {files.map((f) => (
                <li key={f.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3.5">
                  <div className="min-w-0 flex-1 basis-64">
                    <p className="truncate font-semibold" title={asWritten(f.filename)}><Named value={f.filename} /></p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-soft">
                      <Badge>{kindLabel(f.kind)}</Badge>
                      <span>{f.rows.toLocaleString("en-US")} {f.rows === 1 ? "row" : "rows"}</span>
                      {f.first_date && <span>{span(f.first_date, f.last_date)}</span>}
                      <span>{FORMAT[f.format] ?? f.format}</span>
                    </p>
                    <p className="mt-1 text-xs text-ink-soft">
                      Mapping <span className="font-medium text-ink"><Named value={f.layout} /></span> · imported {day(f.created_at)} · kept until {day(f.expires_at)}
                      {f.skipped > 0 && <> · {f.skipped} {f.skipped === 1 ? "row" : "rows"} skipped</>}
                    </p>
                  </div>
                  <DeleteButton url={`/api/merchants/${id}/imports/${encodeURIComponent(f.id)}`} what={asWritten(f.filename)} confirmLabel="Delete file and rows" />
                </li>
              ))}
            </CardBody>
          </Card>
        )}
      </Section>

      <Section title="Saved mappings" description="Pick one when the next file comes from the same processor or bank.">
        {unavailable ? null : layouts.length === 0 ? (
          <Card as="div" className="px-5 py-4 text-sm text-ink-soft">
            None yet. Save a mapping while you import a file, and pick it next time.
          </Card>
        ) : (
          <Card flush>
            <CardBody list>
              {layouts.map((l) => (
                <li key={l.name} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3">
                  <div className="min-w-0 flex-1 basis-56">
                    <p className="truncate font-semibold"><Named value={l.name} /></p>
                    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-soft">
                      <Badge>{kindLabel(l.kind)}</Badge>
                      <span>{Object.keys(l.mapping.columns ?? {}).length} columns mapped</span>
                      <span>saved {day(l.updated_at)}</span>
                    </p>
                  </div>
                  <DeleteButton url={`/api/merchants/${id}/import-layouts/${encodeURIComponent(l.name)}`} what={`the mapping ${asWritten(l.name)}`} />
                </li>
              ))}
            </CardBody>
          </Card>
        )}
      </Section>

      <div className="mt-6">
        <HowItWorks
          label="What happens to a file"
          items={[
            "It is read once, in memory, and never kept. Only the columns you map are stored, as rows.",
            "A file holding a full card number anywhere is refused before anything is kept. Mask card columns to the last four digits.",
            "Rows go 13 months after upload. Deleting a file deletes its rows at once, and with them the results of checks that read this merchant's files: run the check again on what is left.",
          ]}
        />
      </div>
    </div>
  );
}
