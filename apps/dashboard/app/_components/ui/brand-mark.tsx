import { useId } from "react";
import { LOGOS } from "./brand-logos";

/**
 * The logo of a connected system, beside its name: never instead of it.
 *
 * Full colour, as its owner draws it (the owner's call, OKWAN_PROJECT.md §9
 * 2026-10-04): a connected PayPal shows PayPal's own mark. On a white tile
 * (`tile`), so a third party's colours sit on neutral ground and never on
 * volt, which stays Okwan's only accent (§2). `muted` draws it in grey for
 * a system not connected yet; it turns to colour when it connects, and
 * `pop` plays the short arrival (off under prefers-reduced-motion).
 *
 * Each logo is its owner's trademark, shown to name the integration it
 * identifies. Whether each owner's brand rules permit this use is a legal
 * item, flagged for the owner (§10 item 25), not something this code
 * decides. Brand pages: Shopify shopify.com/brand-assets · PayPal
 * newsroom.paypal-corp.com/media-resources · Stripe stripe.com/newsroom/brand-assets
 * · PostgreSQL postgresql.org/about/policies/trademarks · WhatsApp
 * about.meta.com/brand/resources/whatsapp · Claude anthropic.com · Cursor cursor.com.
 *
 * A system with no logo here (Paystack today) gets a letter mark, so the
 * row still scans the same way; adding one is one entry in brand-logos.ts.
 * The mark is decorative (`aria-hidden`); the name beside it is the text.
 */

/** Marks for MCP clients, keyed by the recipe id (lib/mcp-clients.ts). */
export const CLIENT_MARK: Record<string, string> = {
  "claude-code": "claude",
  "claude-desktop": "claude",
  cursor: "cursor",
};

/** Connector names that differ from the logo's key. */
const ALIAS: Record<string, string> = { postgres: "postgresql" };

export function hasBrandMark(name: string): boolean {
  return (ALIAS[name] ?? name) in LOGOS;
}

export function BrandMark({ name, label, size = 16, tile = false, muted = false, pop = false, className = "" }: {
  /** A connector name ("stripe") or a client mark ("claude"). */
  name: string;
  /** The display name, for the letter fallback. */
  label?: string;
  /** The logo's box in px; with `tile`, the tile's. */
  size?: number;
  /** On a white rounded tile with a hairline border. */
  tile?: boolean;
  /** Grey, for a system not connected yet. */
  muted?: boolean;
  /** Play the arrival: the system just connected. */
  pop?: boolean;
  className?: string;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const logo = LOGOS[ALIAS[name] ?? name];
  const tone = muted ? "opacity-55 grayscale" : "";
  const motion = pop ? "animate-pop" : "";
  const inner = tile ? Math.round(size * 0.62) : size;

  const mark = logo ? (
    <svg
      aria-hidden
      viewBox={logo.viewBox}
      width={inner}
      height={inner}
      preserveAspectRatio="xMidYMid meet"
      className={`shrink-0 ${tile ? "" : `${tone} ${motion} ${className}`}`}
      // Vendored, static SVG data (brand-logos.ts); gradient ids made unique per drawing.
      dangerouslySetInnerHTML={{ __html: logo.body.replace(/__ID(\d+)__/g, `bm${uid}$1`) }}
    />
  ) : (
    <span
      aria-hidden
      style={{ width: inner, height: inner, fontSize: Math.max(9, Math.round(inner * 0.62)) }}
      className={`inline-flex shrink-0 items-center justify-center rounded-[25%] bg-ink ${tile ? "" : `${tone} ${motion} ${className}`}`}
    >
      <span className="font-semibold leading-none text-surface">{(label ?? name).trim().charAt(0).toUpperCase() || "?"}</span>
    </span>
  );
  if (!tile) return mark;
  return (
    <span
      aria-hidden
      style={{ width: size, height: size }}
      className={`inline-flex shrink-0 items-center justify-center rounded-[22%] border border-line bg-white ${tone} ${motion} ${className}`}
    >
      {mark}
    </span>
  );
}
