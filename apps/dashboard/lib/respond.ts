import "server-only";
import { NextResponse } from "next/server";
import { sameOrigin } from "./api";

export function json(data: unknown, status = 200) {
  return NextResponse.json(data, { status, headers: { "Cache-Control": "no-store" } });
}

export function fail(detail: string, status: number) {
  return json({ detail }, status);
}

/** Guard for every mutating handler: same-origin, JSON body or 400. */
export async function readBody<T>(req: Request): Promise<T | Response> {
  if (!sameOrigin(req)) return fail("cross-origin request refused", 403);
  try {
    return (await req.json()) as T;
  } catch {
    return fail("expected a JSON body", 400);
  }
}
