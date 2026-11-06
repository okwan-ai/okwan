/* The brand pieces every page of the site shares. */

/* The Okwan logo (§9 2026-11-06), from the owner's own design: "Okwan" in
 * Figtree ExtraBold (SIL Open Font License) as outlines, so it never waits
 * on a font, over the yellow swoosh fitted to the owner's mockup. One
 * drawing everywhere: the dashboard, the site, the browser-tab icon. */
const WORD = "M398 12Q290 12 207 -34Q124 -81 77 -162Q30 -244 30 -349Q30 -455 77 -536Q124 -618 206 -664Q289 -711 396 -711Q503 -711 586 -664Q668 -618 715 -536Q762 -455 762 -349Q762 -244 716 -162Q669 -81 587 -34Q505 12 398 12ZM398 -136Q457 -136 502 -163Q547 -190 572 -238Q597 -287 597 -349Q597 -412 571 -460Q545 -509 500 -536Q455 -564 396 -564Q337 -564 292 -536Q247 -509 221 -460Q195 -412 195 -349Q195 -287 221 -238Q247 -190 293 -163Q339 -136 398 -136ZM843 0V-700H999V-332L1150 -500H1333L1144 -284L1346 0H1165L1045 -171L999 -120V0ZM1534 0 1364 -500H1523L1615 -208L1707 -500H1861L1950 -215L2044 -500H2203L2032 0H1879L1783 -313L1687 0ZM2407 12Q2318 12 2269 -28Q2220 -68 2220 -141Q2220 -220 2271 -259Q2322 -298 2414 -298H2536Q2531 -338 2512 -360Q2494 -383 2453 -383Q2425 -383 2404 -370Q2383 -357 2368 -334L2232 -379Q2249 -416 2278 -446Q2308 -476 2351 -494Q2394 -512 2451 -512Q2529 -512 2583 -483Q2637 -454 2664 -402Q2691 -350 2691 -279L2689 0H2543L2541 -51Q2521 -20 2487 -4Q2453 12 2407 12ZM2441 -106Q2470 -106 2492 -118Q2514 -130 2526 -150Q2539 -171 2539 -195V-198H2473Q2423 -198 2401 -184Q2379 -170 2379 -145Q2379 -127 2396 -116Q2412 -106 2441 -106ZM2786 0V-500H2933L2939 -436Q2964 -472 3000 -492Q3036 -512 3081 -512Q3141 -512 3182 -488Q3223 -463 3244 -413Q3264 -363 3264 -285V0H3108V-256Q3108 -296 3099 -320Q3090 -343 3074 -354Q3057 -365 3033 -366Q2988 -367 2965 -342Q2942 -316 2942 -264V0Z";
const SWOOSH = "M365 641C1088 324 1900 299 2651 532";
const O = "M398 12Q290 12 207 -34Q124 -81 77 -162Q30 -244 30 -349Q30 -455 77 -536Q124 -618 206 -664Q289 -711 396 -711Q503 -711 586 -664Q668 -618 715 -536Q762 -455 762 -349Q762 -244 716 -162Q669 -81 587 -34Q505 12 398 12ZM398 -136Q457 -136 502 -163Q547 -190 572 -238Q597 -287 597 -349Q597 -412 571 -460Q545 -509 500 -536Q455 -564 396 -564Q337 -564 292 -536Q247 -509 221 -460Q195 -412 195 -349Q195 -287 221 -238Q247 -190 293 -163Q339 -136 398 -136Z";
export const LOGO_INK = "#0C0A2E";
export const LOGO_SWOOSH = "#FBCC0E";

/** The wordmark. `width` in CSS pixels; the height follows (2.28:1).
 *  `light` for dark grounds: white letters, the same swoosh. */
export function OkwanLogo({ width = 116, light = false, title }: { width?: number; light?: boolean; title?: string }) {
  return (
    <svg
      viewBox="30 -711 3234 1416"
      width={width}
      height={Math.round((width * 1416) / 3234)}
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      className="block shrink-0"
    >
      <path d={WORD} fill={light ? "#FFFFFF" : LOGO_INK} />
      <path d={SWOOSH} fill="none" stroke={LOGO_SWOOSH} strokeWidth="125" strokeLinecap="round" />
    </svg>
  );
}

/** The square mark for small places: the logo's own O over its swoosh. */
export function OkwanMark({ size = 32, light = false }: { size?: number; light?: boolean }) {
  return (
    <svg viewBox="0 0 128 128" width={size} height={size} aria-hidden="true" className="block shrink-0">
      <g transform="translate(64 0) scale(0.0874) translate(-396 940)">
        <path d={O} fill={light ? "#FFFFFF" : LOGO_INK} />
      </g>
      <path d="M30 107C50 97 78 94 100 100" fill="none" stroke={LOGO_SWOOSH} strokeWidth="9" strokeLinecap="round" />
    </svg>
  );
}

export function Wrap({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <div className={`mx-auto w-full max-w-[1440px] px-6 sm:px-8 lg:px-12 xl:px-16 ${className}`}>{children}</div>;
}
