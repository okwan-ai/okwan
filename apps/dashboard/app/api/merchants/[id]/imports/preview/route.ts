import { api, sameOrigin, session } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/** An 8 MB file as base64, and its mapping. */
const IMPORT_BODY_MAX = 12 * 1024 * 1024;

/**
 * Read a file without storing it: its columns, its first rows and a
 * mapping to start from (POST /v1/tenants/{id}/imports/preview). The same
 * card guard as an import; a card column comes back as its last four
 * digits at most.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  // Signed in before a file-sized body is read.
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const body = await readBody<Record<string, unknown>>(req, IMPORT_BODY_MAX);
  if (body instanceof Response) return body;
  const { id } = await params;
  const r = await api<Record<string, unknown>>(`/v1/tenants/${encodeURIComponent(id)}/imports/preview`, {
    method: "POST",
    session: token,
    body: { filename: body.filename, content_base64: body.content_base64, header_row: body.header_row ?? 1 },
  });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
