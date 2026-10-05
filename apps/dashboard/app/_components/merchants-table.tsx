"use client";

import Link from "next/link";
import { useState } from "react";
import { missingFor, type RunDigest } from "@/lib/finding";
import { LastResult } from "./last-result";
import { RailStrip } from "./rail-strip";
import { EmptyState } from "./ui/empty-state";
import { IconSearch } from "./ui/icons";
import { Table, Td, Th } from "./ui/table";

export type MerchantRow = {
  id: string;
  name: string;
  createdAt: string;
  ready: string[];
  partial: string[];
  known: boolean;
  /** The newest stored run, read by the page; null when there is none. */
  digest: RunDigest | null;
};

/** The merchants list, searchable in the browser. Nothing here runs a check. */
export function MerchantsTable({ rows }: { rows: MerchantRow[] }) {
  const [q, setQ] = useState("");
  const term = q.trim().toLowerCase();
  const shown = rows.filter((m) => !term || m.name.toLowerCase().includes(term) || m.id.toLowerCase().includes(term));
  const ready = rows.filter((m) => m.known && missingFor(m).length === 0).length;
  const withFindings = rows.filter((m) => m.digest?.ok && m.digest.open > 0).length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-ink-soft">
          {rows.length} merchant{rows.length === 1 ? "" : "s"} · {ready} ready to check
          {withFindings > 0 && <> · <span className="text-ink">{withFindings} with findings</span></>}
        </p>
        <label className="relative">
          <span className="sr-only">Search merchants</span>
          <IconSearch className="pointer-events-none absolute top-1/2 left-2.5 h-4 w-4 -translate-y-1/2 text-ink-soft" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or id" className="field w-56 py-2 pl-8" />
        </label>
      </div>
      {shown.length === 0 ? (
        <EmptyState title="No merchant matches">Try part of the name, or the ten_ id.</EmptyState>
      ) : (
        <Table label="Merchants" minWidth={760}>
          <thead>
            <tr>
              <Th>Name</Th>
              <Th>ID</Th>
              <Th>Rails</Th>
              <Th>Last run</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {shown.map((m) => (
              <tr key={m.id} className="hover:bg-canvas/60">
                <Td>
                  <Link href={`/merchants/${encodeURIComponent(m.id)}`} className="font-medium underline-offset-4 hover:underline">
                    {m.name}
                  </Link>
                  <span className="block text-xs text-ink-soft" suppressHydrationWarning>
                    Added {new Date(m.createdAt).toLocaleDateString("en-US", { dateStyle: "medium" })}
                  </span>
                </Td>
                <Td><code className="font-mono text-xs text-ink-soft">{m.id}</code></Td>
                <Td><RailStrip ready={m.ready} partial={m.partial} known={m.known} size="sm" labels /></Td>
                <Td><LastResult ready={m.ready} known={m.known} digest={m.digest} /></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </div>
  );
}
