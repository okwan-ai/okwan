"use client";

import Link from "next/link";
import { byOrder } from "@/lib/connector-meta";
import { FOLD_READS, railLabel } from "@/lib/finding";
import { usePop } from "@/lib/tab-results";
import { BrandMark } from "./ui/brand-mark";

/**
 * A tenant's connections as logo tiles: the three a check reads first
 * (FOLD_READS), each in colour when connected and grey on a dashed tile
 * when not, then any other connected system after a hairline. Props are
 * plain data, because a server layout renders it.
 *
 * With `connectBase`, a missing tile is a link that opens its connect
 * sheet on the Connections tab. With `tenantKey`, a tile pops once when
 * that system arrives in this tab (the arrival store, lib/tab-results.tsx).
 * Logos are decorative; the label or sr-only text names each system.
 */
export function RailStrip({
  ready,
  partial,
  known = true,
  size = "sm",
  labels = false,
  connectBase,
  tenantKey,
}: {
  ready: string[];
  partial: string[];
  known?: boolean;
  size?: "sm" | "md";
  labels?: boolean;
  connectBase?: string;
  tenantKey?: string;
}) {
  if (!known) return <span className="text-xs text-ink-soft">Connections unavailable</span>;
  const fold = new Set<string>(FOLD_READS);
  const others = ready.filter((n) => !fold.has(n)).sort(byOrder);
  const item = (name: string) => (
    <RailItem
      key={name}
      name={name}
      state={ready.includes(name) ? "connected" : partial.includes(name) ? "partial" : "missing"}
      size={size}
      labels={labels}
      connectBase={connectBase}
      tenantKey={tenantKey}
    />
  );
  return (
    <ul aria-label="Connections" className="flex flex-wrap items-center gap-x-3 gap-y-2">
      {FOLD_READS.map(item)}
      {others.length > 0 && <li aria-hidden className="h-4 w-px bg-line" />}
      {others.map(item)}
    </ul>
  );
}

const SR: Record<"connected" | "partial" | "missing", string> = {
  connected: "connected",
  partial: "partly connected",
  missing: "not connected",
};

function RailItem({ name, state, size, labels, connectBase, tenantKey }: {
  name: string;
  state: "connected" | "partial" | "missing";
  size: "sm" | "md";
  labels: boolean;
  connectBase?: string;
  tenantKey?: string;
}) {
  const connected = state === "connected";
  const label = railLabel(name);
  const { pop, onAnimationEnd } = usePop("header", tenantKey, name, connected);
  const body = (
    <>
      <BrandMark name={name} label={label} tile size={size === "md" ? 32 : 24} muted={!connected} pop={pop} onAnimationEnd={onAnimationEnd} />
      {labels ? (
        <>
          <span className={connected ? "text-ink" : "text-ink-soft"}>{label}</span>
          <span className="sr-only"> {SR[state]}</span>
        </>
      ) : (
        <span className="sr-only">{label} {SR[state]}</span>
      )}
    </>
  );
  const text = size === "md" ? "text-sm" : "text-xs";
  if (!connected && connectBase) {
    return (
      <li className={`inline-flex items-center ${text}`}>
        <Link
          href={`${connectBase}?tab=connections&connect=${encodeURIComponent(name)}`}
          scroll={false}
          aria-label={`Connect ${label}`}
          className="-mx-1 inline-flex min-h-11 items-center gap-1.5 rounded-lg px-1 hover:bg-ink/5"
        >
          {body}
        </Link>
      </li>
    );
  }
  return (
    <li className={`inline-flex items-center gap-1.5 ${text}`} title={labels ? undefined : `${label}: ${SR[state]}`}>
      {body}
    </li>
  );
}
