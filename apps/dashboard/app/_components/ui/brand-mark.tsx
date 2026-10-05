import { type AnimationEvent, useId } from "react";
import { LOGOS } from "./brand-logos";

/**
 * The logo of a connected system, beside its name: never instead of it.
 *
 * Full colour, as its owner draws it (the owner's call, OKWAN_PROJECT.md §9
 * 2026-10-05, which supersedes the monochrome entry of 2026-10-04): a
 * connected PayPal shows PayPal's own logo. On a white tile (`tile`) with a
 * hairline border, 22% radius, the logo at 62% of the tile, so a third
 * party's colours sit on neutral ground and never on volt, which stays
 * Okwan's only accent (§2). Tiles of 40, 32 or 24px and a bare 16px logo;
 * nothing smaller.
 *
 * States, wherever a tenant is in view:
 * - `muted`: not connected (or partly). Only the inner logo goes grey at
 *   45% opacity; the tile border turns dashed `--line-strong` at full
 *   strength (3.5:1 on surface), so the state does not rely on colour
 *   (WCAG 1.4.1, 1.4.11).
 * - `corner`: a disc from this tab's last test, "ok" ✓ or "alert" !.
 * - `pop`: the 420ms arrival, once, on the connect that caused it. The
 *   global prefers-reduced-motion rule turns it off; the state still
 *   arrives. `onAnimationEnd` reports the tile's own animation only, never
 *   the disc's bubbled one.
 *
 * Each logo is its owner's trademark, shown to name the integration it
 * identifies. Whether each owner's brand rules permit this use is a legal
 * item, flagged for the owner (§10 item 25), not something this code
 * decides. Brand pages: Shopify shopify.com/brand-assets · PayPal
 * newsroom.paypal-corp.com/media-resources · Stripe stripe.com/newsroom/brand-assets
 * · PostgreSQL postgresql.org/about/policies/trademarks · WhatsApp
 * about.meta.com/brand/resources/whatsapp · Claude anthropic.com · Cursor cursor.com.
 *
 * A system with no logo here (Paystack today) gets an ink initial on the
 * white tile (ink-outlined when bare), never a filled square, so it takes
 * the same states; adding a logo is one entry in brand-logos.ts. The mark
 * is decorative (`aria-hidden`); the name beside it is the text. Never on
 * volt, in a finding sentence, in Okwan's own branding or on signed-out pages.
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

export function BrandMark({
  name,
  label,
  size = 16,
  tile = false,
  muted = false,
  pop = false,
  corner,
  onAnimationEnd,
  className = "",
}: {
  /** A connector name ("stripe") or a client mark ("claude"). */
  name: string;
  /** The display name, for the letter fallback. */
  label?: string;
  /** The logo's box in px; with `tile`, the tile's (40, 32 or 24). */
  size?: number;
  /** On a white rounded tile with a hairline border. */
  tile?: boolean;
  /** Not connected: grey inner logo, dashed tile border. */
  muted?: boolean;
  /** Play the arrival: the system just connected. */
  pop?: boolean;
  /** A disc in the tile's corner from this tab's last test. Tiles only. */
  corner?: "ok" | "alert";
  /** The arrival finished (the tile's own animation, not the disc's). */
  onAnimationEnd?: () => void;
  className?: string;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const logo = LOGOS[ALIAS[name] ?? name];
  const tone = muted ? "opacity-45 grayscale" : "";
  const motion = pop ? "animate-pop" : "";
  const inner = tile ? Math.round(size * 0.62) : size;
  const initial = (label ?? name).trim().charAt(0).toUpperCase() || "?";
  // Only a client caller can pass a callback, so a server render never
  // serializes a handler. Bubbled events (the disc's) are ignored.
  const ended = onAnimationEnd
    ? (e: AnimationEvent<HTMLElement | SVGSVGElement>) => {
        if (e.target === e.currentTarget) onAnimationEnd();
      }
    : undefined;

  if (!tile) {
    return logo ? (
      <svg
        aria-hidden
        viewBox={logo.viewBox}
        width={size}
        height={size}
        preserveAspectRatio="xMidYMid meet"
        onAnimationEnd={ended}
        className={`shrink-0 ${tone} ${motion} ${className}`}
        // Vendored, static SVG data (brand-logos.ts); gradient ids made unique per drawing.
        dangerouslySetInnerHTML={{ __html: logo.body.replace(/__ID(\d+)__/g, `bm${uid}$1`) }}
      />
    ) : (
      <span
        aria-hidden
        style={{ width: size, height: size, fontSize: Math.max(9, Math.round(size * 0.62)) }}
        onAnimationEnd={ended}
        className={`inline-flex shrink-0 items-center justify-center rounded-[25%] border border-ink font-semibold leading-none text-ink ${tone} ${motion} ${className}`}
      >
        {initial}
      </span>
    );
  }

  const mark = logo ? (
    <svg
      aria-hidden
      viewBox={logo.viewBox}
      width={inner}
      height={inner}
      preserveAspectRatio="xMidYMid meet"
      className={`shrink-0 ${tone}`}
      dangerouslySetInnerHTML={{ __html: logo.body.replace(/__ID(\d+)__/g, `bm${uid}$1`) }}
    />
  ) : (
    <span aria-hidden className={`font-semibold leading-none text-ink ${tone}`} style={{ fontSize: Math.round(size * 0.45) }}>
      {initial}
    </span>
  );
  return (
    <span
      aria-hidden
      style={{ width: size, height: size }}
      onAnimationEnd={ended}
      className={`relative inline-flex shrink-0 items-center justify-center rounded-[22%] border bg-white ${muted ? "border-dashed border-line-strong" : "border-line"} ${motion} ${className}`}
    >
      {mark}
      {corner && (
        <span
          aria-hidden
          className={`absolute -right-1 -bottom-1 inline-flex size-4 items-center justify-center rounded-full text-[11px] leading-none font-semibold text-white ring-2 ring-surface ${corner === "ok" ? "bg-ok" : "bg-danger"} ${pop && corner === "ok" ? "animate-pop [animation-delay:180ms]" : ""}`}
        >
          {corner === "ok" ? "✓" : "!"}
        </span>
      )}
    </span>
  );
}
