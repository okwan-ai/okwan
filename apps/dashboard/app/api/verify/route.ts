import { api, setSession } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

export async function POST(req: Request) {
  const body = await readBody<{ token?: string; password?: string }>(req);
  if (body instanceof Response) return body;
  const r = await api<{ session: string }>("/v1/signup/verify", {
    method: "POST",
    body: { token: body.token ?? "", password: body.password ?? "" },
  });
  if (!r.ok) return fail(r.detail, r.status);
  await setSession(r.data.session);
  return json({ ok: true }, 201);
}
