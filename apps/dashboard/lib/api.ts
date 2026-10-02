import "server-only";
import { cookies, headers as incoming } from "next/headers";

/**
 * The dashboard's only route to the Okwan API. Server-side by construction:
 * the session token lives in an httpOnly cookie the browser cannot read,
 * and every call that carries it is made from here.
 */

export const SESSION_COOKIE = "okwan_session";
const SESSION_MAX_AGE = 60 * 60 * 24 * 7; // matches the API's SESSION_TTL

export function apiUrl(): string {
  return (process.env.OKWAN_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
}

export type ApiResult<T> = { ok: true; status: number; data: T } | {
  ok: false;
  status: number;
  detail: string;
};

export async function api<T>(
  path: string,
  init: { method?: string; body?: unknown; session?: string | null } = {},
): Promise<ApiResult<T>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(await forwardedClient()),
  };
  if (init.session) headers.Authorization = `Bearer ${init.session}`;
  let res: Response;
  try {
    res = await fetch(`${apiUrl()}${path}`, {
      method: init.method ?? "GET",
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
    });
  } catch {
    return { ok: false, status: 502, detail: "the Okwan API is unreachable" };
  }
  if (res.status === 204) return { ok: true, status: 204, data: undefined as T };
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, status: res.status, detail: describe(payload) };
  return { ok: true, status: res.status, data: payload as T };
}

/**
 * Every call reaches the API from this server's address, so the API's
 * per-IP limits would see one client. Forward the browser's address, with
 * the shared secret that makes the API believe it (okwan_api/ratelimit.py).
 * Without the secret the header is ignored, so leaving it unset in dev is
 * harmless.
 *
 * The browser's address is taken OKWAN_DASHBOARD_PROXY_HOPS entries from the
 * right of X-Forwarded-For. Separate from the API's OKWAN_TRUSTED_PROXY_HOPS:
 * the two services do not see the same chain (§11). The default of 1 is the
 * value that produced 10.30.203.20 on 2026-10-02 and is NOT known to be right.
 * It stays until the diagnostic below shows what this service receives.
 *
 * Note what "X-Forwarded-For" is here: Next.js fills the header with the
 * socket's remote address when the request arrived without one
 * (base-server.js, `??=`), so a single entry may be Next's, not a proxy's.
 */
async function forwardedClient(): Promise<Record<string, string>> {
  const secret = process.env.OKWAN_DASHBOARD_SECRET;
  const h = await incoming();
  const raw = h.get("x-forwarded-for");
  const hops = Number(process.env.OKWAN_DASHBOARD_PROXY_HOPS ?? "1");
  const chain = (raw ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const ip = hops > 0 && chain.length >= hops ? chain[chain.length - hops] : "";
  const out: Record<string, string> =
    secret && ip ? { "X-Okwan-Client-IP": ip, "X-Okwan-Dashboard-Secret": secret } : {};
  if (process.env.OKWAN_LOG_FORWARDED === "1") {
    logForwarded(raw, chain, h, hops, out["X-Okwan-Client-IP"] ?? null, Boolean(secret));
  }
  return out;
}

// TEMPORARY DIAGNOSTIC — remove with the API's (§10 item 1). One line per
// API call: the raw X-Forwarded-For string as this route handler sees it,
// the parsed chain, the hop count, the address passed on (never the secret,
// only whether one is configured), and the two client addresses Cloudflare
// sets independently of the chain. Addresses only: no other header, cookie
// or body.
function logForwarded(
  raw: string | null, chain: string[], h: Headers, hops: number,
  passed: string | null, secretConfigured: boolean,
): void {
  console.info(
    "okwan_dashboard.forwarded " +
      JSON.stringify({
        raw_x_forwarded_for: raw,
        chain,
        hops,
        passes_on: passed,
        secret_configured: secretConfigured,
        cf_connecting_ip: h.get("cf-connecting-ip"),
        true_client_ip: h.get("true-client-ip"),
      }),
  );
}

/** FastAPI errors are a string or a list of {loc, msg}. Never an input value:
 * the API's 422 handler strips those before they leave. */
function describe(payload: { detail?: unknown }): string {
  const d = payload?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((e: { loc?: unknown[]; msg?: string }) =>
        `${(e.loc ?? []).slice(1).join(".")}: ${e.msg ?? "invalid"}`)
      .join("; ");
  }
  return "request failed";
}

export async function session(): Promise<string | null> {
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export async function setSession(token: string): Promise<void> {
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
}

export async function clearSession(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}

export type Me = { id: string; name: string };

/** The signed-in tenant, or null when the session is missing or expired. */
export async function me(): Promise<Me | null> {
  const token = await session();
  if (!token) return null;
  const r = await api<{ self: Me }>("/v1/tenants", { session: token });
  return r.ok ? r.data.self : null;
}

/** Same-origin check for mutating route handlers. SameSite=Lax already keeps
 * the cookie off cross-site POSTs; this refuses them outright as well. */
export function sameOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return false;
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}
