import "server-only";
import { api, session } from "./api";

/** An account's choices about the daily check and its mail (okwan_api/notify.py). */
export type NotifySettings = {
  daily_checks: boolean;
  finding_emails: boolean;
  /** An item open this many days is mailed once more (1 to 365). */
  aging_days: number;
  /** An open item at or above this many whole units of its currency is
   *  named in every mail (0: none). */
  large_amount: number;
  /** Where the mail goes: the account's sign-in address. */
  email: string | null;
  /** Why this server can't send the mail at all, or null. */
  emails_blocked: string | null;
  /** The operator has paused every daily check. */
  paused_by_operator: boolean;
  /** The connectors a merchant needs connected to be checked. */
  needs: string[];
};

export async function notifySettings(accountId: string): Promise<NotifySettings | null> {
  const r = await api<NotifySettings>(`/v1/tenants/${encodeURIComponent(accountId)}/notify`, { session: await session() });
  return r.ok ? r.data : null;
}
