import { api, session } from "@/lib/api";
import { TICKET_ID } from "@/lib/ops";
import { fail, json, readBody } from "@/lib/respond";

/** Email a reply to the customer who raised the ticket. The API sends it,
 *  stores it and logs who sent it. */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const body = await readBody<{ body?: unknown; resolve?: unknown }>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id } = await params;
  if (!TICKET_ID.test(id)) return fail("no such ticket", 404);
  if (typeof body.body !== "string" || !body.body.trim()) return fail("write a reply first", 400);
  const r = await api<{ status: string }>(`/v1/ops/tickets/${id}/reply`, {
    method: "POST",
    session: token,
    body: { body: body.body.slice(0, 8000), resolve: body.resolve === true },
  });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
