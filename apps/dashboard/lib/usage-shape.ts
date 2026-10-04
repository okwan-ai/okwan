/**
 * The usage meter as the API reports it, and the shapes the pages draw.
 * Pure: no fetch, no React, so client components can use it too.
 */
export type UsageBucket = { tenant_id: string; hour: string; surface: string; requests: number };

export type Usage = {
  tenant_id: string;
  plan: {
    name: string;
    limit: number;
    used: number;
    remaining: number | null;
    unmetered: boolean;
    month_start: string;
  };
  window: { since: string; days: number; granularity?: "hour" | "day" };
  buckets: UsageBucket[];
};

/** A surface ("mcp:reconcile", "rest:stripe", "test:paypal") read as the
 *  channel a customer recognises. Order is the order lists show. The
 *  prefixes mirror where the API meters: dashboard:* (okwan_api/admin.py),
 *  mcp:* (okwan_query/mcp_http.py), rest:* (okwan_query/rest.py and
 *  okwan_recon/emitters/rest.py), test:* (admin.py meter_test). */
export const CHANNELS: { key: string; label: string; test: (s: string) => boolean }[] = [
  { key: "agents", label: "Agents over MCP", test: (s) => s.startsWith("mcp:") },
  { key: "rest", label: "REST and SQL", test: (s) => s.startsWith("rest:") },
  { key: "dashboard", label: "Dashboard checks", test: (s) => s.startsWith("dashboard:") },
  { key: "tests", label: "Connection tests", test: (s) => s.startsWith("test:") },
];

export function channelOf(surface: string): { key: string; label: string } {
  return CHANNELS.find((c) => c.test(surface)) ?? { key: "other", label: surface };
}

/** The UTC day of an ISO hour: "2026-10-03". */
function dayOf(iso: string): string {
  return iso.slice(0, 10);
}

export type Day = { day: string; total: number; byChannel: Record<string, number> };

/** One entry per day of the window, oldest first, zeros kept, in UTC. */
export function dailyTotals(u: Usage, now = new Date()): Day[] {
  const days: Day[] = [];
  const end = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  for (let i = u.window.days - 1; i >= 0; i--) {
    const d = new Date(end - i * 86_400_000).toISOString().slice(0, 10);
    days.push({ day: d, total: 0, byChannel: {} });
  }
  const index = new Map(days.map((d) => [d.day, d]));
  for (const b of u.buckets) {
    const d = index.get(dayOf(b.hour));
    if (!d) continue;
    d.total += b.requests;
    const c = channelOf(b.surface).key;
    d.byChannel[c] = (d.byChannel[c] ?? 0) + b.requests;
  }
  return days;
}

export type Ranked = { key: string; label: string; requests: number; share: number };

/** Requests per channel in the window, largest first. */
export function byChannel(u: Usage): Ranked[] {
  const sums = new Map<string, number>();
  for (const b of u.buckets) {
    const c = channelOf(b.surface).key;
    sums.set(c, (sums.get(c) ?? 0) + b.requests);
  }
  return rank([...sums].map(([key, requests]) => ({ key, label: CHANNELS.find((c) => c.key === key)?.label ?? key, requests })));
}

/** Requests per tenant in the window, largest first; names from the caller. */
export function byTenant(u: Usage, names: Record<string, string>, selfId: string): Ranked[] {
  const sums = new Map<string, number>();
  for (const b of u.buckets) sums.set(b.tenant_id, (sums.get(b.tenant_id) ?? 0) + b.requests);
  return rank([...sums].map(([key, requests]) => ({
    key,
    label: key === selfId ? "This workspace" : names[key] ?? key,
    requests,
  })));
}

function rank(rows: { key: string; label: string; requests: number }[]): Ranked[] {
  const total = rows.reduce((n, r) => n + r.requests, 0) || 1;
  return rows
    .sort((a, b) => b.requests - a.requests || a.label.localeCompare(b.label))
    .map((r) => ({ ...r, share: r.requests / total }));
}

/** Requests today (UTC) across the window's buckets. */
export function today(u: Usage, now = new Date()): number {
  const d = now.toISOString().slice(0, 10);
  return u.buckets.filter((b) => dayOf(b.hour) === d).reduce((n, b) => n + b.requests, 0);
}

/** Where the month stands against the plan: a share, and the state a
 *  meter should show. Unmetered plans have no share. */
export function planState(p: Usage["plan"]): { share: number | null; state: "ok" | "warn" | "danger"; text: string } {
  if (p.unmetered) return { share: null, state: "ok", text: `${p.used.toLocaleString("en-US")} requests this month · unmetered` };
  const share = p.limit ? Math.min(p.used / p.limit, 1) : 1;
  const state = share >= 1 ? "danger" : share >= 0.8 ? "warn" : "ok";
  const remaining = (p.remaining ?? 0).toLocaleString("en-US");
  return {
    share,
    state,
    text: state === "danger"
      ? `Limit reached: ${p.used.toLocaleString("en-US")} of ${p.limit.toLocaleString("en-US")} this month`
      : `${p.used.toLocaleString("en-US")} of ${p.limit.toLocaleString("en-US")} this month · ${remaining} left`,
  };
}

/** When the month's allowance resets, from the API's month start: the first
 *  of the next month, 00:00 UTC. "Nov 1, 2026, 00:00 UTC". */
export function resetAfter(monthStart: string): string {
  const d = new Date(monthStart);
  const next = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1));
  return `${next.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })}, 00:00 UTC`;
}
