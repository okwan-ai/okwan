/**
 * The access log as the API reports it (GET /v1/tenants/{id}/access), and
 * how each row reads. Pure: no fetch, no React.
 */
import { channelOf } from "./usage-shape";

export type AccessEntry = {
  tenant_id: string;
  at: string;
  /** An API key's public prefix (`okw_` and eight characters), "session", or "unknown". */
  credential: string;
  ip: string;
  network: string;
  surface: string;
  target: string;
};

export type AccessAlert = {
  id: string;
  tenant_id: string;
  at: string;
  kind: "new_network" | "volume";
  credential: string;
  detail: {
    ip?: string;
    network?: string;
    surface?: string;
    target?: string;
    at?: string;
    requests?: number;
    busiest?: number;
    hour_to?: string;
  };
  mailed: boolean;
};

export type Access = {
  tenant_id: string;
  window: { since: string; days: number; limit: number };
  entries: AccessEntry[];
  alerts: AccessAlert[];
};

/** "Nov 9, 05:39 UTC": the API's clock, said as such, so every viewer reads the same time. */
export function when(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const time = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" });
  return `${day}, ${time} UTC`;
}

export function isKey(credential: string): boolean {
  return credential.startsWith("okw_");
}

/** Who read: a key by its prefix, or the dashboard. */
export function who(credential: string): string {
  if (isKey(credential)) return credential;
  return credential === "session" ? "Dashboard session" : "Unknown";
}

/** The last path segments of a route, after a fixed prefix. */
function after(target: string, prefix: RegExp): string[] | null {
  const m = target.match(prefix);
  return m ? target.slice(m[0].length).split("/").filter(Boolean) : null;
}

/**
 * What was read, in words: the channel, and the thing in it. The surfaces
 * mirror where the API meters (usage-shape's CHANNELS); the targets are
 * the route, or for the hosted MCP the tool and the check it ran.
 */
export function what(surface: string, target: string): { channel: string; thing: string } {
  const channel = channelOf(surface).label;
  const [kind, rest = ""] = surface.split(":");
  const check = (name?: string) => (name ? `Check: ${name.replaceAll("_", " ")}` : "A check");
  if (rest === "query") return { channel, thing: "SQL query" };
  if (rest === "reconcile" || rest === "across") {
    const name = target.startsWith("okwan_reconcile ") ? target.slice("okwan_reconcile ".length) : target.split("/").pop();
    return { channel, thing: check(name) };
  }
  if (kind === "test") return { channel, thing: `Connection test: ${rest}` };
  if (kind === "rest") {
    const parts = after(target, new RegExp(`^/v1/${rest}/`));
    return { channel, thing: parts && parts[0] ? `${rest}: ${parts[0].replaceAll("_", " ")}` : rest };
  }
  return { channel, thing: target || surface };
}

/** One line saying what an alert was about. */
export function alertTitle(a: AccessAlert): string {
  return a.kind === "new_network"
    ? `Key ${a.credential} read from a new network`
    : `Key ${a.credential} read far more than usual`;
}

export function alertDetail(a: AccessAlert): string {
  const d = a.detail;
  if (a.kind === "new_network") {
    const where = d.ip && d.network ? `${d.ip} (network ${d.network})` : d.network ?? "an unknown address";
    const read = d.surface ? what(d.surface, d.target ?? "").thing : "your data";
    return `From ${where}, reading ${read}. It had not used that network in the last 30 days.`;
  }
  const to = d.hour_to ? ` in the hour to ${when(d.hour_to)}` : "";
  return `${(d.requests ?? 0).toLocaleString("en-US")} reads${to}. Its busiest hour in the week before was ${(d.busiest ?? 0).toLocaleString("en-US")}.`;
}
