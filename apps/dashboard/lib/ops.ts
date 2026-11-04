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
