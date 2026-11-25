import "server-only";
import { cookies, headers as incoming } from "next/headers";
import { cache } from "react";

/**
 * The dashboard's only route to the Okwan API. Server-side by construction:
 * the session token lives in an httpOnly cookie the browser cannot read,
 * and every call that carries it is made from here.
 */

export const SESSION_COOKIE = "okwan_session";
const SESSION_MAX_AGE = 60 * 60 * 12; // matches the API's SESSION_TTL (12 hours)
/**
 * Test mode: the signed-in person working in their account's sandbox. Not a
 * credential: the cookie only says which side the session is sent to, as
 * `Okwan-Mode: test`, and the API decides what that session reaches there
 * (the sandbox and nothing live). Signing in or out starts live again.
 */
export const MODE_COOKIE = "okwan_mode";

/**
 * The API version this dashboard is written for (okwan_core/versions.py),
 * named on every call. The API and the dashboard deploy apart, so an API
 * release that adds a version never reshapes what this code reads. It moves
 * to a newer version in a release after the one that adds it to the API; if
 * it is ever ahead of the API serving it (a rollback), the API answers in
 * its own newest version rather than refusing. Webhook addresses added here
 * take the account's version, not this one.
 */
export const API_VERSION = "2026-11-24";

export function apiUrl(): string {
  return (process.env.OKWAN_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
}

export type ApiResult<T> = { ok: true; status: number; data: T } | {
  ok: false;
  status: number;
  detail: string;
  /** Every problem, when the API lists them (an import's mapping). */
  problems?: string[];
};

export async function api<T>(
  path: string,
  init: {
    method?: string; body?: unknown; session?: string | null; idempotencyKey?: string;
    /** The live account whatever the mode: making the sandbox, signing out. */
    live?: boolean;
  } = {},
): Promise<ApiResult<T>> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Okwan-Version": API_VERSION,
    ...(await forwardedClient()),
  };
  if (init.session) {
    headers.Authorization = `Bearer ${init.session}`;
    if (!init.live && (await testMode())) headers["Okwan-Mode"] = "test";
  }
  // One header a change may carry, and only this one: a route cannot set
  // or override any other on its way to the API.
  if (init.idempotencyKey) headers["Idempotency-Key"] = init.idempotencyKey;
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
  if (!res.ok) return { ok: false, status: res.status, detail: describe(payload), problems: problemsOf(payload) };
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

/** FastAPI errors are a string or a list of {loc, msg}, or an object with a
 * message (an import's refusal). Never an input value: the API's 422
 * handler strips those before they leave. */
function describe(payload: { detail?: unknown }): string {
  const d = payload?.detail;
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && !Array.isArray(d) && typeof (d as { message?: unknown }).message === "string") {
    return (d as { message: string }).message;
  }
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
  const jar = await cookies();
  jar.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
  jar.delete(MODE_COOKIE);
}

export async function clearSession(): Promise<void> {
  const jar = await cookies();
  jar.delete(SESSION_COOKIE);
  jar.delete(MODE_COOKIE);
}

/** Whether this browser is in Test mode. */
export async function testMode(): Promise<boolean> {
  return (await cookies()).get(MODE_COOKIE)?.value === "test";
}

export async function setTestMode(on: boolean): Promise<void> {
  const jar = await cookies();
  if (!on) {
    jar.delete(MODE_COOKIE);
    return;
  }
  jar.set(MODE_COOKIE, "test", {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_MAX_AGE,
  });
}

export type Me = { id: string; name: string };
export type Tenant = Me & { parent_id: string | null; created_at: string };

/**
 * The signed-in tenant and its direct children, or null when the session is
 * missing or expired. One API call per render: the shell, the guard and the
 * page all ask, and React's per-request cache answers after the first.
 */
export const tenantTree = cache(async (): Promise<{ self: Tenant; children: Tenant[] } | null> => {
  const token = await session();
  if (!token) return null;
  const r = await api<{ self: Tenant; children: Tenant[] }>("/v1/tenants", { session: token });
  return r.ok ? r.data : null;
});

/** The signed-in tenant, or null when the session is missing or expired. */
export async function me(): Promise<Me | null> {
  const tree = await tenantTree();
  return tree ? { id: tree.self.id, name: tree.self.name } : null;
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

/** The list an import's refusal carries (`detail.problems`), if any. */
function problemsOf(payload: { detail?: unknown }): string[] | undefined {
  const d = payload?.detail as { problems?: unknown } | undefined;
  return d && typeof d === "object" && Array.isArray(d.problems) ? d.problems.map(String) : undefined;
}
