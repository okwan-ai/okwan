import { api, sameOrigin, session } from "@/lib/api";
import { fail, json } from "@/lib/respond";

/** Ask the API for Stripe's customer portal for the signed-in account:
 *  change card, see invoices, cancel. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const r = await api<{ url: string }>("/v1/billing/portal", { method: "POST", session: token });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
