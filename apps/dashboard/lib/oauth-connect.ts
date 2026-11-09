import "server-only";

/**
 * Connect buttons for systems with no key to type (Gusto). The person goes
 * to the system to approve, and comes back to /integrations/<name>/callback.
 * Between the two, a short-lived httpOnly cookie holds the state the API
 * issued, the tenant, and where to return, so a code that arrives in a
 * browser that didn't start the connection connects nothing.
 */
export const OAUTH_CONNECTORS = new Set(["gusto"]);
export const CONNECT_COOKIE = "okwan_connect";
export const CONNECT_COOKIE_PATH = "/integrations";
/** Gusto's own code lives ten minutes; so does the API's state. */
export const CONNECT_MAX_AGE = 600;

export type Pending = { s: string; t: string; b: string };

export function readPending(raw: string | undefined): Pending | null {
  if (!raw) return null;
  try {
    const p = JSON.parse(raw) as Partial<Pending>;
    return typeof p.s === "string" && typeof p.t === "string" && typeof p.b === "string" && safeBack(p.b) === p.b
      ? (p as Pending)
      : null;
  } catch {
    return null;
  }
}

/** A path inside the dashboard, or the integrations page. Never another site. */
export function safeBack(path: string | null | undefined): string {
  return path && path.startsWith("/") && !path.startsWith("//") && !path.includes("\\") ? path : "/integrations";
}

/** The dashboard's own address for a redirect, as the browser sees it. */
export function publicUrl(req: Request, path: string): URL {
  const url = new URL(req.url);
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? url.host;
  const proto = req.headers.get("x-forwarded-proto") ?? url.protocol.replace(":", "");
  return new URL(path, `${proto}://${host}`);
}
