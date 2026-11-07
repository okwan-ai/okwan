import { api, session } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/**
 * Read a file without storing it: its columns, its first rows and a
 * mapping to start from (POST /v1/tenants/{id}/imports/preview). The same
 * card guard as an import; a card column comes back as its last four
 * digits at most.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const body = await readBody<Record<string, unknown>>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id } = await params;
  const r = await api<Record<string, unknown>>(`/v1/tenants/${encodeURIComponent(id)}/imports/preview`, {
    method: "POST",
    session: token,
    body: { filename: body.filename, content_base64: body.content_base64, header_row: body.header_row ?? 1 },
  });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
