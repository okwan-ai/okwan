import { api, sameOrigin, session } from "@/lib/api";
import { WEBHOOK_ID } from "@/lib/webhooks";
import { fail, json } from "@/lib/respond";

/** Delete one of the signed-in account's addresses, its secret and what
 *  waits to be sent to it. Another account's address reads as missing. */
export async function DELETE(req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const { id } = await params;
  if (!WEBHOOK_ID.test(id)) return fail("no such webhook", 404);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const r = await api(`/v1/webhooks/${id}`, { method: "DELETE", session: token });
  return r.ok ? json({ ok: true }) : fail(r.detail, r.status);
}
