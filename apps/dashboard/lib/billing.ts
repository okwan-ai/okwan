import "server-only";
import { api, session, testMode } from "./api";

export type Billing = {
  enabled: boolean;
  plan: string;
  pro_price_usd: number;
  subscription: { status: string; cancel_at_period_end: boolean; current_period_end: string | null } | null;
  can_manage: boolean;
  /** The refund policy's own words for the plan page (okwan_api/policy.py). */
  notice: string;
  refunds_url: string;
  terms_url: string;
};

/** The signed-in account's plan and payment state, or null when the API
 *  does not answer, and in Test mode, which never reaches billing. */
export async function billing(): Promise<Billing | null> {
  if (await testMode()) return null;
  const r = await api<Billing>("/v1/billing", { session: await session() });
  return r.ok ? r.data : null;
}
