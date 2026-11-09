import Link from "next/link";
import { type Access, type AccessEntry, alertDetail, alertTitle, isKey, what, when, who } from "@/lib/access-shape";
import { ACCESS_LIMIT } from "@/lib/access";
import { Badge } from "../ui/badge";
import { Card, CardBody, CardFooter, CardHeader } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { RowCard } from "../ui/row-card";
import { Segmented } from "../ui/segmented";
import { Table, Td, Th } from "../ui/table";

const RANGES = [7, 30, 90] as const;

/**
 * Settings › Access log: every read of the workspace's data (or one
 * merchant's), newest first, and the alerts those reads raised. What the
 * API records (okwan_api/access.py) and nothing more: never a key, a
 * query or a result. Server-rendered from GET /v1/tenants/{id}/access.
 */
export function AccessLog({ access, names, selfId, rangeHref }: {
  access: Access;
  /** Merchant ids to names; the workspace itself is `selfId`. */
  names: Record<string, string>;
  selfId: string;
  rangeHref: (days: number) => string;
}) {
  const days = access.window.days;
  const nameOf = (id: string) => (id === selfId ? "Your workspace" : names[id] ?? id);
  const capped = access.entries.length >= ACCESS_LIMIT;
  // A merchant's own log has one tenant; the workspace's names each row's.
  const merchantColumn = Object.keys(names).length > 0 || access.entries.some((e) => e.tenant_id !== selfId);
  const rows = { entries: access.entries, nameOf, merchantColumn };

  return (
    <div className="space-y-6">
      <p className="max-w-3xl text-sm text-ink-soft">
        Every time your data is read, through the API, the MCP server or this dashboard, Okwan records when, which API key
        or a dashboard session, the address it came from, and what was read. Never the key itself, the query or its
        results. Records are kept 90 days. If a key reads from a network it hasn&apos;t used in 30 days, or far more than
        usual, Okwan emails your account.
      </p>

      <Card>
        <CardHeader
          title="Alerts"
          description={access.alerts.length ? "Okwan emails your account when one is raised; each says whether the email went out." : undefined}
          actions={
            <Link href="/settings?tab=keys" className="inline-flex min-h-11 items-center text-sm font-medium underline underline-offset-4">
              Revoke a key →
            </Link>
          }
        />
        {access.alerts.length ? (
          <CardBody list>
            {access.alerts.map((a) => (
              <li key={a.id} className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
                <div className="min-w-0 max-w-2xl">
                  <p className="text-sm font-semibold break-words">{alertTitle(a)}</p>
                  <p className="mt-1 text-sm text-ink-soft break-words">{alertDetail(a)}</p>
                  <p className="mt-1 text-xs text-ink-soft">
                    <span suppressHydrationWarning>{when(a.at)}</span>
                    {a.tenant_id !== selfId && <> · {nameOf(a.tenant_id)}</>}
                  </p>
                </div>
                <Badge tone={a.mailed ? "neutral" : "warn"} symbol={a.mailed ? "✓" : "!"}>
                  {a.mailed ? "Emailed" : "Not emailed"}
                </Badge>
              </li>
            ))}
          </CardBody>
        ) : (
          <CardBody>
            <p className="text-sm text-ink-soft">No alerts in the last {days} days.</p>
          </CardBody>
        )}
      </Card>

      <Card flush>
        <CardHeader
          title="Reads"
          description={access.entries.length ? `Newest first${capped ? `, the latest ${ACCESS_LIMIT}` : ""}.` : undefined}
          actions={
            <Segmented
              label="Window"
              value={String(days)}
              items={RANGES.map((d) => ({ key: String(d), label: `${d} days`, href: rangeHref(d) }))}
            />
          }
        />
        {access.entries.length ? (
          <>
            <div className="hidden sm:block"><ReadsTable {...rows} /></div>
            <ReadsList {...rows} />
          </>
        ) : (
          <CardBody>
            <EmptyState title={`No reads in the last ${days} days`}>
              Reads through the API, the MCP server and this dashboard appear here as they happen.
            </EmptyState>
          </CardBody>
        )}
        <CardFooter>
          Your account can also read this log over the API: <code className="font-mono">GET /v1/tenants/{access.tenant_id}/access</code>
        </CardFooter>
      </Card>
    </div>
  );
}

type Rows = { entries: AccessEntry[]; nameOf: (id: string) => string; merchantColumn: boolean };

function Key({ credential }: { credential: string }) {
  return isKey(credential) ? <code className="font-mono text-[13px]">{who(credential)}</code> : <>{who(credential)}</>;
}

/** From sm up: one row per read. */
function ReadsTable({ entries, nameOf, merchantColumn }: Rows) {
  return (
    <Table flush label="Reads of your data" minWidth={merchantColumn ? 900 : 760}>
      <thead>
        <tr>
          <Th>When</Th>
          {merchantColumn && <Th>Merchant</Th>}
          <Th>Key</Th>
          <Th>From</Th>
          <Th>What was read</Th>
        </tr>
      </thead>
      <tbody>
        {entries.map((e, i) => {
          const read = what(e.surface, e.target);
          return (
            <tr key={`${e.at}-${i}`} className="border-b border-line last:border-0">
              <Td className="whitespace-nowrap tabular-nums"><span suppressHydrationWarning>{when(e.at)}</span></Td>
              {merchantColumn && <Td>{nameOf(e.tenant_id)}</Td>}
              <Td className="whitespace-nowrap"><Key credential={e.credential} /></Td>
              <Td className="whitespace-nowrap">
                <code className="font-mono text-[13px]">{e.ip}</code>
                {e.network !== e.ip && <span className="block text-xs text-ink-soft">{e.network}</span>}
              </Td>
              <Td>
                <span className="block">{read.thing}</span>
                <span className="block text-xs text-ink-soft">{read.channel}</span>
              </Td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

/** Below sm: each read as a card, so the key and the address stay on screen. */
function ReadsList({ entries, nameOf, merchantColumn }: Rows) {
  return (
    <ul aria-label="Reads of your data" className="divide-y divide-line sm:hidden">
      {entries.map((e, i) => {
        const read = what(e.surface, e.target);
        const [day, time] = when(e.at).split(", ");
        return (
          <RowCard
            key={`${e.at}-${i}`}
            title={<span className="font-medium">{read.thing}</span>}
            money={{ value: time, label: day, soft: true }}
            line2={
              <>
                <span className="text-sm"><Key credential={e.credential} /></span>
                {merchantColumn && <span className="text-sm text-ink-soft">{nameOf(e.tenant_id)}</span>}
              </>
            }
            meta={<>From <code className="font-mono">{e.ip}</code>{e.network !== e.ip && ` (${e.network})`} · {read.channel}</>}
          />
        );
      })}
    </ul>
  );
}
