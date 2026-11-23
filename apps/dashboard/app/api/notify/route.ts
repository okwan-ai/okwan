import { api, me, session } from "@/lib/api";
import type { NotifySettings } from "@/lib/notify";
import { fail, json, readBody } from "@/lib/respond";

/** A whole number within the range, or undefined: anything else is left out. */
function within(value: unknown, min: number, max: number): number | undefined {
  return Number.isSafeInteger(value) && (value as number) >= min && (value as number) <= max ? (value as number) : undefined;
}

type NotifyBody = { daily_checks?: unknown; finding_emails?: unknown; aging_days?: unknown; large_amount?: unknown };

/** Turn the account's daily check, or only its mail, on or off, or change
 *  the days and amount the mail goes by. Always the signed-in account's
 *  own: there is no id to pass. */
export async function PUT(req: Request) {
  const body = await readBody<NotifyBody | null>(req);
  if (body instanceof Response) return body;
  if (body === null || typeof body !== "object" || Array.isArray(body)) return fail("expected a JSON object", 400);
  const [token, account] = await Promise.all([session(), me()]);
  if (!token || !account) return fail("signed out — sign in again", 401);
  const change: { daily_checks?: boolean; finding_emails?: boolean; aging_days?: number; large_amount?: number } = {};
  if (typeof body.daily_checks === "boolean") change.daily_checks = body.daily_checks;
  if (typeof body.finding_emails === "boolean") change.finding_emails = body.finding_emails;
  const days = within(body.aging_days, 1, 365);
  const amount = within(body.large_amount, 0, 1_000_000_000_000);
  if (days !== undefined) change.aging_days = days;
  if (amount !== undefined) change.large_amount = amount;
  if (Object.keys(change).length === 0) return fail("nothing to change", 422);
  const r = await api<NotifySettings>(`/v1/tenants/${encodeURIComponent(account.id)}/notify`, {
    method: "PUT", session: token, body: change,
  });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
