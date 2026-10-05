"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { MERCHANT_TABS, tabOf } from "@/lib/merchant-tabs";
import { Tabs } from "./ui/tabs";

export function MerchantTabs({ rails }: { rails: number }) {
  const path = usePathname();
  const active = tabOf(useSearchParams().get("tab"));
  return (
    <Tabs
      label="Merchant sections"
      items={MERCHANT_TABS.map((t) => ({
        href: t.id === "findings" ? path : `${path}?tab=${t.id}`,
        label: t.label,
        active: t.id === active,
        badge: t.id === "connections" ? { text: String(rails), tone: "neutral", label: "connected" } : undefined,
      }))}
    />
  );
}
