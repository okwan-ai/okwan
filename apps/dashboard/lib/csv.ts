"use client";

import { atStake, type FindingRow, OUTCOME_LABEL, railLabel, sameCurrency } from "./finding";
import { minorToDecimal } from "./money";

/** CSV from rows already in the browser: no request, no run. Amounts are
 *  decimal strings built from minor units without floating point. */
export function downloadFindings(
  rows: (FindingRow & { merchantName: string; merchantId: string; at?: number; partial?: boolean })[],
  name = "findings",
) {
  const head = [
    "merchant", "merchant_id", "order", "outcome", "outcome_code", "currency", "order_total", "taken", "at_stake",
    "at_stake_kind", "taken_by_rail", "rails_collected", "rails_not_ruled_out", "checked_at", "result_complete",
  ];
  const lines = rows.map((r) => {
    const s = atStake(r);
    return [
      r.merchantName,
      r.merchantId,
      r.order,
      OUTCOME_LABEL[r.outcome] ?? r.outcome,
      r.outcome,
      (r.currency ?? "").toUpperCase(),
      minorToDecimal(r.total_minor, r.currency),
      // Blank when rails took another currency: the sum would add unlike units.
      sameCurrency(r) ? minorToDecimal(r.collected_minor, r.currency) : "",
      s ? minorToDecimal(s.minor, r.currency) : "",
      s?.label ?? "",
      r.paid.map((p) => `${railLabel(p.rail)} ${minorToDecimal(p.minor, p.currency ?? r.currency)} ${(p.currency ?? r.currency ?? "").toUpperCase()}`.trim()).join("; "),
      r.collected_on.map(railLabel).join("; "),
      r.unverified.map(railLabel).join("; "),
      r.at ? new Date(r.at).toISOString() : "",
      // false: the merchant's result was cut short, so this list may be incomplete.
      r.partial === undefined ? "" : String(!r.partial),
    ];
  });
  const csv = [head, ...lines].map((row) => row.map(cell).join(",")).join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `okwan-${name}-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Every cell quoted (RFC 4180), so no delimiter a locale might use, "," or
 *  ";", can split a value into a new cell; and a leading quote on anything a
 *  spreadsheet would read as a formula (CSV injection). */
function cell(v: string): string {
  const numeric = /^-?\d+(\.\d+)?$/.test(v);
  const safe = !numeric && /^[=+\-@\t\r]/.test(v) ? `'${v}` : v;
  return `"${safe.replace(/"/g, '""')}"`;
}
