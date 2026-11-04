import { api, session } from "@/lib/api";
import { TICKET_ID } from "@/lib/ops";
import { fail, json, readBody } from "@/lib/respond";

/** Resolve or reopen a ticket without sending anything. */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const body = await readBody<{ status?: unknown }>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id } = await params;
  if (!TICKET_ID.test(id)) return fail("no such ticket", 404);
  if (body.status !== "open" && body.status !== "resolved" && body.status !== "answered") {
    return fail("status is open, answered or resolved", 400);
  }
  const r = await api<{ status: string }>(`/v1/ops/tickets/${id}/status`, {
    method: "POST",
    session: token,
    body: { status: body.status },
  });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
