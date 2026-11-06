"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { RANGES } from "@/lib/ranges";

/** Which checks the page counts, by when they finished. In the URL, so a
 *  view can be shared and the server renders it. */
export function RangeSelect({ value }: { value: string }) {
  const router = useRouter();
  const path = usePathname();
  const params = useSearchParams();
  return (
    <label className="relative inline-flex">
      <span className="sr-only">Show results from</span>
      <select
        value={value}
        onChange={(e) => {
          const next = new URLSearchParams(params.toString());
          next.set("days", e.target.value);
          router.push(`${path}?${next.toString()}`, { scroll: false });
        }}
        className="min-h-12 appearance-none rounded-2xl border border-white/80 bg-white/90 py-2 pr-11 pl-5 text-[15px] font-semibold text-ink shadow-float hover:border-accent/30"
      >
        {RANGES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
      </select>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="pointer-events-none absolute top-1/2 right-4 -translate-y-1/2">
        <path d="m6 9 6 6 6-6" />
      </svg>
    </label>
  );
}
