import { api, sameOrigin, session } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/** Save a column mapping under a name, replacing one of the same name. */
export async function PUT(req: Request, { params }: { params: Promise<{ id: string; name: string }> }) {
  const body = await readBody<Record<string, unknown>>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, name } = await params;
  const r = await api<Record<string, unknown>>(
    `/v1/tenants/${encodeURIComponent(id)}/import-layouts/${encodeURIComponent(name)}`,
    { method: "PUT", session: token, body },
  );
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}

/** Delete a saved mapping. Files imported with it keep their rows. */
export async function DELETE(req: Request, { params }: { params: Promise<{ id: string; name: string }> }) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, name } = await params;
  const r = await api(`/v1/tenants/${encodeURIComponent(id)}/import-layouts/${encodeURIComponent(name)}`, {
    method: "DELETE",
    session: token,
  });
  return r.ok ? json({ deleted: name }) : fail(r.detail, r.status);
}
