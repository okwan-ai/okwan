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

/** The dashboard's wordmark (§9 2026-11-06): "Okwan" over a yellow swoosh,
 *  the path between two systems. The swoosh is decoration; the word names
 *  the product. */
export function SwooshWordmark({ size = 30 }: { size?: number }) {
  return (
    <span className="inline-flex flex-col items-start leading-none">
      <span className="font-bold tracking-[-0.03em] text-ink" style={{ fontSize: size }}>Okwan</span>
      <svg aria-hidden="true" viewBox="0 0 120 16" width={size * 3.1} height={size * 0.42} className="-mt-0.5 ml-[6%]">
        <path d="M4 10 C 32 2, 70 2, 96 6 S 114 12, 116 10" fill="none" stroke="#FFC61A" strokeWidth="5" strokeLinecap="round" />
      </svg>
    </span>
  );
}
