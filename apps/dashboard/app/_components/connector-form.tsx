"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { nowReadableAs } from "@/lib/connector-meta";
import { railLabel } from "@/lib/finding";
import { markArrived, reportTest, type TestResult } from "@/lib/tab-results";
import { Button } from "./ui/button";
import { Card, CardFooter, CardHeader } from "./ui/card";
import { IconChevron } from "./ui/icons";

/** A connector as one tenant sees it: its declaration (GET /v1/connectors)
 *  plus the credential field names that tenant has stored, never values. */
export type ConnectorView = {
  name: string;
  version?: string;
  description: string;
  /** Resource → its operations, as the SDK declares them. */
  resources?: Record<string, string[]>;
  credential_fields: string[];
  probe: string | null;
  /** SQL tables this connector generates ("stripe.charges"). */
  sql_tables?: string[];
  /** Operations that are not read-only ("messages.send_text"). */
  writes?: string[];
  stored: string[];
};

/** What a save told the sheet: the test's status, whether every field is
 *  now stored, and whether this save is the one that connected it. */
export type SheetResult = { status: TestResult["status"]; complete: boolean; arrived: boolean };

/** The save connected the system (or confirmed it again): every field is
 *  stored and the test read worked, came back empty, or can't run blind. */
export function isSuccess(r: Pick<SheetResult, "status" | "complete">): boolean {
  return r.complete && (r.status === "rows" || r.status === "empty" || r.status === "untestable");
}

// Identifiers, not secrets: shown as typed. Everything else is masked.
const PLAIN = /_domain$/;

/**
 * Test and Save for one connector. `tenantId` targets a merchant's tenant;
 * without it, the signed-in tenant.
 *
 * The connect moment (OKWAN_PROJECT.md §9 2026-10-05): a save that leaves
 * every field stored and whose test read works is a success. The first
 * success on a system that wasn't connected marks it arrived, so each
 * surface showing it pops once; `onResult` tells the sheet. A success folds
 * the form into "Update credentials", shows what the system is now
 * readable as, and puts `next` (the sheet's "Connect {next}" or "Done") in
 * its place. A failed read stores the fields all the same: no pop, the
 * scrubbed error, and Test and Save stays the main action.
 */
