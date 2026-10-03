import { api, session } from "@/lib/api";
import { type AcrossPage, toFinding } from "@/lib/finding";
import { fail, json, readBody } from "@/lib/respond";

/**
 * Run an across-rails fold as a merchant. The id passes through as given;
 * the API's subtree guard answers, so a foreign or unknown id is its 404,
 * an exhausted plan its 402, and an upstream failure its status and detail.
 * Nothing is kept: each click is a fresh, metered run.
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string; name: string }> },
) {
  const body = await readBody<object>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, name } = await params;
  const r = await api<AcrossPage>(
    `/v1/tenants/${encodeURIComponent(id)}/reconciliations/across/${encodeURIComponent(name)}?limit=1000`,
    { method: "POST", session: token },
  );
  return r.ok ? json(toFinding(r.data)) : fail(r.detail, r.status);
}
