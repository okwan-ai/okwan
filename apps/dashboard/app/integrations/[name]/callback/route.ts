import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { api, session } from "@/lib/api";
import {
  CONNECT_COOKIE, CONNECT_COOKIE_PATH, OAUTH_CONNECTORS, publicUrl, readPending,
} from "@/lib/oauth-connect";

type Connected = { connected: boolean; test?: { status?: string; rows?: number; detail?: string } };

/**
 * Where the system sends the person back. The state must match the one
 * this browser started with; the API checks it again against the tenant
 * and the signed-in account, swaps the code for tokens it keeps, and runs
 * the test read. Nothing secret reaches the browser or the result page.
 */
export async function GET(req: Request, { params }: { params: Promise<{ name: string }> }) {
  const { name: connector } = await params;
  if (!OAUTH_CONNECTORS.has(connector)) return new NextResponse("Not found", { status: 404 });
  const url = new URL(req.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const pending = readPending((await cookies()).get(CONNECT_COOKIE)?.value);
  const back = pending?.b ?? "/integrations";

  const finish = (q: Record<string, string>) => {
    const res = NextResponse.redirect(
      publicUrl(req, `/integrations/${connector}/done?${new URLSearchParams({ ...q, back })}`), 303);
    res.cookies.set(CONNECT_COOKIE, "", { path: CONNECT_COOKIE_PATH, maxAge: 0 });
    return res;
  };

  if (url.searchParams.get("error")) return finish({ result: "declined" });
  if (!pending || !code || !state || pending.s !== state) {
    return finish({ result: "error", reason: "This connection wasn't started in this browser, or took over ten minutes." });
  }
  const token = await session();
  if (!token) return NextResponse.redirect(publicUrl(req, "/signup?mode=signin"), 303);
  const r = await api<Connected>(
    `/v1/tenants/${encodeURIComponent(pending.t)}/connectors/${connector}/callback`,
    { method: "POST", session: token, body: { code, state } },
  );
  if (!r.ok) return finish({ result: "error", reason: r.detail });
  const test = r.data.test ?? {};
  return finish({
    result: "connected",
    test: test.status ?? "",
    rows: String(test.rows ?? ""),
    detail: test.detail ?? "",
  });
}
