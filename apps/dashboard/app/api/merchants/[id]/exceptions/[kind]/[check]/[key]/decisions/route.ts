import { api, session } from "@/lib/api";
import type { ApiDecision } from "@/lib/exceptions";
import { fail, json, readBody } from "@/lib/respond";

/**
 * A person's decision about one open item, and the item's history (C58).
 *
 * The merchant id passes through as given; the API's subtree guard answers,
 * so a foreign or unknown id is its 404. The other parts are checked here
 * against the shapes the API makes them in, so nothing else reaches it: a
 * kind, a check's name, an item's 24-hex key. The browser makes the
 * Idempotency-Key once per decision and sends it again on a retry; it is
 * the one header passed on. The body is rebuilt from the fields a decision
 * has, never forwarded as sent: a name only when the item is given to
 * someone, and the terms the person was shown, which the API holds the
 * decision to; and the suggestion it is made from, when it is (C60), by its
 * id's shape. Signed in only, JSON only: a decision is recorded as the
 * signed-in account's.
 */

const KINDS = new Set(["pair", "across"]);
const STATUSES = new Set(["open", "assigned", "explained", "resolved"]);
const CHECK = /^[a-z][a-z0-9_]{0,63}$/;
const KEY = /^[0-9a-f]{24}$/;
const IDEMPOTENCY = /^[A-Za-z0-9._:-]{1,128}$/;
const OWNER_MAX = 80;
const NOTE_MAX = 1000;
const SEEN_MAX = 200;
const SUGGESTION = /^sug_[0-9a-f]{16}$/;

type Params = { params: Promise<{ id: string; kind: string; check: string; key: string }> };

function path(id: string, kind: string, check: string, key: string): string | null {
  if (!KINDS.has(kind) || !CHECK.test(check) || !KEY.test(key)) return null;
  return `/v1/tenants/${encodeURIComponent(id)}/exceptions/${kind}/${check}/${key}/decisions`;
}

function text(value: unknown, max: number): string | undefined | null {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string" || Array.from(value).length > max) return null;
  return value;
}

export async function POST(req: Request, { params }: Params) {
  if (!(req.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) {
    return fail("send the decision as JSON", 415);
  }
  const body = await readBody<{ status?: unknown; owner?: unknown; note?: unknown; seen?: unknown;
                                suggestion?: unknown } | null>(req);
  if (body instanceof Response) return body;
  if (body === null || typeof body !== "object" || Array.isArray(body)) return fail("expected a decision", 400);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, kind, check, key } = await params;
  const to = path(id, kind, check, key);
  if (!to) return fail("no such item", 404);
  const idem = req.headers.get("idempotency-key") ?? "";
  if (!IDEMPOTENCY.test(idem)) return fail("the decision has no Idempotency-Key", 400);
  const status = typeof body.status === "string" && STATUSES.has(body.status) ? body.status : null;
  const owner = text(body.owner, OWNER_MAX);
  const note = text(body.note, NOTE_MAX);
  const seen = text(body.seen, SEEN_MAX);
  const suggestion = body.suggestion === undefined || body.suggestion === null ? undefined
    : typeof body.suggestion === "string" && SUGGESTION.test(body.suggestion) ? body.suggestion : null;
  if (!status || owner === null || note === null || seen === null || suggestion === null) {
    return fail("not a decision Okwan can record", 422);
  }
  const r = await api<{ decision: ApiDecision; replayed: boolean }>(to, {
    method: "POST", session: token, idempotencyKey: idem,
    body: { status, owner: status === "assigned" ? owner : undefined, note, seen, suggestion },
  });
  return r.ok ? json(r.data, r.status) : fail(r.detail, r.status);
}

export async function GET(_req: Request, { params }: Params) {
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, kind, check, key } = await params;
  const to = path(id, kind, check, key);
  if (!to) return fail("no such item", 404);
  const r = await api<{ data: ApiDecision[]; status: string }>(to, { session: token });
  return r.ok ? json(r.data) : fail(r.detail, r.status);
}
