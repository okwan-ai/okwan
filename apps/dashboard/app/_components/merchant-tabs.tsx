"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { FOLD_READS } from "@/lib/finding";
import { MERCHANT_TABS, tabOf } from "@/lib/merchant-tabs";
import { useMerchantRun } from "./merchant-run";
import { type TabItem, Tabs } from "./ui/tabs";

/** Findings, Connections and Files, each href carrying ?tab= so a bare
 *  URL's default (Connections until ready) never hides which tab a link
 *  means. `connected` counts the systems a check reads that are connected;
 *  `files` the merchant's imported files. Under /import, Files is active. */
export function MerchantTabs({ connected, files, findingsBadge }: {
  connected: number;
  files: number;
  findingsBadge?: TabItem["badge"];
}) {
  const path = usePathname();
  const params = useSearchParams();
  const { defaultTab } = useMerchantRun();
  // The merchant's own path, whatever page under it is open.
  const base = path.split("/").slice(0, 3).join("/");
  const importing = path !== base;
  const active = importing ? "files" : tabOf(params.get("tab"), defaultTab, Boolean(params.get("order") || params.get("run")));
  const all = connected >= FOLD_READS.length;
  const badge = (id: string): TabItem["badge"] => {
    if (id === "connections") {
      return all
        ? { text: "✓", tone: "ok", label: "all three connected" }
        : { text: `${connected}/${FOLD_READS.length}`, tone: "neutral", label: "of Shopify, PayPal and Stripe connected" };
    }
    if (id === "files") return files > 0 ? { text: String(files), tone: "neutral", label: "imported files" } : undefined;
    return findingsBadge;
  };
  return (
    <Tabs
      label="Merchant sections"
      items={MERCHANT_TABS.map((t) => ({
        href: `${base}?tab=${t.id}`,
        label: t.label,
        active: t.id === active,
        badge: badge(t.id),
      }))}
    />
  );
}
