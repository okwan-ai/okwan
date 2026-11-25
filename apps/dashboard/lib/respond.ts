import "server-only";
import { NextResponse } from "next/server";
import { sameOrigin, staleShown } from "./api";

export function json(data: unknown, status = 200) {
  return NextResponse.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

export function fail(detail: string, status: number) {
  return json({ detail }, status);
}

/** What a body may hold before it is refused (413): a change's fields are
 *  a few hundred bytes. A route that takes a file passes its own limit. */
export const BODY_MAX = 64 * 1024;

/** Guard for every mutating handler: same-origin, declared JSON (415) and
 *  JSON (400), and no bigger than `max` (413), counted as it arrives: a body
 *  is never read whole before it is known to fit, signed in or not. */
export async function readBody<T>(req: Request, max: number = BODY_MAX, { anyMode = false } = {}): Promise<T | Response> {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  // A page shown in the other mode (another tab switched): nothing it sends
  // is acted on, wherever the route would act (lib/api.ts, PAGE_MODE_HEADER).
  const stale = anyMode ? null : await staleShown(req);
  if (stale) return fail(stale, 409);
  // JSON only, declared as such: a form or a text/plain body (what a page
  // elsewhere can send without asking first) is refused before it is read.
  const type = (req.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase();
  if (type !== "application/json") return fail("send the body as application/json", 415);
  const declared = Number(req.headers.get("content-length") ?? "0");
  if (!Number.isFinite(declared) || declared > max) return fail("the request is too large", 413);
  const reader = req.body?.getReader();
  if (!reader) return fail("expected a JSON body", 400);
  const parts: Uint8Array[] = [];
  let size = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > max) {
      await reader.cancel().catch(() => {});
      return fail("the request is too large", 413);
    }
    parts.push(value);
  }
  const whole = new Uint8Array(size);
  let at = 0;
  for (const p of parts) { whole.set(p, at); at += p.byteLength; }
  try {
    return JSON.parse(new TextDecoder().decode(whole)) as T;
  } catch {
    return fail("expected a JSON body", 400);
  }
}
