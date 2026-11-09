import { NextResponse } from "next/server";
import { api, session, targetTenant } from "@/lib/api";
import {
  CONNECT_COOKIE, CONNECT_COOKIE_PATH, CONNECT_MAX_AGE, OAUTH_CONNECTORS, publicUrl,
} from "@/lib/oauth-connect";

/**
 * Start a Connect button: ask the API for the system's approval link (it
 * issues a state bound to this tenant and this signed-in account), keep the
 * state in a short httpOnly cookie, and send the person to approve.
 * `?tenant=` names a merchant; the API's subtree guard decides.
 */
export async function GET(req: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name: connector } = await params;
  if (!OAUTH_CONNECTORS.has(connector)) return new NextResponse("Not found", { status: 404 });
  const token = await session();
  const tenant = await targetTenant(req);
  if (!token || !tenant) return NextResponse.redirect(publicUrl(req, "/signup?mode=signin"), 303);
  const merchant = new URL(req.url).searchParams.get("tenant");
  const back = merchant ? `/merchants/${encodeURIComponent(merchant)}?tab=connections` : "/settings#own-connections";

  const r = await api<{ url: string; state: string }>(
    `/v1/tenants/${encodeURIComponent(tenant)}/connectors/${connector}/authorize`,
    { method: "POST", session: token },
  );
  if (!r.ok) {
    const q = new URLSearchParams({ result: "error", reason: r.detail, back });
    return NextResponse.redirect(publicUrl(req, `/integrations/${connector}/done?${q}`), 303);
  }
  const res = NextResponse.redirect(r.data.url, 303);
  res.cookies.set(CONNECT_COOKIE, JSON.stringify({ s: r.data.state, t: tenant, b: back }), {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    // Lax: the cookie rides the top-level GET back from the system.
    sameSite: "lax",
    path: CONNECT_COOKIE_PATH,
    maxAge: CONNECT_MAX_AGE,
  });
  return res;
}
