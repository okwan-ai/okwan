import { api } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

export async function POST(req: Request) {
  const body = await readBody<{ email?: string; password?: string }>(req);
  if (body instanceof Response) return body;
  const r = await api<{ detail: string }>("/v1/signup", {
    method: "POST",
    body: { email: body.email ?? "", password: body.password ?? "" },
  });
  return r.ok ? json(r.data, 202) : fail(r.detail, r.status);
}
