/**
 * A verdict drawn as a path, the public site's signature (§2.4). Okwan is
 * Twi for "path": one line when two records agree, a fork when one order
 * was paid on two rails, a break with the signal dot where nothing accounts
 * for a gap, a path that runs out where the other side has nothing.
 *
 * Decorative: the verdict is always also in text beside it.
 */
export type PathShape = "once" | "twice" | "explained" | "unexplained" | "ambiguous" | "unpaid" | "orphan" | "unverifiable";

const SIGNAL = "#FFD400";

export function PathGlyph({ shape, light = false, className = "h-11 w-28", animate = true }: {
  shape: PathShape;
  /** Drawn on night: white strokes. */
  light?: boolean;
  className?: string;
  animate?: boolean;
}) {
  const ink = light ? "#FFFFFF" : "#0A0A0A";
  const soft = light ? "#737373" : "#A3A3A3";
  const paper = light ? "#0B0B0C" : "#FFFFFF";
  const node = (cx: number, cy: number, fill = ink, stroke = "none", dashed = false) => (
    <circle cx={cx} cy={cy} r="3.6" fill={fill} stroke={stroke} strokeWidth="1.5" strokeDasharray={dashed ? "2 2" : undefined} />
  );
  const line = { stroke: ink, strokeWidth: 1.6, fill: "none", strokeLinecap: "round" as const };
  const faint = { stroke: soft, strokeWidth: 1.6, fill: "none", strokeDasharray: "3 4", strokeLinecap: "round" as const };

  let body: React.ReactNode;
  switch (shape) {
    case "once":
      body = (<><path d="M10 22H102" {...line} />{node(6, 22)}{node(106, 22)}</>);
      break;
    case "twice":
      body = (
        <>
          <path d="M10 22C44 22 58 9 102 9" {...line} />
          <path d="M10 22C44 22 58 35 102 35" {...line} />
          {node(6, 22)}
          {node(106, 9, SIGNAL, light ? "none" : ink)}
          {node(106, 35, SIGNAL, light ? "none" : ink)}
        </>
      );
      break;
    case "explained":
      body = (
        <>
          <path d="M10 22H102" {...line} />
          <rect x="52" y="18" width="8" height="8" transform="rotate(45 56 22)" fill={paper} stroke={ink} strokeWidth="1.5" />
          {node(6, 22)}{node(106, 22)}
        </>
      );
      break;
    case "unexplained":
      body = (
        <>
          <path d="M10 22H47M65 22H102" {...line} />
          <circle cx="56" cy="22" r="5" fill={SIGNAL} stroke={light ? "none" : ink} strokeWidth="1.5" />
          {node(6, 22)}{node(106, 22)}
        </>
      );
      break;
    case "ambiguous":
      body = (
        <>
          <path d="M10 22H40" {...line} />
          <path d="M40 22C62 22 70 9 102 9" {...faint} stroke={ink} />
          <path d="M40 22C62 22 70 35 102 35" {...faint} stroke={ink} />
          {node(6, 22)}{node(106, 9, paper, ink)}{node(106, 35, paper, ink)}
        </>
      );
      break;
    case "unpaid":
      body = (<><path d="M10 22H60" {...line} /><path d="M66 22H100" {...faint} />{node(6, 22)}{node(106, 22, paper, soft, true)}</>);
      break;
    case "orphan":
      body = (<><path d="M12 22H46" {...faint} /><path d="M52 22H102" {...line} />{node(6, 22, paper, soft, true)}{node(106, 22)}</>);
      break;
    case "unverifiable":
      body = (
        <>
          <path d="M10 22H100" {...faint} />
          {node(6, 22)}
          <circle cx="106" cy="22" r="5" fill={paper} stroke={soft} strokeWidth="1.5" />
        </>
      );
      break;
  }
  return (
    <svg viewBox="0 0 112 44" className={`shrink-0 ${className}`} aria-hidden="true">
      <g className={animate ? "animate-draw" : undefined}>{body}</g>
    </svg>
  );
}
