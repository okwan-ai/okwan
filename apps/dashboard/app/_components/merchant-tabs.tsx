"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { FOLD_READS } from "@/lib/finding";
import { MERCHANT_TABS, tabOf } from "@/lib/merchant-tabs";
import { useMerchantRun } from "./merchant-run";
import { type TabItem, Tabs } from "./ui/tabs";

/** Findings and Connections, each href carrying ?tab= so a bare URL's
 *  default (Connections until ready) never hides which tab a link means.
 *  `connected` counts the systems a check reads that are connected. */
export function MerchantTabs({ connected, findingsBadge }: { connected: number; findingsBadge?: TabItem["badge"] }) {
  const path = usePathname();
  const params = useSearchParams();
  const { defaultTab } = useMerchantRun();
  const active = tabOf(params.get("tab"), defaultTab, Boolean(params.get("order") || params.get("run")));
  const all = connected >= FOLD_READS.length;
  return (
    <Tabs
      label="Merchant sections"
      items={MERCHANT_TABS.map((t) => ({
        href: `${path}?tab=${t.id}`,
        label: t.label,
        active: t.id === active,
        badge: t.id === "connections"
          ? all
            ? { text: "✓", tone: "ok", label: "all three connected" }
            : { text: `${connected}/${FOLD_READS.length}`, tone: "neutral", label: "of Shopify, PayPal and Stripe connected" }
          : findingsBadge,
      }))}
    />
  );
}
