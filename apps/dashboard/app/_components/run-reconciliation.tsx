"use client";

import { useState } from "react";
import { type Coverage, type Finding, type FindingRow, OUTCOME_LABEL, railLabel } from "@/lib/finding";
import { formatMinor } from "@/lib/money";

/** Symbol + label, never colour alone. */
const MARK: Record<string, string> = {
  collected_twice: "×2",
  collected_inconsistent: "≠",
  uncollected: "∅",
  unverifiable: "?",
};

const TONE: Record<string, string> = {
  collected_twice: "bg-red-50 text-red-900 border-red-700/30",
  collected_inconsistent: "bg-volt/15 text-ink border-volt-deep/50",
  uncollected: "bg-volt/15 text-ink border-volt-deep/50",
  unverifiable: "bg-canvas text-ink-soft border-line",
};

export function RunReconciliation({ tenantId, fold = "rails" }: { tenantId: string; fold?: string }) {
  const [busy, setBusy] = useState(false);
  const [finding, setFinding] = useState<Finding | null>(null);
  const [ranAt, setRanAt] = useState<Date | null>(null);
  const [error, setError] = useState<{ status: number; detail: string } | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    const res = await fetch(
      `/api/merchants/${encodeURIComponent(tenantId)}/across/${encodeURIComponent(fold)}`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    ).catch(() => null);
    const data = res ? await res.json().catch(() => ({})) : {};
    setBusy(false);
    if (!res || !res.ok) {
      return setError({ status: res?.status ?? 0, detail: data.detail ?? "the dashboard could not reach the API" });
    }
    setFinding(data as Finding);
    setRanAt(new Date());
  }

  return (
    <section id="run" className="card scroll-mt-8 p-6 sm:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-xl">
          <h2 className="font-display text-3xl font-light tracking-tight">Was every order paid once?</h2>
          <p className="mt-2 text-sm text-ink-soft">
            Reads the order ledger and every connected rail once, then gives one verdict per order. A
            double collection is a clean match on each rail alone, so only this view can see it.
          </p>
        </div>
        <button className="btn btn-primary" onClick={run} disabled={busy}>
          {busy ? "Reading rails…" : finding ? "Run again" : "Run reconciliation"}
        </button>
      </div>

      {busy && (
        <p role="status" className="mt-6 animate-pulse text-sm text-ink-soft">
          Reading the ledger, then each rail. A busy account can take several seconds.
        </p>
      )}

      {error && !busy && (
        <div role="alert" className="mt-6 rounded-lg border border-red-700/30 bg-red-50 px-4 py-3 text-sm text-red-900">
          <p className="font-medium">The run did not finish{error.status ? ` (${error.status})` : ""}</p>
          <p className="mt-0.5 break-words opacity-80">{error.detail}</p>
        </div>
      )}

      {finding && !busy && <Result f={finding} ranAt={ranAt} />}
    </section>
  );
}

