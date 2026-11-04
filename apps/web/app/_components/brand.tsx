/* The brand pieces every page of the site shares. */

export function Mark({ size = 24, light = false }: { size?: number; light?: boolean }) {
  const stroke = light ? "#FFFFFF" : "#0A0A0A";
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M6 8h20" stroke={stroke} strokeWidth="3.5" strokeLinecap="round" />
      <path d="M6 24h20" stroke={stroke} strokeWidth="3.5" strokeLinecap="round" />
      <circle cx="16" cy="16" r="4.5" fill="#FFD400" stroke={light ? "none" : stroke} strokeWidth="2" />
    </svg>
  );
}

export function Wordmark({ light = false }: { light?: boolean }) {
  return (
    <span className="flex items-center gap-2.5">
      <Mark light={light} />
      <span className={`text-[21px] font-semibold tracking-[-0.02em] ${light ? "text-white" : "text-ink"}`}>
        okwan
      </span>
    </span>
  );
}

export function Wrap({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`mx-auto w-full max-w-[1440px] px-6 sm:px-8 lg:px-12 xl:px-16 ${className}`}>{children}</div>;
}
