import { api, session } from "@/lib/api";
import { fail, json } from "@/lib/respond";

/** Someone is using the dashboard: one read moves the API's idle clock.
 *  401 once the session has ended, so the page signs itself out. */
export async function GET() {
  const token = await session();
  if (!token) return fail("signed out", 401);
  const r = await api("/v1/tenants", { session: token });
  return r.ok ? json({ ok: true }) : fail("signed out", r.status === 401 ? 401 : r.status);
}
