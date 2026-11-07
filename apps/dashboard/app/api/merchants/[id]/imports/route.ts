import { api, session } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/**
 * Import a file as a merchant: the file (base64) and its mapping, or the
 * name of a saved one, go to POST /v1/tenants/{id}/imports. The API reads
 * it, refuses it whole if any cell holds a card number, and keeps only the
 * mapped fields. Nothing here reads, keeps or logs the file. A refused
 * mapping comes back with every problem listed.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const body = await readBody<Record<string, unknown>>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id } = await params;
  const r = await api<Record<string, unknown>>(`/v1/tenants/${encodeURIComponent(id)}/imports`, {
    method: "POST",
    session: token,
    body: { filename: body.filename, content_base64: body.content_base64, mapping: body.mapping, layout: body.layout },
  });
  return r.ok ? json(r.data, 201) : json({ detail: r.detail, problems: r.problems }, r.status);
}
