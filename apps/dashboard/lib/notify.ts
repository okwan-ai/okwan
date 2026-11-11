import "server-only";
import { api, session } from "./api";

/** An account's choices about the daily check (okwan_api/notify.py). */
export type NotifySettings = {
  daily_checks: boolean;
  finding_emails: boolean;
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
