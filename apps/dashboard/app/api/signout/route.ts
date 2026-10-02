import { api, clearSession, session } from "@/lib/api";
import { fail, json } from "@/lib/respond";
import { sameOrigin } from "@/lib/api";

export async function POST(req: Request) {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  const token = await session();
  if (token) await api("/v1/sessions/current", { method: "DELETE", session: token });
  await clearSession();
  return json({ ok: true });
}
