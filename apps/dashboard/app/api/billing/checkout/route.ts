import { api, sameOrigin, session } from "@/lib/api";
import { fail, json } from "@/lib/respond";

/** Ask the API for a Stripe Checkout page for the signed-in account. The
 *  browser is sent there; the plan changes only when Stripe's signed
 *  webhook says so. */
export async function POST(req: Request) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const r = await api<{ url: string }>("/v1/billing/checkout", { method: "POST", session: token });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
