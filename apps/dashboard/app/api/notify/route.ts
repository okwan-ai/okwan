import { api, me, session } from "@/lib/api";
import type { NotifySettings } from "@/lib/notify";
import { fail, json, readBody } from "@/lib/respond";

/** Turn the account's daily check, or only its mail, on or off. Always the
 *  signed-in account's own: there is no id to pass. */
export async function PUT(req: Request) {
  const body = await readBody<{ daily_checks?: unknown; finding_emails?: unknown }>(req);
  if (body instanceof Response) return body;
  const [token, account] = await Promise.all([session(), me()]);
  if (!token || !account) return fail("signed out — sign in again", 401);
  const change: { daily_checks?: boolean; finding_emails?: boolean } = {};
  if (typeof body.daily_checks === "boolean") change.daily_checks = body.daily_checks;
  if (typeof body.finding_emails === "boolean") change.finding_emails = body.finding_emails;
  const r = await api<NotifySettings>(`/v1/tenants/${encodeURIComponent(account.id)}/notify`, {
    method: "PUT", session: token, body: change,
  });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