export function ConnectorForm({ c, tenantId, tenantKey, onResult, next, autoFocus = false }: {
  c: ConnectorView;
  tenantId?: string;
  tenantKey: string;
  onResult?: (r: SheetResult) => void;
  /** The action that follows a success, rendered in the form's place. */
  next?: ReactNode;
  /** Focus the first field on mount (the sheet moved on to this connector). */
  autoFocus?: boolean;
}) {
  const router = useRouter();
  const label = railLabel(c.name);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  // Spoken outcome. Mounted empty with the form, so the first message is heard.
  const [said, setSaid] = useState("");
  // Field names this form has saved, until the refreshed props carry them.
  const saved = useRef(new Set<string>());
  const nextBox = useRef<HTMLDivElement>(null);
  const submitButton = useRef<HTMLButtonElement>(null);
  const [succeeded, setSucceeded] = useState(0);
  // The busy button disabled itself and dropped focus; put it back.
  const refocus = () => requestAnimationFrame(() => submitButton.current?.focus());

  // After a success the form folds away, taking the focused button with it:
  // land on what comes next.
  useEffect(() => {
    if (succeeded) nextBox.current?.querySelector<HTMLElement>("button,a[href]")?.focus();
  }, [succeeded]);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const el = e.currentTarget;
    const form = new FormData(el);
    const fields = Object.fromEntries(c.credential_fields.map((f) => [f, String(form.get(f) ?? "")]));
    const has = (f: string) => c.stored.includes(f) || saved.current.has(f);
    const before = c.credential_fields.every(has);
    const after = c.credential_fields.every((f) => fields[f].trim() !== "" || has(f));
    // Values leave the page now; nothing on the client keeps them.
    el.reset();
    setBusy(true);
    setError(null);
    setResult(null);
    setSaid(`Testing ${label}…`);
    const res = await fetch(`/api/connections/${c.name}${tenantQuery(tenantId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields }),
    }).catch(() => null);
    const data = res ? await res.json().catch(() => ({})) : {};
    setBusy(false);
    if (!res || !res.ok) {
      setSaid("");
      setError(data.detail ?? "something went wrong");
      return refocus();
    }
    for (const f of c.credential_fields) if (fields[f].trim() !== "") saved.current.add(f);
    const r = data as TestResult;
    if (r.status === "missing") {
      // Nothing to test yet: say which fields, leave the tiles as they are.
      setSaid("");
      setError(`Every field is needed to test. ${r.detail.charAt(0).toUpperCase()}${r.detail.slice(1)}.`);
      refocus();
      // The fields that were filled in are stored all the same.
      return router.refresh();
    }
    const success = isSuccess({ status: r.status, complete: after });
    const arrived = success && !before;
    reportTest(tenantKey, c.name, r);
    if (arrived) markArrived(tenantKey, c.name);
    onResult?.({ status: r.status, complete: after, arrived });
    setResult(r);
    setDone(success);
    setSaid(spoken(label, r, success, c.probe));
    if (success) setSucceeded((n) => n + 1);
    else refocus();
    router.refresh();
  }

  // A connector that declares writes is not "only read": WhatsApp's two
  // sends are mounted on the hosted REST (OKWAN_PROJECT.md §9 2026-10-04),
  // so its sheet names them instead of claiming read-only.
  const writes = c.writes ?? [];
  const readOnly = (
    <p className="text-xs text-ink-soft">
      {writes.length === 0 ? (
        <>Okwan only reads this account. It can&apos;t refund, charge or move money.</>
      ) : (
        <>
          Okwan reads this account and can call its write operations:{" "}
          {writes.map((w, i) => (
            <Fragment key={w}>
              {i > 0 && ", "}
              <code className="font-mono">{w}</code>
            </Fragment>
          ))}
          . It can&apos;t refund, charge or move money.
        </>
      )}
    </p>
  );
  const fieldsForm = (
    <form onSubmit={submit} className="space-y-4" autoComplete="off">
      {c.credential_fields.map((f, i) => (
        <label key={f} className="block space-y-1.5">
          <span className="flex flex-wrap items-center justify-between gap-x-2 text-sm">
            <code className="font-mono text-[13px]">{f}</code>
            {c.stored.includes(f) && (
              <span className="text-xs text-ink-soft">stored · leave blank to keep</span>
            )}
          </span>
          <input
            name={f}
            type={PLAIN.test(f) ? "text" : "password"}
            autoComplete="off"
            spellCheck={false}
            autoFocus={autoFocus && i === 0}
            className="field font-mono"
            placeholder={c.stored.includes(f) ? "••••••••" : ""}
          />
        </label>
      ))}

      {/* The sheet's one volt until a success hands it to what comes next. */}
      <Button ref={submitButton} type="submit" variant={done ? "secondary" : "primary"} disabled={busy} aria-busy={busy} className="w-full">
        {busy ? "Testing…" : "Test and Save"}
      </Button>
      {!done && readOnly}
    </form>
  );
  const alert = error && (
    <p role="alert" className="mt-4 rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>
  );

  return (
    <div>
      <p role="status" aria-live="polite" className="sr-only">{said}</p>
      {done ? (
        <>
          {result && <TestOutcome result={result} />}
          <NowReadableAs c={c} />
          <div ref={nextBox} className="mt-4 space-y-2">
            {next}
            {readOnly}
          </div>
          <Disclosure label="Update credentials" className="mt-4">
            {fieldsForm}
            {alert}
          </Disclosure>
        </>
      ) : (
        <>
          {fieldsForm}
          {alert}
          {result && <TestOutcome result={result} className="mt-4" />}
        </>
      )}
      <Disclosure label="Technical details" className={done ? "" : "mt-4"}>
        <div className="space-y-2 text-xs text-ink-soft">
          <p className="break-words">{c.description}</p>
          <p>
            {c.probe ? (
              <>Test read: <code className="font-mono">{c.probe}</code></>
            ) : (
              "No list call can run without your own IDs, so there's no live test."
            )}
          </p>
        </div>
      </Disclosure>
    </div>
  );
}

/** What the live region says once a save comes back. */
function spoken(label: string, r: TestResult, success: boolean, probe: string | null): string {
  if (r.status === "failed") return `${label} saved, but the test read failed: ${r.detail}`;
  if (!success) return `${label} saved, but not every field is stored yet.`;
  if (r.status === "untestable") return `${label} saved. It can't be tested without your own IDs.`;
  if (r.status === "empty") return `${label} connected. Read works; the list is empty.`;
  const n = r.rows ?? 0;
  const op = r.operation ?? probe;
  return `${label} connected. Read works: ${n} row${n === 1 ? "" : "s"}${op ? ` from ${op}` : " came back"}.`;
}

/**
 * Where a newly connected system can now be read, one row per surface,
 * derived from its declaration (lib/connector-meta.ts nowReadableAs). Okwan's
 * own ending to a connect: one declaration, three surfaces, read-only.
 */
function NowReadableAs({ c }: { c: ConnectorView }) {
  const r = nowReadableAs(c);
  if (!r) return null;
  const label = railLabel(c.name);
  const row = (name: string, value: ReactNode) => (
    <div className="grid grid-cols-[3rem_minmax(0,1fr)] gap-3 px-5 py-2.5 text-sm">
      <dt className="text-ink-soft">{name}</dt>
      <dd className="min-w-0 break-words">{value}</dd>
    </div>
  );
  const code = (s: string) => <code className="font-mono text-[13px]">{s}</code>;
  return (
    <Card className="mt-4" aria-labelledby="now-readable-as">
      <CardHeader as="h3" id="now-readable-as" title="Now readable as" />
      <dl className="divide-y divide-line">
        {row("REST", code(r.rest))}
        {r.sql && row("SQL", code(r.sql))}
        {row(
          "MCP",
          <>
            {r.hosted && <>{code(r.hosted)} <span className="text-ink-soft">(hosted)</span> · </>}
            {code(r.sdk)} <span className="text-ink-soft">(SDK)</span>
          </>,
        )}
      </dl>
      <CardFooter>
        <p>{r.sql ? "All three from one declaration." : "Both from one declaration."}</p>
        <Link
          href={`/integrations/${encodeURIComponent(c.name)}`}
          className="inline-flex min-h-11 items-center text-sm font-medium text-ink underline underline-offset-4"
        >
          See everything {label} exposes <span aria-hidden>&nbsp;→</span>
        </Link>
      </CardFooter>
    </Card>
  );
}

/** A folded section of the sheet, in the style of "How this works". */
function Disclosure({ label, className = "", children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <details className={`group ${className}`}>
      <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-1 text-sm text-ink-soft underline-offset-4 hover:text-ink hover:underline [&::-webkit-details-marker]:hidden">
        {label}
        <IconChevron className="h-4 w-4 transition-transform group-open:rotate-90" />
      </summary>
      <div className="mt-2 pb-1">{children}</div>
    </details>
  );
}

export const TEST_LABEL: Record<TestResult["status"], { symbol: string; label: string; tone: "ok" | "warn" | "danger" | "neutral" }> = {
  rows: { symbol: "✓", label: "Read works", tone: "ok" },
  empty: { symbol: "✓", label: "Works · list empty", tone: "warn" },
  failed: { symbol: "!", label: "Read failed", tone: "danger" },
  missing: { symbol: "○", label: "Fields missing", tone: "neutral" },
  untestable: { symbol: "–", label: "Saved, not tested", tone: "neutral" },
};

const TONE_BOX = {
  ok: "border-ok/30 bg-ok-soft text-ok",
  warn: "border-ink-soft/50 bg-surface text-ink",
  danger: "border-danger/30 bg-danger-soft text-danger",
  neutral: "border-line bg-canvas text-ink-soft",
};

/** The test's outcome as a box. Not a live region: the form's own status
 *  line speaks it, once. */
function TestOutcome({ result, className = "" }: { result: TestResult; className?: string }) {
  const t = TEST_LABEL[result.status];
  return (
    <div className={`rounded-lg border px-3 py-2.5 text-sm ${TONE_BOX[t.tone]} ${className}`}>
      <p className="font-medium">
        <span aria-hidden className="mr-1.5 font-mono">{t.symbol}</span>
        {result.status === "rows" ? `${result.rows} real row${result.rows === 1 ? "" : "s"} came back` : t.label}
      </p>
      <p className="mt-0.5 break-words text-ink-soft">
        {result.operation && <code className="font-mono">{result.operation} · </code>}
        {result.detail}
      </p>
    </div>
  );
}

export function tenantQuery(tenantId?: string): string {
  return tenantId ? `?tenant=${encodeURIComponent(tenantId)}` : "";
}
