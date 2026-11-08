/**
 * Imported files: what the Files tab and the import page read and say.
 *
 * Everything about a file comes from the API (/v1/tenants/{id}/imports,
 * /import-layouts, /imports/preview): what it holds, which fields a kind
 * has and which are required, and the mapping it suggests from the
 * column names. The words for a kind and a field are the dashboard's own.
 * Generic mapping only: no processor or platform presets until real files
 * arrive (OKWAN_PROJECT.md §9 2026-11-07).
 */

export const KINDS = [
  "settlement_batches", "settlement_transactions", "delivery_lines", "delivery_payouts", "bank_deposits",
  "tax_filings",
] as const;
export type Kind = (typeof KINDS)[number];

/** What each kind is, in a few words, and the file it comes from. */
export const KIND_LABEL: Record<Kind, { short: string; long: string; from: string }> = {
  settlement_batches: { short: "Settlement batches", long: "Batches from a processor's settlement report", from: "Processor batch report" },
  settlement_transactions: { short: "Card transactions", long: "Transactions from a processor's export", from: "Processor transaction export" },
  delivery_lines: { short: "Delivery orders", long: "Orders and charges from a delivery platform's report", from: "DoorDash, Uber Eats or Grubhub transactions" },
  delivery_payouts: { short: "Delivery payouts", long: "Payouts from a delivery platform's report", from: "DoorDash, Uber Eats or Grubhub payouts" },
  bank_deposits: { short: "Bank statement", long: "Lines of a bank statement", from: "Bank statement (CSV, Excel or OFX)" },
  tax_filings: { short: "Tax returns", long: "What each sales tax return reported", from: "Return summary or filing report" },
};

export function kindLabel(kind: string): string {
  return (KIND_LABEL as Record<string, { short: string }>)[kind]?.short ?? kind;
}

/** A field's name as a person reads it: "closed_on" → "Closed on". */
export function fieldLabel(field: string): string {
  const special: Record<string, string> = {
    mid: "Merchant ID (MID)", fitid: "Bank transaction id", card_last4: "Card, last 4 digits",
    account_last4: "Account, last 4 digits", order_ref: "Order or invoice number",
    credit: "Money in", debit: "Money out", auth_code: "Authorization code",
    pos_order_ref: "POS order reference", payout_on: "Payout date", paid_on: "Paid on",
    marketing_minor: "Marketing and promotions", payout_fee_minor: "Instant payout fee",
    jurisdiction: "State or jurisdiction", vendor_discount_minor: "Vendor discount", paid_minor: "Paid",
    return_id: "Return or confirmation number", tax_minor: "Tax reported",
  };
  if (special[field]) return special[field];
  const words = field.replace(/_minor$/, "").replace(/_id$/, "_ID").replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export type ImportFile = {
  id: string;
  kind: Kind;
  filename: string;
  format: "csv" | "xlsx" | "ofx";
  layout: string;
  rows: number;
  skipped: number;
  skipped_rows: number[];
  card_columns: string[];
  first_date: string | null;
  last_date: string | null;
  created_at: string;
  expires_at: string;
  mapping: Mapping;
};

export type Mapping = {
  kind: Kind;
  columns: Record<string, string>;
  currency?: string | null;
  day_first?: boolean;
  decimal_comma?: boolean;
  negate?: string[];
  header_row?: number;
  skip_invalid_rows?: boolean;
  name?: string;
};

export type Layout = { name: string; kind: Kind; mapping: Mapping; created_at: string; updated_at: string };

export type FieldSpec = { field: string; type: string; required: boolean; description: string };

export type Preview = {
  format: "csv" | "xlsx" | "ofx";
  header_row: number;
  header: string[];
  rows: number;
  sample: string[][];
  card_columns: string[];
  fields: Record<Kind, FieldSpec[]>;
  suggested: Record<Kind, Record<string, string>>;
};

/** "2 Sep – 30 Sep 2026", "2 Sep 2026", or "". Dates are days, read as UTC. */
export function span(first: string | null, last: string | null): string {
  if (!first) return "";
  const fmt = (d: string, year: boolean) =>
    new Date(`${d}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", ...(year ? { year: "numeric" } : {}), timeZone: "UTC" });
  if (!last || last === first) return fmt(first, true);
  const sameYear = first.slice(0, 4) === last.slice(0, 4);
  return `${fmt(first, !sameYear)} – ${fmt(last, true)}`;
}

/** "7 Nov 2026" from an ISO instant. */
export function day(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

/** The import routes answer 503 until the owner opens them (OKWAN_IMPORTS_OPEN). */
export const CLOSED_STATUS = 503;
