"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import {
  fieldLabel,
  type FieldSpec,
  type ImportFile,
  KIND_LABEL,
  KINDS,
  type Kind,
  type Layout,
  type Mapping,
  type Preview,
  span,
} from "@/lib/imports";
import { Badge } from "./ui/badge";
import { Button, ButtonLink, buttonClass } from "./ui/button";
import { Card, CardBody, CardHeader } from "./ui/card";
import { IconFileUp } from "./ui/icons";
import { Segmented } from "./ui/segmented";
import { Table, Td, Th } from "./ui/table";

const MAX_BYTES = 8 * 1024 * 1024;
const ACCEPT = ".csv,.txt,.xlsx,.ofx,.qfx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
const NONE = "";

type Problem = { message: string; problems?: string[] };

/** The file's bytes as base64, read in the browser. */
function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function size(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Import a file for a merchant: say what it is, choose it, match its
 * columns to the fields of that kind, import. The file is read twice by
 * the API, once for the preview and once to import, and kept by neither:
 * only the mapped fields of each row are stored. A mapping can be saved
 * and picked for the next file from the same processor or bank. Generic
 * only: the starting mapping comes from the column names, never a preset.
 */
export function ImportFlow({ tenantId, layouts }: { tenantId: string; layouts: Layout[] }) {
  const router = useRouter();
  const id = encodeURIComponent(tenantId);
  const input = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState<Kind>("settlement_batches");
  const [file, setFile] = useState<{ name: string; size: number; content: string } | null>(null);
  const [headerRow, setHeaderRow] = useState(1);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<"reading" | "importing" | null>(null);
  const [error, setError] = useState<Problem | null>(null);
  const [columns, setColumns] = useState<Record<string, string>>({});
  const [currency, setCurrency] = useState("USD");
  const [dayFirst, setDayFirst] = useState(false);
  const [decimalComma, setDecimalComma] = useState(false);
  const [negate, setNegate] = useState<string[]>([]);
  const [skipInvalid, setSkipInvalid] = useState(false);
  const [fromLayout, setFromLayout] = useState(NONE);
  const [saveAs, setSaveAs] = useState<string | null>(null);
  const [done, setDone] = useState<ImportFile | null>(null);

  const fields: FieldSpec[] = preview?.fields[kind] ?? [];
  const forKind = layouts.filter((l) => l.kind === kind);
  const money = fields.filter((f) => f.type === "money" && f.field !== "credit" && f.field !== "debit");
  const mappedCurrency = Boolean(columns.currency);

  function startFrom(p: Preview, k: Kind) {
    setColumns({ ...(p.suggested[k] ?? {}) });
    setNegate([]);
    setFromLayout(NONE);
  }

  async function read(f: File, row: number, layout?: Layout) {
    setError(null);
    setDone(null);
    if (f.size > MAX_BYTES) return setError({ message: `${f.name} is ${size(f.size)}; files up to 8 MB can be imported. Split it and import each part.` });
    setBusy("reading");
    try {
      const content = await readBase64(f);
      setFile({ name: f.name, size: f.size, content });
      const res = await fetch(`/api/merchants/${id}/imports/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: f.name, content_base64: content, header_row: row }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setPreview(null);
        return setError({ message: res.status === 503 ? "File imports aren't open for this workspace yet." : (data.detail ?? "the file could not be read") });
      }
      setPreview(data as Preview);
      setHeaderRow(row);
      if (layout) fill(layout);
      else startFrom(data as Preview, kind);
    } catch {
      setError({ message: "The file could not be read in the browser." });
    } finally {
      setBusy(null);
    }
  }

  function chooseKind(k: Kind) {
    setKind(k);
    if (preview) startFrom(preview, k);
  }

  /** A saved mapping's choices, into the form. */
  function fill(l: Layout) {
    const m = l.mapping;
    setFromLayout(l.name);
    setColumns({ ...m.columns });
    setCurrency(m.currency ?? "USD");
    setDayFirst(Boolean(m.day_first));
    setDecimalComma(Boolean(m.decimal_comma));
    setNegate(m.negate ?? []);
    setSkipInvalid(Boolean(m.skip_invalid_rows));
  }

  function applyLayout(name: string) {
    const l = layouts.find((x) => x.name === name);
    if (!l) {
      setFromLayout(NONE);
      if (preview) startFrom(preview, kind);
      return;
    }
    // A mapping saved against another header row reads the file again first.
    const row = l.mapping.header_row ?? 1;
    const f = input.current?.files?.[0];
    if (row !== headerRow && f) void read(f, row, l);
    else fill(l);
  }

  const mapping: Mapping = useMemo(() => ({
    kind,
    columns: Object.fromEntries(Object.entries(columns).filter(([, v]) => v)),
    currency: currency.trim() ? currency.trim().toUpperCase() : null,
    day_first: dayFirst,
    decimal_comma: decimalComma,
    negate,
    header_row: headerRow,
    skip_invalid_rows: skipInvalid,
    name: (saveAs?.trim() || fromLayout || "custom"),
  }), [kind, columns, currency, dayFirst, decimalComma, negate, headerRow, skipInvalid, saveAs, fromLayout]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !preview) return;
    setError(null);
    setBusy("importing");
    try {
      if (saveAs !== null) {
        const name = saveAs.trim();
        if (!name) return setError({ message: "Name the mapping to save it, or untick Save." });
        const saved = await fetch(`/api/merchants/${id}/import-layouts/${encodeURIComponent(name)}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(mapping),
        });
        if (!saved.ok) {
          const d = await saved.json().catch(() => ({}));
          return setError({ message: `The mapping was not saved: ${d.detail ?? "try another name"}` });
        }
      }
      const res = await fetch(`/api/merchants/${id}/imports`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name, content_base64: file.content, mapping }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return setError({
          message: res.status === 409 ? "This file is already imported for this kind. Delete it on the Files tab to import it again." : (data.detail ?? "the file was not imported"),
          problems: data.problems,
        });
      }
      setDone(data as ImportFile);
      router.refresh();
    } finally {
      setBusy(null);
    }
  }

  function reset() {
    setFile(null);
    setPreview(null);
    setDone(null);
    setError(null);
    setSaveAs(null);
    setHeaderRow(1);
    if (input.current) input.current.value = "";
  }

  if (done) {
    return (
      <Card>
        <CardHeader title={`Imported ${done.rows.toLocaleString("en-US")} ${done.rows === 1 ? "row" : "rows"}`} description={done.filename} />
        <CardBody className="space-y-2 text-sm">
          <p className="flex flex-wrap items-center gap-2">
            <Badge tone="ok" symbol="✓">{KIND_LABEL[done.kind].short}</Badge>
            {done.first_date && <span>{span(done.first_date, done.last_date)}</span>}
            <span className="text-ink-soft">mapping {done.layout}</span>
          </p>
          {done.skipped > 0 && (
            <p className="text-ink-soft">
              {done.skipped} {done.skipped === 1 ? "row was" : "rows were"} skipped because a required field couldn&apos;t be read:
              {" "}rows {done.skipped_rows.slice(0, 20).join(", ")}{done.skipped > 20 ? "…" : ""}.
            </p>
          )}
          {done.card_columns.length > 0 && (
            <p className="text-ink-soft">Card columns ({done.card_columns.join(", ")}) were not stored beyond the last four digits you mapped.</p>
          )}
          <div className="flex flex-wrap gap-2 pt-2">
            <ButtonLink variant="primary" href={`/merchants/${id}?tab=files`}>See the files</ButtonLink>
            <Button onClick={reset}>Import another file</Button>
          </div>
        </CardBody>
      </Card>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <Card>
        <CardHeader title="1. What is in the file?" />
        <CardBody>
          <fieldset>
            <legend className="sr-only">What the file holds</legend>
            <div className="grid gap-3 md:grid-cols-3">
              {KINDS.map((k) => (
                <label
                  key={k}
                  className={`flex min-h-11 cursor-pointer gap-3 rounded-xl border p-3 text-sm ${kind === k ? "border-accent bg-accent-soft/40" : "border-line bg-surface hover:border-accent/40"}`}
                >
                  <input type="radio" name="kind" value={k} checked={kind === k} onChange={() => chooseKind(k)} className="mt-1 accent-[var(--color-accent)]" />
                  <span className="min-w-0">
                    <span className="block font-semibold">{KIND_LABEL[k].short}</span>
                    <span className="mt-0.5 block text-xs text-ink-soft">{KIND_LABEL[k].from}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="2. Choose the file" description="CSV, Excel (.xlsx) or OFX, up to 8 MB. Read in memory; the file itself is never kept." />
        <CardBody className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <label className={buttonClass("secondary", "cursor-pointer")}>
              <IconFileUp className="h-4 w-4" />
              {file ? "Choose another file" : "Choose a file"}
              <input
                ref={input}
                type="file"
                accept={ACCEPT}
                className="sr-only"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) void read(f, headerRow); }}
              />
            </label>
            {busy === "reading" && <span role="status" className="text-sm text-ink-soft">Reading…</span>}
            {file && busy !== "reading" && (
              <span className="min-w-0 text-sm">
                <span className="font-medium break-all">{file.name}</span>
                <span className="text-ink-soft"> · {size(file.size)}</span>
              </span>
            )}
          </div>
          {preview && (
            <div className="flex flex-wrap items-end gap-3 text-sm">
              <label className="w-40">
                <span className="mb-1 block font-medium">Column names on row</span>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={headerRow}
                  onChange={(e) => setHeaderRow(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
                  className="field"
                />
              </label>
              <Button onClick={() => input.current?.files?.[0] && void read(input.current.files[0], headerRow)} disabled={busy !== null}>
                Read again
              </Button>
              <p className="basis-full text-xs text-ink-soft">
                {preview.rows.toLocaleString("en-US")} rows under {preview.header.length} columns
                {preview.format !== "csv" ? ` · ${preview.format === "xlsx" ? "Excel, first sheet" : "OFX"}` : ""}.
                {" "}Some reports put a title above the column names: move the row down to skip it.
              </p>
            </div>
          )}
        </CardBody>
      </Card>

      {preview && (
        <Card>
          <CardHeader
            title="3. Match the columns"
            description="Each field takes one column of the file. Required fields must be matched; the rest are optional."
            actions={forKind.length > 0 ? (
              <label className="flex items-center gap-2 text-sm">
                <span className="whitespace-nowrap text-ink-soft">Start from</span>
                <select value={fromLayout} onChange={(e) => applyLayout(e.target.value)} className="field max-w-48">
                  <option value={NONE}>The column names</option>
                  {forKind.map((l) => <option key={l.name} value={l.name}>{l.name}</option>)}
                </select>
              </label>
            ) : undefined}
          />
          {preview.card_columns.length > 0 && (
            <p className="border-b border-line px-5 py-3 text-xs text-ink-soft">
              <span className="font-medium text-ink">Card columns:</span> {preview.card_columns.join(", ")}. They can only fill
              {" "}&ldquo;Card, last 4 digits&rdquo;, and nothing more of them is kept.
            </p>
          )}
          <CardBody list>
            {fields.map((f) => (
              <li key={f.field} className="grid gap-2 px-5 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,18rem)] sm:items-center">
                <label htmlFor={`col-${f.field}`} className="min-w-0 text-sm">
                  <span className="font-medium">{fieldLabel(f.field)}</span>
                  {f.required ? (
                    <span className="ml-2 text-xs text-ink-soft">{f.field === "currency" ? "required unless set below" : "required"}</span>
                  ) : null}
                  <span className="mt-0.5 block text-xs text-ink-soft">{f.description}</span>
                </label>
                <select
                  id={`col-${f.field}`}
                  value={columns[f.field] ?? NONE}
                  onChange={(e) => setColumns((c) => ({ ...c, [f.field]: e.target.value }))}
                  className="field"
                >
                  <option value={NONE}>Not in this file</option>
                  {preview.header.filter(Boolean).map((h) => <option key={h} value={h}>{h}</option>)}
                </select>
              </li>
            ))}
          </CardBody>
        </Card>
      )}

      {preview && (
        <Card>
          <CardHeader title="4. How the file writes things" />
          <CardBody className="grid gap-5 md:grid-cols-2">
            <label className="text-sm">
              <span className="mb-1 block font-medium">Currency{mappedCurrency ? " when a row has none" : ""}</span>
              <input
                value={currency}
                onChange={(e) => setCurrency(e.target.value.slice(0, 3))}
                className="field w-28 font-mono uppercase"
                maxLength={3}
                aria-describedby="currency-hint"
              />
              <span id="currency-hint" className="mt-1 block text-xs text-ink-soft">Three letters: USD, CAD, EUR.</span>
            </label>
            <div className="text-sm">
              <span className="mb-1 block font-medium">Dates like 03/04/2026 are</span>
              <Segmented
                label="Date order"
                value={dayFirst ? "day" : "month"}
                onChange={(v) => setDayFirst(v === "day")}
                items={[{ key: "month", label: "Month first" }, { key: "day", label: "Day first" }]}
              />
            </div>
            <div className="text-sm">
              <span className="mb-1 block font-medium">Amounts are written</span>
              <Segmented
                label="Decimal mark"
                value={decimalComma ? "comma" : "point"}
                onChange={(v) => setDecimalComma(v === "comma")}
                items={[{ key: "point", label: "1,234.56" }, { key: "comma", label: "1.234,56" }]}
              />
            </div>
            <fieldset className="text-sm">
              <legend className="mb-1 font-medium">Amounts signed the other way</legend>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {money.filter((f) => columns[f.field]).map((f) => (
                  <label key={f.field} className="inline-flex min-h-11 items-center gap-2">
                    <input
                      type="checkbox"
                      checked={negate.includes(f.field)}
                      onChange={(e) => setNegate((n) => (e.target.checked ? [...n, f.field] : n.filter((x) => x !== f.field)))}
                    />
                    {fieldLabel(f.field)}
                  </label>
                ))}
                {money.every((f) => !columns[f.field]) && <span className="text-xs text-ink-soft">Match an amount column first.</span>}
              </div>
            </fieldset>
            <label className="inline-flex min-h-11 items-center gap-2 text-sm md:col-span-2">
              <input type="checkbox" checked={skipInvalid} onChange={(e) => setSkipInvalid(e.target.checked)} />
              Skip rows whose required fields can&apos;t be read (a totals row, say), and list them
            </label>
          </CardBody>
        </Card>
      )}

      {preview && (
        <Card flush>
          <CardHeader title="The first rows" description={`As read from the file${preview.card_columns.length ? "; card columns show four digits at most" : ""}.`} />
          <Table flush minWidth={Math.max(480, preview.header.length * 130)} label="The file's first rows">
            <thead>
              <tr>{preview.header.map((h, j) => <Th key={`${h}-${j}`}>{h || `Column ${j + 1}`}</Th>)}</tr>
            </thead>
            <tbody className="divide-y divide-line">
              {preview.sample.map((row, i) => (
                <tr key={i}>{preview.header.map((_, j) => <Td key={j} className="whitespace-nowrap">{row[j] ?? ""}</Td>)}</tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {preview && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="inline-flex min-h-11 items-center gap-2">
              <input type="checkbox" checked={saveAs !== null} onChange={(e) => setSaveAs(e.target.checked ? (fromLayout || "") : null)} />
              Save this mapping as
            </label>
            {saveAs !== null && (
              <input
                value={saveAs}
                onChange={(e) => setSaveAs(e.target.value.slice(0, 60))}
                placeholder="e.g. Bank of the West statement"
                aria-label="Name for the saved mapping"
                className="field min-w-0 flex-1 sm:max-w-sm"
                pattern="[A-Za-z0-9][A-Za-z0-9 ._\-]{0,59}"
                title="Letters, digits, spaces, dots, dashes and underscores"
              />
            )}
          </div>
          {error && <ErrorBox error={error} />}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant="primary" disabled={busy !== null}>
              {busy === "importing" ? "Importing…" : `Import ${preview.rows.toLocaleString("en-US")} rows`}
            </Button>
            <Link href={`/merchants/${id}?tab=files`} className="inline-flex min-h-11 items-center text-sm text-ink-soft underline-offset-4 hover:underline">
              Cancel
            </Link>
          </div>
        </div>
      )}
      {!preview && error && <ErrorBox error={error} />}
    </form>
  );
}

function ErrorBox({ error }: { error: Problem }) {
  return (
    <div role="alert" className="rounded-xl border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">
      <p className="font-medium">{error.message}</p>
      {error.problems && error.problems.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5">
          {error.problems.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
    </div>
  );
}
