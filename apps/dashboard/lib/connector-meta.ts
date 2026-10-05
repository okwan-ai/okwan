import { FOLD_READS } from "./finding";

/**
 * What the dashboard knows about a connector beyond its declaration.
 *
 * A dashboard mirror, like FOLD_READS (lib/finding.ts): the API does not yet
 * state a connector's role or where it sits in a merchant's picture, so the
 * order and the role are kept here. It strains the define-once rule and goes
 * when the API exposes them (OKWAN_PROJECT.md §10 item 12). Anything
 * derivable from the declaration itself (routes, tables, tools, writes) is
 * derived below from GET /v1/connectors, never kept here as a constant.
 */

/** The parts of a declaration (GET /v1/connectors) these helpers read. */
export type Declaration = {
  name: string;
  /** Resource → its operations, as the SDK declares them. */
  resources?: Record<string, string[]>;
  /** SQL tables this connector generates ("stripe.charges"). */
  sql_tables?: string[];
  /** Operations that are not read-only ("messages.send_text"). */
  writes?: string[];
  /** The live read a connection test makes ("paypal.transactions.list"). */
  probe?: string | null;
};

/** The order connectors are shown in: the orders, the payments a check
 *  reads, then the rest. A connector not listed sorts after these, by name. */
export const ORDER = ["shopify", "paypal", "stripe", "paystack", "postgres", "whatsapp"] as const;

/** What each connector is, in a word. */
export const ROLE: Record<string, string> = {
  shopify: "Orders",
  paypal: "Payments",
  stripe: "Payments",
  paystack: "Payments",
  postgres: "Database",
  whatsapp: "Messaging",
};

/** Compare two connector names in ORDER, unknown names last and alphabetical. */
export function byOrder(a: string, b: string): number {
  const rank = (n: string) => {
    const i = (ORDER as readonly string[]).indexOf(n);
    return i === -1 ? ORDER.length : i;
  };
  return rank(a) - rank(b) || a.localeCompare(b);
}

/** The check (the `rails` fold) reads this connector. */
export function inCheck(name: string): boolean {
  return (FOLD_READS as readonly string[]).includes(name);
}

/** "Orders", "Payments", "Database"; "Connector" for one not listed. */
export function roleOf(name: string): string {
  return ROLE[name] ?? "Connector";
}

/** "Payments · in the check", "Payments · not in the check yet", "Database". */
export function roleLine(name: string): string {
  const role = roleOf(name);
  if (role === "Orders" || role === "Payments") return `${role} · ${inCheck(name) ? "in the check" : "not in the check yet"}`;
  return role;
}

/** A tenant's view of a connector: the fields it needs and the ones stored
 *  (names only, never values). */
type Stored = { name: string; credential_fields: string[]; stored: string[] };

/** Every credential field the connector needs is stored. */
export function isComplete(c: Omit<Stored, "name">): boolean {
  return c.credential_fields.every((f) => c.stored.includes(f));
}

/** The systems a check reads that this tenant hasn't fully connected, in
 *  FOLD_READS order. Names in `done` count as connected: they just arrived
 *  and the refreshed props may not have landed yet. */
export function missingForCheck<T extends Stored>(list: T[], done: string[] = []): T[] {
  return FOLD_READS.flatMap((n) => list.filter((c) => c.name === n)).filter((c) => !isComplete(c) && !done.includes(c.name));
}

/** "Shopify, PayPal and Stripe". */
export function andList(items: string[]): string {
  if (items.length < 2) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/** What the ✎ badge says for a connector that can change something. */
export function writeLabel(c: Declaration): string {
  if (c.name === "whatsapp") return "Can send messages";
  const n = c.writes?.length ?? 0;
  return `${n} write operation${n === 1 ? "" : "s"}`;
}

/** Sorted in ORDER, unknown connectors last and alphabetical. */
export function sortConnectors<T extends { name: string }>(list: T[]): T[] {
  return [...list].sort((a, b) => byOrder(a.name, b.name));
}

/** The connectors a check reads, then the rest, each in ORDER. */
export function groups<T extends { name: string }>(list: T[]): { check: T[]; more: T[] } {
  const sorted = sortConnectors(list);
  return { check: sorted.filter((c) => inCheck(c.name)), more: sorted.filter((c) => !inCheck(c.name)) };
}

/** What one declaration produces: a REST route and an SDK tool per
 *  operation, the SQL tables it generates, and the operations that write. */
export function defineOnce(c: Declaration): { routes: number; tables: number; sdkTools: number; writes: number } {
  const ops = Object.values(c.resources ?? {}).reduce((n, list) => n + list.length, 0);
  return { routes: ops, tables: c.sql_tables?.length ?? 0, sdkTools: ops, writes: c.writes?.length ?? 0 };
}

/** n and the noun, singular when n is 1. */
export function plural(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** "3 REST routes · 2 SQL tables · 3 SDK tools", counted from the declaration. */
export function defineOnceLine(c: Declaration): string {
  const d = defineOnce(c);
  return [plural(d.routes, "REST route"), d.tables ? plural(d.tables, "SQL table") : "no SQL table", plural(d.sdkTools, "SDK tool")].join(" · ");
}

/**
 * How a newly connected system can be read, one line per surface, from its
 * declaration: the operation its connection test makes (or the first one
 * declared when it has no test), the table over that resource (or the first
 * table), the hosted query over it, and the SDK tool for the operation.
 */
export function nowReadableAs(c: Declaration): { rest: string; sql: string | null; hosted: string | null; sdk: string } | null {
  let resource: string | undefined;
  let op: string | undefined;
  const probe = c.probe?.split(".");
  if (probe && probe.length === 3 && probe[0] === c.name) {
    [, resource, op] = probe;
  } else {
    const first = Object.entries(c.resources ?? {}).find(([, ops]) => ops.length > 0);
    if (first) [resource, op] = [first[0], first[1][0]];
  }
  if (!resource || !op) return null;
  const tables = c.sql_tables ?? [];
  const sql = tables.find((t) => t === `${c.name}.${resource}`) ?? tables[0] ?? null;
  return {
    rest: `POST /v1/${c.name}/${resource}/${op}`,
    sql,
    hosted: sql ? `okwan_query over ${sql}` : null,
    sdk: `${c.name}_${resource}_${op}`,
  };
}

/** The first sentence of a docstring, for a one-line description. */
export function firstSentence(text: string): string {
  const t = text.trim();
  const m = t.match(/^.*?[.!?](?=\s|$)/s);
  return m ? m[0] : t;
}
