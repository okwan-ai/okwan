import Link from "next/link";
import { IconLock } from "./icons";

/**
 * Read-only, said the same way everywhere it is said: a neutral lock pill
 * linking to the Security row that explains it. Never ok-green, so it is
 * not read as a connection status. The pill is small; the link around it
 * keeps a 44px hit area.
 */
export function ReadOnlyChip({ tone = "light" }: { tone?: "light" | "navy" }) {
  return (
    <Link
      href="/settings?tab=security#read-only"
      title="Okwan only reads. It can't refund, charge or move money."
      className={`inline-flex min-h-11 items-center${tone === "navy" ? " focus-visible:outline-canvas" : ""}`}
    >
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${
          tone === "navy" ? "border-canvas/30 bg-transparent text-canvas/80 hover:text-canvas" : "border-line bg-canvas text-ink-soft hover:text-ink"
        }`}
      >
        <IconLock className="h-3 w-3" />
        Read-only
      </span>
    </Link>
  );
}
