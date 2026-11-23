import "server-only";
import { api, session } from "./api";

/** One of the account's webhook addresses, as `GET /v1/webhooks` lists it
 *  (okwan_api/webhooks.py). Never its secret: only the first characters. */
export type WebhookAddress = {
  id: string;
  url: string;
  secret_hint: string;
  enabled: boolean;
  created_at: string;
  created_by: string;
  disabled_at: string | null;
  disabled_why: string | null;
  /** Every delivery since then has failed. */
  failing_since: string | null;
  last_ok_at: string | null;
  /** Deliveries waiting to be sent. */
  pending: number;
};

/** The signed-in account's own addresses, or null when the API did not answer. */
export async function webhookAddresses(): Promise<WebhookAddress[] | null> {
  const r = await api<{ data: WebhookAddress[] }>("/v1/webhooks", { session: await session() });
  return r.ok ? r.data.data : null;
}

/** An address id as the API makes them; anything else never reaches it. */
export const WEBHOOK_ID = /^whe_[0-9a-f]{16}$/;
