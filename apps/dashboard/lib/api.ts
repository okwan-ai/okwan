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
 * The browser's address, in order of preference:
 * 1. CF-Connecting-IP. Cloudflare sets it to the address it accepted the
 *    connection from and overwrites anything the client sent. It does not
 *    depend on how many hops Render adds, which has differed before.
 * 2. X-Forwarded-For, counted OKWAN_DASHBOARD_PROXY_HOPS from the right.
 *    Observed 2026-10-02: "<client>, <Cloudflare edge>, <Render internal>",
 *    so three. Counted from the right because a client's own entries land to
 *    the left of what a proxy appends; the leftmost entry is whatever the
 *    client chose to send.
 * Both trust that requests reach Render only through Cloudflare. When
 * neither yields an address nothing is forwarded, and the API limits on
 * what it sees.
 */
async function forwardedClient(): Promise<Record<string, string>> {
  const secret = process.env.OKWAN_DASHBOARD_SECRET;
  const h = await incoming();
  const cloudflare = address(h.get("cf-connecting-ip"));
  const hops = Number(process.env.OKWAN_DASHBOARD_PROXY_HOPS ?? "3");
  const chain = (h.get("x-forwarded-for") ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const counted = hops > 0 && chain.length >= hops ? chain[chain.length - hops] : "";
  const ip = cloudflare || counted;
  return secret && ip ? { "X-Okwan-Client-IP": ip, "X-Okwan-Dashboard-Secret": secret } : {};
}

/** An IPv4 or IPv6 literal, or "". A malformed header is not trusted. */
function address(value: string | null): string {
  const v = (value ?? "").trim();
  const v4 = /^(\d{1,3})(\.\d{1,3}){3}$/.test(v);
  const v6 = v.includes(":") && /^[0-9a-fA-F:.]+$/.test(v);
  return v4 || v6 ? v : "";
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
export type Tenant = Me & { parent_id: string | null; created_at: string };

/** The signed-in tenant, or null when the session is missing or expired. */
export async function me(): Promise<Me | null> {
  const token = await session();
  if (!token) return null;
  const r = await api<{ self: Me }>("/v1/tenants", { session: token });
  return r.ok ? r.data.self : null;
}

/**
 * The tenant a route handler acts on: `?tenant=` when given (a merchant),
 * else the signed-in tenant. Not an authorization decision. The id passes
 * through as given, the API's subtree guard answers, and a foreign or
 * unknown id comes back as its 404.
 */
export async function targetTenant(req: Request): Promise<string | null> {
  const id = new URL(req.url).searchParams.get("tenant");
  if (id) return id;
  return (await me())?.id ?? null;
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
