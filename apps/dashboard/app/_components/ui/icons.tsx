/** Inline stroke icons. Decorative: the control carries the label. */
type P = { className?: string };
const base = {
  width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.75, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true,
};

export const IconHome = ({ className }: P) => (
  <svg {...base} className={className}><path d="M3 10.5 12 3l9 7.5" /><path d="M5 9.5V21h14V9.5" /></svg>
);
export const IconStore = ({ className }: P) => (
  <svg {...base} className={className}><path d="M4 9h16l-1.5-5h-13z" /><path d="M5 9v11h14V9" /><path d="M10 20v-6h4v6" /></svg>
);
export const IconAlert = ({ className }: P) => (
  <svg {...base} className={className}><path d="M12 3 2 20h20z" /><path d="M12 10v4" /><path d="M12 17h.01" /></svg>
);
export const IconKey = ({ className }: P) => (
  <svg {...base} className={className}><circle cx="8" cy="15" r="4" /><path d="m11 12 9-9" /><path d="m17 6 3 3" /></svg>
);
export const IconAgent = ({ className }: P) => (
  <svg {...base} className={className}><rect x="4" y="7" width="16" height="12" rx="3" /><path d="M12 3v4" /><path d="M9 13h.01M15 13h.01" /></svg>
);
export const IconPlug = ({ className }: P) => (
  <svg {...base} className={className}><path d="M9 3v5M15 3v5" /><path d="M6 8h12v3a6 6 0 0 1-12 0z" /><path d="M12 17v4" /></svg>
);
export const IconMenu = ({ className }: P) => (
  <svg {...base} className={className}><path d="M4 7h16M4 12h16M4 17h16" /></svg>
);
export const IconClose = ({ className }: P) => (
  <svg {...base} className={className}><path d="M6 6l12 12M18 6 6 18" /></svg>
);
export const IconCopy = ({ className }: P) => (
  <svg {...base} className={className}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h8" /></svg>
);
export const IconCheck = ({ className }: P) => (
  <svg {...base} className={className}><path d="m5 12 4.5 4.5L19 7" /></svg>
);
export const IconChevron = ({ className }: P) => (
  <svg {...base} className={className}><path d="m9 6 6 6-6 6" /></svg>
);
export const IconPlay = ({ className }: P) => (
  <svg {...base} className={className}><path d="M7 5v14l11-7z" /></svg>
);
export const IconPlus = ({ className }: P) => (
  <svg {...base} className={className}><path d="M12 5v14M5 12h14" /></svg>
);
export const IconSignOut = ({ className }: P) => (
  <svg {...base} className={className}><path d="M15 4h4v16h-4" /><path d="M10 8l-4 4 4 4" /><path d="M6 12h10" /></svg>
);
