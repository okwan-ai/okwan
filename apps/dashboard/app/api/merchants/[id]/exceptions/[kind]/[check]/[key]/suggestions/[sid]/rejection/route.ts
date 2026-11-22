import { api, session } from "@/lib/api";
import type { ApiSuggestion } from "@/lib/exceptions";
import { itemPath } from "@/lib/item-path";
import { fail, json, readBody } from "@/lib/respond";

/**
 * Turn one of Okwan's suggestions down, with why if the person says (C60).
 * The suggestion id is checked against the shape the API makes it in, and
 * the body is rebuilt from the one field it has.
 */

const SUGGESTION = /^sug_[0-9a-f]{16}$/;
const NOTE_MAX = 1000;

type Params = { params: Promise<{ id: string; kind: string; check: string; key: string; sid: string }> };

export async function POST(req: Request, { params }: Params) {
  if (!(req.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) {
    return fail("send it as JSON", 415);
  }
  const body = await readBody<{ note?: unknown } | null>(req);
  if (body instanceof Response) return body;
  if (body === null || typeof body !== "object" || Array.isArray(body)) return fail("expected an answer", 400);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const { id, kind, check, key, sid } = await params;
  const base = itemPath(id, kind, check, key, "suggestions");
  if (!base || !SUGGESTION.test(sid)) return fail("no such suggestion", 404);
  const note = body.note === undefined || body.note === null ? undefined : body.note;
  if (note !== undefined && (typeof note !== "string" || Array.from(note).length > NOTE_MAX)) {
    return fail("not a reason Okwan can record", 422);
  }
  const r = await api<{ suggestion: ApiSuggestion }>(`${base}/${sid}/rejection`, {
    method: "POST", session: token, body: { note: note?.trim() || undefined },
  });
  return r.ok ? json(r.data, r.status) : fail(r.detail, r.status);
}
