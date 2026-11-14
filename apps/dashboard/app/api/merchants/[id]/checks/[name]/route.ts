import { api, session } from "@/lib/api";
import { FILE_CHECKS } from "@/lib/file-checks";
import { fail, json, readBody } from "@/lib/respond";

/**
 * Run a file-based check as a merchant: only the checks the Files tab
 * offers, by exact name, so this route reaches no other run. The id passes
 * through as given; the API's subtree guard answers, so a foreign or
 * unknown id is its 404, an exhausted plan its 402, and a failure its
 * status and detail. Each click is a fresh, metered run; the API stores
 * it, and the page reads the stored run back like any other. Nothing of
 * the result travels through here but the stored run's id.
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
  const check = FILE_CHECKS.find((c) => c.name === name);
  if (!check) return fail("no such check", 404);
  const r = await api<{ run_id: string | null }>(
    `/v1/tenants/${encodeURIComponent(id)}/reconciliations/${encodeURIComponent(check.name)}?limit=1`,
    { method: "POST", session: token },
  );
  return r.ok ? json({ run_id: r.data.run_id, at: Date.now() }) : fail(r.detail, r.status);
}
