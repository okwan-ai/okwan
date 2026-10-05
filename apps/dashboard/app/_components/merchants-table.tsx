"use client";

import Link from "next/link";
import { useState } from "react";
import { ago, FOLD_READS, missingFor, railLabel, type RunDigest, SURFACE_LABEL } from "@/lib/finding";
import { owedAmount, RunStatus } from "./merchant-status";
import { RailStrip } from "./rail-strip";
import { BrandMark } from "./ui/brand-mark";
import { Card, CardHeader } from "./ui/card";
import { EmptyState } from "./ui/empty-state";
import { IconSearch } from "./ui/icons";
import { RowCard, RowCardList } from "./ui/row-card";
import { Table, Td, Th } from "./ui/table";

/** One merchant as the list draws it. Built on the server by
 *  lib/runs.ts toMerchantRow, so only plain data crosses to the browser. */
export type MerchantRow = {
  id: string;
  name: string;
  createdAt: string;
  ready: string[];
  partial: string[];
  known: boolean;
  /** skipped: not every system a check reads is connected (or unknown). */
  state: "skipped" | "none" | "ok" | "failed";
  /** The newest saved check; null when there is none. */
  digest: RunDigest | null;
  detail?: string;
  at?: number;
  surface?: string;
};

const at = (id: string, query: string) => `/merchants/${encodeURIComponent(id)}?${query}`;

/** The one thing to do next for a merchant that needs you. */
export function nextStep(r: MerchantRow): { label: string; href: string } {
  if (r.state === "skipped") {
    const missing = r.known ? missingFor(r) : [];
    if (missing.length) return { label: `Connect ${railLabel(missing[0])} →`, href: at(r.id, `tab=connections&connect=${missing[0]}`) };
    return { label: "Check connections →", href: at(r.id, "tab=connections") };
  }
  if (r.state === "failed") return { label: "Check connections →", href: at(r.id, "tab=connections") };
  if (r.state === "none") return { label: "Run check →", href: at(r.id, "tab=findings") };
  return { label: "View findings →", href: at(r.id, "tab=findings") };
}

/** Collected twice first, then other findings, couldn't run, clean, never
 *  checked, not ready. */
export function rank(r: MerchantRow): number {
  const d = r.digest;
  if (r.state === "ok" && d?.ok) return d.twice ? 0 : d.open ? 1 : 3;
  if (r.state === "failed") return 2;
  if (r.state === "none") return 4;
  return 5;
}

function sortRows(rows: MerchantRow[]): MerchantRow[] {
  // Amounts compare only within one currency; a merchant without a single
  // owed-back currency sorts after the priced ones in its rank.
  const cur = (r: MerchantRow) => (r.digest?.ok && r.digest.twice ? r.digest.currency ?? "~" : "~");
  const owed = (r: MerchantRow) => (r.digest?.ok ? r.digest.overMinor : 0);
  return [...rows].sort((a, b) => rank(a) - rank(b)
    || cur(a).localeCompare(cur(b))
    || owed(b) - owed(a)
    || a.name.localeCompare(b.name));
}

/** "just now · Dashboard", or why there is no check. */
function lastCheck(r: MerchantRow): string {
  if (r.state === "ok" || r.state === "failed") {
    return `${r.at ? ago(r.at) : ""}${r.surface ? ` · ${SURFACE_LABEL[r.surface] ?? r.surface}` : ""}`;
  }
  return r.state === "none" ? "Not checked yet" : "Not ready";
}

/** One system's cell: a tile when connected, a 44px connect link around a
 *  muted tile when not. The column header names the system. */
function SystemCell({ r, name }: { r: MerchantRow; name: string }) {
  const label = railLabel(name);
  if (!r.known) return <span className="text-xs text-ink-soft">Unknown</span>;
  if (r.ready.includes(name)) {
    return (
      <span className="inline-flex min-h-11 items-center">
        <BrandMark name={name} label={label} size={24} tile />
        <span className="sr-only">{label} connected</span>
      </span>
    );
  }
  return (
    <Link
      href={at(r.id, `tab=connections&connect=${name}`)}
      aria-label={`Connect ${label} for ${r.name}`}
      className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg hover:bg-ink/5"
    >
      <BrandMark name={name} label={label} size={24} tile muted />
    </Link>
  );
}

function StatusCell({ r }: { r: MerchantRow }) {
  return (
    <>
      <RunStatus m={r} d={r.digest} />
      <p className="mt-1 text-xs text-ink-soft" suppressHydrationWarning>{lastCheck(r)}</p>
      {r.state === "failed" && r.detail && <p className="mt-1 max-w-[260px] text-xs break-words text-danger">{r.detail}</p>}
    </>
  );
}

function SystemHeads() {
  return FOLD_READS.map((c) => (
    <Th key={c}>
      <span className="inline-flex items-center gap-1.5"><BrandMark name={c} label={railLabel(c)} size={16} />{railLabel(c)}</span>
    </Th>
  ));
}

