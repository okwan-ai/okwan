"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { RANGES } from "@/lib/ranges";
import { IconCalendar } from "./ui/icons";

/** Which checks the page counts, by when they finished. In the URL, so a
 *  view can be shared and the server renders it. */
export function RangeSelect({ value }: { value: string }) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  return (
    <label className="relative inline-flex">
      <span className="sr-only">Show results from</span>
      <span className="pointer-events-none absolute top-1/2 left-5 -translate-y-1/2 text-ink"><IconCalendar /></span>
      <select
        value={value}
        onChange={(e) => {
          const next = new URLSearchParams(params.toString());
          next.set("days", e.target.value);
          router.push(`${path}?${next.toString()}`, { scroll: false });
        }}
        className="min-h-[46px] min-w-[208px] appearance-none rounded-[14px] border border-white/90 bg-white/90 py-2 pr-12 pl-[58px] text-[16px] font-medium text-ink shadow-float hover:border-accent/30 2xl:text-[17px]"
      >
        {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
      </select>
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="pointer-events-none absolute top-1/2 right-5 -translate-y-1/2">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </label>
  );
}