function Result({ f, ranAt }: { f: Finding; ranAt: Date | null }) {
  const s = f.summary;
  const paidOnce = s.collected + s.split_tender;
  return (
    <div className="mt-8 space-y-8">
      <Headline f={f} />

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Tile label="Orders read" value={s.orders.toLocaleString("en-US")} />
        <Tile label="Paid once" value={paidOnce.toLocaleString("en-US")} />
        <Tile label="Collected twice" value={s.collected_twice.toLocaleString("en-US")} mark={s.collected_twice ? "×2" : undefined} />
        <Tile label="No payment found" value={s.uncollected.toLocaleString("en-US")} />
        <Tile label="Couldn't verify" value={s.unverifiable.toLocaleString("en-US")} />
        <Tile
          label="Match rate"
          value={s.match_rate === null ? "Withheld" : `${(s.match_rate * 100).toFixed(1)}%`}
          note={s.match_rate === null ? "Some orders could not be checked, so no rate is given." : undefined}
        />
      </dl>

      <CoverageStrip s={s} />

      {f.rows.length > 0 ? (
        <div>
          <h3 className="mb-3 text-sm font-medium">
            Orders to look at <span className="font-normal text-ink-soft">· {f.rows.length}</span>
          </h3>
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="bg-canvas text-xs text-ink-soft">
                <tr>
                  <th className="px-4 py-2.5 font-medium">Order</th>
                  <th className="px-4 py-2.5 text-right font-medium">Amount</th>
                  <th className="px-4 py-2.5 font-medium">Outcome</th>
                  <th className="px-4 py-2.5 font-medium">Rails</th>
                  <th className="px-4 py-2.5 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {f.rows.map((r, i) => (
                  <tr key={`${r.order}-${i}`} className="align-top">
                    <td className="px-4 py-3 font-mono">{r.order}</td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {formatMinor(r.total_minor, r.currency)}
                      {r.outcome === "collected_twice" && r.collected_minor !== null && (
                        <span className="block text-xs text-ink-soft">
                          taken {formatMinor(r.collected_minor, r.currency)}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${TONE[r.outcome] ?? ""}`}>
                        <span aria-hidden>{MARK[r.outcome]}</span>
                        {OUTCOME_LABEL[r.outcome] ?? r.outcome}
                      </span>
                      <code className="mt-1 block font-mono text-[11px] text-ink-soft">{r.outcome}</code>
                    </td>
                    <td className="px-4 py-3">
                      {r.collected_on.map(railLabel).join(" + ") || "—"}
                      {r.unverified.length > 0 && (
                        <span className="block text-xs text-ink-soft">
                          unread: {r.unverified.map(railLabel).join(", ")}
                        </span>
                      )}
                    </td>
                    <td className="max-w-[280px] px-4 py-3 text-xs break-words text-ink-soft" title={r.reason ?? undefined}>
                      {detail(r)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {f.partial && (
            <p className="mt-2 text-xs text-ink-soft">
              The first 1,000 orders are shown. The full result pages over the API and MCP.
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm text-ink-soft">Nothing to look at: every order read was paid once.</p>
      )}

      <p className="border-t border-line pt-4 text-xs text-ink-soft">
        {ranAt && <>Run at {ranAt.toLocaleTimeString("en-US", { timeStyle: "short" })}. Not saved; run again for current data. </>}
        Your agents get the same result from <code className="font-mono">reconcile_across_rails</code> over
        the hosted MCP, with a key issued for this merchant.
      </p>
    </div>
  );
}

function Headline({ f }: { f: Finding }) {
  const s = f.summary;
  if (s.collected_twice > 0) {
    const amount = f.twice_currency !== null
      ? formatMinor(s.collected_twice_minor, f.twice_currency)
      : `${s.collected_twice} orders`;
    return (
      <div className="rounded-xl bg-volt px-6 py-6 text-ink">
        <p className="text-sm font-medium">Collected twice</p>
        <p className="mt-1 text-5xl font-semibold tracking-tight sm:text-6xl">{amount}</p>
        <p className="mt-3 max-w-xl text-sm">
          {s.collected_twice === 1 ? "One order was" : `${s.collected_twice} orders were`} taken in full on more
          than one rail.
          {f.twice_currency !== null && (
            <> {formatMinor(s.overcollected_minor, f.twice_currency)} was taken beyond the order totals and is owed back.</>
          )}
        </p>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-line bg-canvas px-6 py-5">
      <p className="font-display text-2xl">
        {s.unverifiable ? "No double collection in what could be read." : "No order was collected twice."}
      </p>
    </div>
  );
}

/** Collected-twice reads from the per-rail amounts; the API reason
 *  carries raw minor units and stays as the tooltip. */
function detail(r: FindingRow): string {
  if (r.outcome === "collected_twice" && r.paid.length > 0) {
    return r.paid.map((p) => `${railLabel(p.rail)} ${formatMinor(p.minor, p.currency ?? r.currency)}`).join(" · ");
  }
  return r.reason ?? "";
}

function Tile({ label, value, note, mark }: { label: string; value: string; note?: string; mark?: string }) {
  return (
    <div className="rounded-xl border border-line bg-canvas px-4 py-3">
      <dt className="min-h-[2lh] text-xs text-ink-soft">{label}</dt>
      <dd className="mt-1 flex items-baseline gap-1.5 text-2xl font-semibold">
        {value}
        {mark && <span className="text-xs font-medium text-red-800" aria-hidden>{mark}</span>}
      </dd>
      {note && <p className="mt-1 text-[11px] leading-snug text-ink-soft">{note}</p>}
    </div>
  );
}

function CoverageStrip({ s }: { s: Finding["summary"] }) {
  const ledger = s.ledger_coverage?.source.split(".")[0] ?? "ledger";
  const sides: { name: string; cov: Coverage | null; orphans?: number }[] = [
    { name: ledger, cov: s.ledger_coverage },
    ...Object.entries(s.rails).map(([name, r]) => ({
      name: r.coverage?.source.split(".")[0] ?? name,
      cov: r.coverage,
      orphans: r.unmatched_right,
    })),
  ];
  return (
    <div>
      <h3 className="mb-3 text-sm font-medium">What was read</h3>
      <ul className="grid gap-3 sm:grid-cols-3">
        {sides.map((side, i) => (
          <li key={`${side.name}-${i}`} className="rounded-xl border border-line px-4 py-3 text-sm">
            <p className="font-medium">
              {railLabel(side.name)}
              {i === 0 && <span className="ml-1.5 text-xs font-normal text-ink-soft">ledger</span>}
            </p>
            {side.cov ? (
              <>
                <p className="mt-1 text-ink-soft">
                  {side.cov.records.toLocaleString("en-US")} records · {span(side.cov)}
                </p>
                {side.cov.truncated && (
                  <p className="mt-1 text-xs text-red-800">⚠ Cut at {side.cov.cap.toLocaleString("en-US")} records; later ones were not read.</p>
                )}
              </>
            ) : (
              <p className="mt-1 text-ink-soft">No coverage reported</p>
            )}
            {side.orphans ? (
              <p className="mt-1 text-xs text-ink-soft">{side.orphans.toLocaleString("en-US")} {side.orphans === 1 ? "payment" : "payments"} with no{" "}
                {railLabel(ledger)} order (outside this check)</p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function span(c: Coverage): string {
  const d = (iso: string | null) =>
    iso ? new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : null;
  const start = d(c.span_start);
  const end = d(c.span_end);
  if (start && end) return `${start} – ${end}`;
  if (end) return `to ${end}`;
  if (start) return `from ${start}`;
  return "full history";
}
