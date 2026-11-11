import { api } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/** Stop the daily check's mail with the token from its link. No session:
 *  the token is the authority, and it can do nothing else. The button on
 *  /unsubscribe posts here, so a link scanner that only opens the page
 *  turns nothing off. */
export async function POST(req: Request) {
  const body = await readBody<{ token?: unknown }>(req);
  if (body instanceof Response) return body;
  const token = typeof body.token === "string" ? body.token : "";
  const r = await api<void>("/v1/unsubscribe", { method: "POST", body: { token } });
  return r.ok ? json({ ok: true }) : fail(r.detail, r.status);
}
