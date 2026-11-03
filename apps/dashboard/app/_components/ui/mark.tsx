/**
 * Okwan's mark and wordmark: the same drawing as the public site. Two
 * strokes for the systems, the signal dot for where they disagree. Never
 * a third party's logo, and never the only thing naming the product.
 */
export function Mark({ size = 22, light = false }: { size?: number; light?: boolean }) {
  const stroke = light ? "#FFFFFF" : "#0A0A0A";
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="shrink-0">
      <path d="M6 8h20" stroke={stroke} strokeWidth="3.5" strokeLinecap="round" />
      <path d="M6 24h20" stroke={stroke} strokeWidth="3.5" strokeLinecap="round" />
      <circle cx="16" cy="16" r="4.5" fill="#FFD400" stroke={light ? "none" : stroke} strokeWidth="2" />
    </svg>
  );
}

export function Wordmark({ light = false, size = 22 }: { light?: boolean; size?: number }) {
  return (
    <span className="flex items-center gap-2">
      <Mark size={size} light={light} />
      <span className={`text-[19px] font-semibold tracking-[-0.02em] ${light ? "text-white" : "text-ink"}`}>okwan</span>
    </span>
  );
}
