import { api, session } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/** Unlock the console for this session with the authenticator app's code. */
export async function POST(req: Request) {
  const body = await readBody<{ code?: unknown }>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const code = typeof body.code === "string" ? body.code.replace(/\s/g, "") : "";
  if (!/^\d{6}$/.test(code)) return fail("enter the 6-digit code from your authenticator app", 400);
  const r = await api<{ unlocked_until: string }>("/v1/ops/unlock", { method: "POST", session: token, body: { code } });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
