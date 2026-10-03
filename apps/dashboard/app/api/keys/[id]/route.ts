import { api, sameOrigin, session } from "@/lib/api";
import { fail, json } from "@/lib/respond";

/** Revoke a key by id. The API's subtree guard decides whether the caller
 *  may; a key outside it reads as nonexistent (404), never 403. Effective
 *  on the next request. */
export async function DELETE(req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id } = await params;
  const r = await api(`/v1/tenants/keys/${encodeURIComponent(id)}`, { method: "DELETE", session: token });
  return r.ok ? json({ ok: true }) : fail(r.detail, r.status);
}
