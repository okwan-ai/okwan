import "server-only";
import { api } from "./api";
import { type AcrossPage, type Finding, toFinding } from "./finding";

/**
 * The sample check (okwan_api/sample.py): the real fold over a made-up
 * store's week. It needs no session and no key, and nothing of this
 * account's is read. Null when the API didn't answer.
 */
export async function sampleCheck(): Promise<{ finding: Finding; store: string } | null> {
  const r = await api<AcrossPage & { store: string }>("/v1/sample/reconciliations/across/rails?trim=true&limit=1000");
  return r.ok ? { finding: toFinding(r.data), store: r.data.store } : null;
}
