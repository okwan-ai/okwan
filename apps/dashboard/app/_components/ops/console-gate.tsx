import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { requireTenant } from "@/lib/guard";
import { operatorMe, type OperatorMe } from "@/lib/ops";
import { PageHeader } from "../ui/page-header";
import { LockButton, UnlockForm } from "./console-actions";

export const CONSOLE_TITLE = "Console";
export const CONSOLE_DESCRIPTION = "Every customer's support requests, across all accounts. Only you can open this page.";

/**
 * The console's three doors, in order: signed in, on the allowlist (else
 * the page does not exist), and unlocked with an authenticator code.
 * `children` renders only behind all three.
 */
export async function ConsoleGate({ header, children }: {
  header?: (op: OperatorMe, lock: ReactNode) => ReactNode;
  children: (op: OperatorMe) => ReactNode;
}) {
  await requireTenant();
  const op = await operatorMe();
  if (!op) notFound();
  if (!op.enrolled) {
    return (
      <>
        <PageHeader title={CONSOLE_TITLE} description={CONSOLE_DESCRIPTION} />
        <div className="max-w-xl rounded-xl border border-line bg-surface p-6 text-sm">
          <h2 className="text-lg font-semibold tracking-tight">Set up your authenticator app first</h2>
          <p className="mt-2 text-ink-soft">The console needs a code from an authenticator app. In the Codespace, run:</p>
          <pre className="mt-3 overflow-x-auto rounded-lg bg-night px-4 py-3 font-mono text-xs text-canvas">
            python -m okwan_vault operator enroll {op.email}
          </pre>
          <p className="mt-3 text-ink-soft">Then open the file it names, add the key to your app, delete the file, and reload this page.</p>
        </div>
      </>
    );
  }
  if (!op.unlocked_until) {
    return (
      <>
        <PageHeader title={CONSOLE_TITLE} description={CONSOLE_DESCRIPTION} />
        <UnlockForm email={op.email} />
      </>
    );
  }
  return (
    <>
      {header?.(op, <LockButton />)}
      {children(op)}
    </>
  );
}

export function until(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) + " UTC";
}
