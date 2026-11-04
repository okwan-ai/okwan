import { api, sameOrigin, session } from "@/lib/api";
import { fail, json } from "@/lib/respond";

/** Lock the console again before the 30 minutes are up. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const r = await api("/v1/ops/lock", { method: "POST", session: token });
  return r.ok ? json({ ok: true }) : fail(r.detail, r.status);
}
