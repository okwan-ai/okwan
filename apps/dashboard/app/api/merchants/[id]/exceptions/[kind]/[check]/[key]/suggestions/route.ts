import { api, session } from "@/lib/api";
import type { ApiSuggestion } from "@/lib/exceptions";
import { itemPath } from "@/lib/item-path";
import { fail, json, readBody } from "@/lib/respond";

/**
 * Okwan's suggestions on one open item (C60): ask for one, or read the
 * item's. A suggestion is never a decision; a person decides from it, on
 * the decisions route, or turns it down.
 *
 * As the decisions route: the merchant id passes through and the API's
 * subtree guard answers; the other parts are checked against the shapes the
 * API makes them in. Asking sends no body on to the API and no header but
 * the session's, so nothing a browser writes reaches the model's request.
 * Signed in only, and asking only from the dashboard's own pages.
 */

type Params = { params: Promise<{ id: string; kind: string; check: string; key: string }> };

function suggestionsPath(id: string, kind: string, check: string, key: string): string | null {
  return itemPath(id, kind, check, key, "suggestions");
}

export async function POST(req: Request, { params }: Params) {
  if (!(req.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) {
    return fail("ask as JSON", 415);
  }
  const body = await readBody<unknown>(req);
  if (body instanceof Response) return body;
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, kind, check, key } = await params;
  const to = suggestionsPath(id, kind, check, key);
  if (!to) return fail("no such item", 404);
  const r = await api<{ suggestion: ApiSuggestion; reused: boolean }>(to, { method: "POST", session: token });
  return r.ok ? json(r.data, r.status) : fail(r.detail, r.status);
}

export async function GET(_req: Request, { params }: Params) {
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, kind, check, key } = await params;
  const to = suggestionsPath(id, kind, check, key);
  if (!to) return fail("no such item", 404);
  const r = await api<{ data: ApiSuggestion[] }>(to, { session: token });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
