import { api, me, session } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/**
 * Test and Save. Each submitted field goes straight to the vault through
 * PUT /v1/tenants/{id}/credentials (put_credential); then the API runs the
 * connector's list operation with limit=1 against what the vault now holds.
 *
 * Values are never logged, never echoed, and dropped when this returns.
 * Blank fields are not sent, so one field can be rotated without retyping
 * the others, and "Test" alone re-checks what is already stored.
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ connector: string }> },
) {
  const body = await readBody<{ fields?: Record<string, unknown> }>(req);
  if (body instanceof Response) return body;
  const token = await session();
  const tenant = await me();
  if (!token || !tenant) return fail("signed out — sign in again", 401);
  const { connector } = await params;

  for (const [field, value] of Object.entries(body.fields ?? {})) {
    if (typeof value !== "string" || value.trim() === "") continue;
    const w = await api(`/v1/tenants/${tenant.id}/credentials`, {
      method: "PUT",
      session: token,
      body: { connector, field, value: value.trim() },
    });
    if (!w.ok) return fail(`${field} was not saved: ${w.detail}`, w.status);
  }

  const t = await api<Record<string, unknown>>(
    `/v1/tenants/${tenant.id}/connectors/${encodeURIComponent(connector)}/test`,
    { method: "POST", session: token },
  );
  return t.ok ? json(t.data) : fail(t.detail, t.status);
}
