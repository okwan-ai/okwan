import { api, session } from "@/lib/api";
import { fail, json, readBody } from "@/lib/respond";

/** The longest address the API keeps. */
const URL_MAX = 2048;

/** Add an address for the signed-in account's events. The answer holds its
 *  signing secret, shown this once; the API checks the address (HTTPS,
 *  port 443, a public host) and keeps five an account at most. */
export async function POST(req: Request) {
  const body = await readBody<{ url?: unknown } | null>(req);
  if (body instanceof Response) return body;
  if (body === null || typeof body !== "object" || Array.isArray(body)) return fail("expected a JSON object", 400);
  const url = typeof body.url === "string" ? body.url.trim() : "";
  if (!url) return fail("give the address to send events to", 422);
  if (url.length > URL_MAX) return fail(`an address is at most ${URL_MAX} characters`, 422);
  if (!/^https:\/\//i.test(url)) return fail("an address starts with https://: events are sent over HTTPS only", 422);
  const token = await session();
  if (!token) return fail("signed out — sign in again", 401);
  const r = await api("/v1/webhooks", { method: "POST", session: token, body: { url } });
  return r.ok ? json(r.data, 201) : fail(r.detail, r.status);
}
