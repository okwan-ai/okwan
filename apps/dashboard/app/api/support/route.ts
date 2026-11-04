import { api, session } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

export type SupportReply = {
  conversation_id: string;
  reply: string;
  /** A ticket id when the assistant handed this to a person. */
  ticket: string | null;
  /** What was removed from the message as a secret, if anything. */
  removed: string[];
  /** The message as kept, with anything secret replaced. */
  message: string;
};

/** One message to Okwan's support assistant. The session stays server-side;
 * the API decides what the assistant may read (only this account). */
export async function POST(req: Request) {
  const body = await readBody<{ message?: unknown; conversation_id?: unknown }>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  if (typeof body.message !== "string" || !body.message.trim()) return fail("write a message first", 400);
  const conversation =
    typeof body.conversation_id === "string" && /^sup_[0-9a-f]{16}$/.test(body.conversation_id)
      ? body.conversation_id
      : undefined;
  const r = await api<SupportReply>("/v1/support/messages", {
    method: "POST",
    session: token,
    body: { message: body.message.slice(0, 4000), conversation_id: conversation },
  });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
