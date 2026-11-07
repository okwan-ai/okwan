import { api, sameOrigin, session } from "@/lib/api";
import { fail, json } from "@/lib/respond";

/** Delete an imported file and every row it produced. */
export async function DELETE(req: Request, { params }: { params: Promise<{ id: string; file: string }> }) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, file } = await params;
  const r = await api(`/v1/tenants/${encodeURIComponent(id)}/imports/${encodeURIComponent(file)}`, {
    method: "DELETE",
    session: token,
  });
  return r.ok ? json({ deleted: file }) : fail(r.detail, r.status);
}
