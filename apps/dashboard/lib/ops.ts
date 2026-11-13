import "server-only";
import { cache } from "react";
import { api, session } from "./api";

/**
 * The operator console, read for Okwan's own staff. The API answers 404 to
 * anyone who is not an allowlisted operator, so a customer's dashboard
 * never learns the console exists: `operatorMe()` is null for them, the
 * sidebar shows nothing, and /ops is a 404.
 */

export type OperatorMe = { email: string; enrolled: boolean; unlocked_until: string | null };

export type TicketRow = {
  id: string;
  severity: "sev1" | "sev2" | "sev3";
  category: string;
  category_label: string;
  issue: string;
  status: "open" | "answered" | "resolved";
  notified: boolean;
  created_at: string;
  resolved_at: string | null;
  tenant_id: string;
  account: string;
  contact: string | null;
};

export type Brief = { findings?: string; likely_cause?: string; resolution?: string; draft_reply?: string };
export type Line = { from: "customer" | "assistant" | "tool" | "note"; text: string };
export type SentReply = { id: string; operator: string; body: string; sent: boolean; created_at: string };
export type TicketDetail = TicketRow & { brief: Brief; conversation: Line[]; replies: SentReply[] };

export const STATUS_FILTERS = ["open", "answered", "resolved", "all"] as const;
export type StatusFilter = (typeof STATUS_FILTERS)[number];

/** One call per render, shared by the layout and the page. */
export const operatorMe = cache(async (): Promise<OperatorMe | null> => {
  const token = await session();
  if (!token) return null;
  const r = await api<OperatorMe>("/v1/ops/me", { session: token });
  return r.ok ? r.data : null;
});

export async function tickets(status: StatusFilter) {
  return api<{ data: TicketRow[] }>(`/v1/ops/tickets?status=${status}`, { session: await session() });
}

export async function ticket(id: string) {
  return api<TicketDetail>(`/v1/ops/tickets/${encodeURIComponent(id)}`, { session: await session() });
}

export const TICKET_ID = /^tkt_[0-9a-f]{16}$/;
export const TENANT_ID = /^ten_[0-9a-f]{16}$/;

export type RunBrief = {
  id: string; kind: string; name: string; surface: string;
  status: "ok" | "failed"; finished_at: string; error: string | null;
};

export type AccountRow = {
  id: string;
  name: string;
  created_at: string;
  contact: string | null;
  plan: { name: string; limit: number; used: number; unmetered: boolean };
  merchants: number;
  connections: string[];
  active_keys: number;
  last_run: RunBrief | null;
  open_tickets: number;
};

export type Member = {
  id: string; name: string; is_account: boolean; created_at: string;
  /** connector → the credential field names saved. Never values. */
  connections: Record<string, string[]>;
  last_run: RunBrief | null;
};

export type AccountDetail = AccountRow & {
  usage_by_surface: Record<string, number>;
  month_start: string;
  members: Member[];
  keys: { id: string; tenant_id: string; prefix: string; issued: string; revoked: string | null }[];
  runs: (RunBrief & { tenant_id: string })[];
  tickets: TicketRow[];
};

export type Switch = { name: string; state: string; ok: boolean | null; needs: string };
export type Activity = { at: string; operator: string; action: string; target: string | null; detail: Record<string, unknown> | null };

export type Health = {
  service: { commit: string | null; started_at: string; vault: { kind: "memory" | "postgres"; ms: number | null } };
  switches: Switch[];
  runs: {
    days: number; ok: number; failed: number;
    failures: (RunBrief & { tenant_id: string; tenant: string; account_id: string; account: string })[];
  };
  not_mailed: TicketRow[];
  near_limit: { id: string; name: string; plan: string; used: number; limit: number }[];
  accounts: number;
  merchants: number;
  open_tickets: number;
  activity: Activity[];
};

export async function health() {
  return api<Health>("/v1/ops/health", { session: await session() });
}

export function planLine(p: AccountRow["plan"]): string {
  return p.unmetered
    ? `${p.name} · ${p.used.toLocaleString("en-US")} this month, unmetered`
    : `${p.name} · ${p.used.toLocaleString("en-US")} of ${p.limit.toLocaleString("en-US")}`;
}

export async function accounts() {
  return api<{ data: AccountRow[] }>("/v1/ops/accounts", { session: await session() });
}

export async function account(id: string) {
  return api<AccountDetail>(`/v1/ops/accounts/${encodeURIComponent(id)}`, { session: await session() });
}

/** A disputed payment: a dispute, the inquiry before one, or a bank's early
 *  warning of fraud. `matched` false: its charge is not a payment Okwan has
 *  on record, so it may be another product's on the same Stripe account. */
export type DisputeRow = {
  id: string;
  kind: "dispute" | "warning";
  status: string;
  stage: "inquiry" | "open" | "appeal" | "ended";
  reason: string | null;
  amount: string;
  charge_id: string;
  opened_at: string;
  due_by: string | null;
  days_left: number | null;
  needs_answer: boolean;
  /** An answer is with the bank. */
  submitted: boolean;
  /** One was sent at an earlier stage, and the bank is waiting again. */
  answered_before: boolean;
  /** Evidence saved in Stripe and not sent: still waiting for an answer. */
  saved: boolean;
  matched: boolean;
  account: { id: string; name: string } | null;
};
export type DisputeActivity = {
  days: number;
  payments: number;
  disputes: number;
  inquiries: number;
  warnings: number;
  unmatched: number;
  rate: string | null;
  with_warnings: string | null;
  said: string;
  lines: { at: string; whose: string; of: string; over: boolean }[];
};
export type EvidenceField = { text: string | null; add: string | null };
export type DisputeDetail = DisputeRow & {
  stripe_url: string;
  evidence: {
    matched: boolean;
    reason: { code: string | null; says: string; show: string };
    for_this_reason: string[];
    fields: Record<string, EvidenceField>;
    attach: { field: string; what: string }[];
    characters: number;
    policy_version: string | null;
  };
};
export const DISPUTE_ID = /^[A-Za-z0-9_]{1,255}$/;

export async function disputes() {
  return api<{ activity: DisputeActivity; data: DisputeRow[] }>("/v1/ops/disputes", { session: await session() });
}

export async function dispute(id: string) {
  return api<DisputeDetail>(`/v1/ops/disputes/${encodeURIComponent(id)}`, { session: await session() });
}

/**
 * A ticket's issue as a title. The assistant writes one sentence now, but
 * tickets from before briefs carry a paragraph: those are cut to their
 * first sentence, or at a word, and the page shows the full text below.
 */
export function headline(issue: string, max = 120): string {
  const text = issue.trim();
  if (text.length <= max) return text;
  const first = /^(.+?[.!?])\s/.exec(text)?.[1];
  if (first && first.length <= max) return first;
  const cut = text.slice(0, max - 1);
  return `${cut.slice(0, cut.lastIndexOf(" ") > 60 ? cut.lastIndexOf(" ") : cut.length)}…`;
}