function MerchantName({ r }: { r: MerchantRow }) {
  const added = new Date(r.createdAt).toLocaleDateString("en-US", { dateStyle: "medium" });
  return (
    <>
      <Link
        href={`/merchants/${encodeURIComponent(r.id)}`}
        title={`Added ${added}`}
        className="font-medium underline-offset-4 hover:underline"
        suppressHydrationWarning
      >
        {r.name}
      </Link>
      <code className="block font-mono text-xs text-ink-soft">{r.id}</code>
    </>
  );
}

/**
 * The one merchant list. `full` (/merchants): every merchant, searchable,
 * findings first. `needs-you` (Overview): only merchants that are not
 * ready, have unknown connections, couldn't run, or were never checked,
 * each with one next step. Nothing here runs a check.
 */
export function MerchantList({ rows, mode }: { rows: MerchantRow[]; mode: "full" | "needs-you" }) {
  const [q, setQ] = useState("");
  if (mode === "needs-you") return <NeedsYou rows={sortRows(rows.filter((r) => r.state !== "ok"))} />;

  const term = q.trim().toLowerCase();
  const shown = sortRows(rows.filter((m) => !term || m.name.toLowerCase().includes(term) || m.id.toLowerCase().includes(term)));
  const ready = rows.filter((m) => m.known && missingFor(m).length === 0).length;
  const withFindings = rows.filter((m) => m.digest?.ok && m.digest.open > 0).length;

  return (
    <Card flush>
      <CardHeader
        title={`${rows.length} merchant${rows.length === 1 ? "" : "s"}`}
        description={`${ready} ready to check${withFindings ? ` · ${withFindings} with findings` : ""}`}
        actions={
          <label className="relative max-sm:w-full">
            <span className="sr-only">Search merchants</span>
            <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-ink-soft" />
            <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or id" className="field w-full py-2 pl-8 sm:w-56" />
          </label>
        }
      />
      {shown.length === 0 ? (
        <div className="p-4"><EmptyState title="No merchant matches">Try part of the name or the ten_ id.</EmptyState></div>
      ) : (
        <>
          <div className="hidden sm:block">
            <Table label="Merchants" minWidth={760} flush>
              <thead>
                <tr>
                  <Th>Merchant</Th>
                  <SystemHeads />
                  <Th>Last check</Th>
                  <Th className="text-right">Owed back</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {shown.map((r) => (
                  <tr key={r.id} className="hover:bg-canvas/60">
                    <Td><MerchantName r={r} /></Td>
                    {FOLD_READS.map((c) => <Td key={c}><SystemCell r={r} name={c} /></Td>)}
                    <Td><StatusCell r={r} /></Td>
                    <Td className="text-right font-medium tabular-nums">{owedAmount(r.digest)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>
          <ul aria-label="Merchants" className="divide-y divide-line sm:hidden">
            {shown.map((r) => (
              <RowCard
                key={r.id}
                href={`/merchants/${encodeURIComponent(r.id)}`}
                title={<span className="font-medium">{r.name}</span>}
                money={r.digest?.ok ? { value: owedAmount(r.digest), label: "owed back" } : undefined}
                line2={<><RunStatus m={r} d={r.digest} /><RailStrip ready={r.ready} partial={r.partial} known={r.known} size="sm" labels /></>}
                meta={<span suppressHydrationWarning>{r.state === "ok" || r.state === "failed" ? `Last check ${lastCheck(r)}` : lastCheck(r)}</span>}
              />
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function NeedsYou({ rows }: { rows: MerchantRow[] }) {
  return (
    <>
      <div className="hidden sm:block">
        <Table label="Merchants that need you" minWidth={760}>
          <thead>
            <tr>
              <Th>Merchant</Th>
              <SystemHeads />
              <Th>Status</Th>
              <Th>Next step</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {rows.map((r) => {
              const next = nextStep(r);
              return (
                <tr key={r.id} className="hover:bg-canvas/60">
                  <Td><MerchantName r={r} /></Td>
                  {FOLD_READS.map((c) => <Td key={c}><SystemCell r={r} name={c} /></Td>)}
                  <Td><StatusCell r={r} /></Td>
                  <Td>
                    <Link href={next.href} className="inline-flex min-h-11 items-center font-medium whitespace-nowrap underline underline-offset-4">
                      {next.label}
                    </Link>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </div>
      <RowCardList label="Merchants that need you">
        {rows.map((r) => {
          const next = nextStep(r);
          return (
            <RowCard
              key={r.id}
              href={next.href}
              title={<span className="font-medium">{r.name}</span>}
              line2={<><RunStatus m={r} d={r.digest} /><RailStrip ready={r.ready} partial={r.partial} known={r.known} size="sm" labels /></>}
              meta={<span className="font-medium text-ink">Next: {next.label}</span>}
            />
          );
        })}
      </RowCardList>
    </>
  );
}
