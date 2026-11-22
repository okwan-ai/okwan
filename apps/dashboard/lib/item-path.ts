import "server-only";

/** The API's path for one open item's decisions or suggestions, or null
 *  when a part is not the shape the API makes it in: a kind, a check's
 *  name, an item's 24-hex key. The merchant id passes through, encoded:
 *  the API's subtree guard answers for it. */

const KINDS = new Set(["pair", "across"]);
const CHECK = /^[a-z][a-z0-9_]{0,63}$/;
const KEY = /^[0-9a-f]{24}$/;

export function itemPath(id: string, kind: string, check: string, key: string,
                         what: "decisions" | "suggestions"): string | null {
  if (!KINDS.has(kind) || !CHECK.test(check) || !KEY.test(key)) return null;
  return `/v1/tenants/${encodeURIComponent(id)}/exceptions/${kind}/${check}/${key}/${what}`;
}
