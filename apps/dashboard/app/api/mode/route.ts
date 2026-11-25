import { api, session, setTestMode } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/**
 * Test mode on or off. On: the account's sandbox is made, or its week
 * brought back where it is missing or old (POST /v1/sandbox, as the live
 * account), and only then does this browser send its session there. Off:
 * the session is sent to the live account again. Nothing else changes: it
 * is the same session, and the API decides what it reaches on each side.
 */
export async function POST(req: Request) {
  const body = await readBody<{ mode?: unknown }>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out", 401);
  if (body.mode === "live") {
    await setTestMode(false);
    return json({ mode: "live" });
  }
  if (body.mode !== "test") return fail("mode is test or live", 400);
  const r = await api<{ id: string }>("/v1/sandbox", { method: "POST", session: token, live: true });
  if (!r.ok) return fail(r.detail, r.status);
  await setTestMode(true);
  return json({ mode: "test" });
}
