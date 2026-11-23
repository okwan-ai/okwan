import { api, sameOrigin, session } from "@/lib/api";
import { WEBHOOK_ID } from "@/lib/webhooks";
import { fail, json } from "@/lib/respond";

/** `enable`: turn an address back on after Okwan turned it off. `test`: send
 *  it a signed ping now and say what it answered. Nothing else. */
const ACTIONS = new Set(["enable", "test"]);

export async function POST(req: Request, { params }: { params: Promise<{ id: string; action: string }> }) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const { id, action } = await params;
  if (!WEBHOOK_ID.test(id) || !ACTIONS.has(action)) return fail("no such webhook", 404);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const r = await api(`/v1/webhooks/${id}/${action}`, { method: "POST", session: token });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
