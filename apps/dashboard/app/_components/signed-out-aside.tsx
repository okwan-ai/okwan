/**
 * Beside sign-in and sign-up on wide screens, as the owner drew it: what a
 * check returns, each verdict as a path. An example, labelled as one; no
 * account's data is ever shown before sign-in.
 */
type Shape = "twice" | "once" | "explained" | "unpaid";

const ROWS: { shape: Shape; verdict: string; note: string }[] = [
  { shape: "twice", verdict: "Charged twice", note: "Both rails took the full total. It is owed back." },
  { shape: "once", verdict: "Paid once", note: "Matched on the order reference." },
  { shape: "explained", verdict: "Fee explained", note: "The processor's fee accounts for the gap exactly." },
  { shape: "unpaid", verdict: "No payment", note: "In the ledger, and on no rail that was read." },
];

export function SignedOutAside() {
  return (
    <aside
      aria-label="What a check returns"
      className="relative isolate hidden overflow-hidden rounded-[40px] px-10 pt-12 pb-14 text-white shadow-[0_40px_80px_-40px_rgba(20,16,70,0.7)] lg:block 2xl:px-[50px]"
      style={{
        background:
          "radial-gradient(55% 45% at 100% 0%, rgba(98,44,178,0.7) 0%, rgba(98,44,178,0) 72%)," +
          "radial-gradient(50% 42% at 100% 100%, rgba(112,48,212,0.75) 0%, rgba(112,48,212,0) 72%)," +
          "linear-gradient(165deg, #1e2444 0%, #171c35 52%, #11162e 100%)",
      }}
    >
      <Ribbon />
      <p className="flex items-center gap-3.5 text-[13px] font-medium tracking-[0.12em] text-white/85 uppercase 2xl:text-[16.5px]">
        <span aria-hidden className="h-3 w-3 rounded-full bg-signal" />
        What a check returns – example
      </p>
      <p className="mt-5 text-[30px] leading-[1.2] font-bold tracking-normal 2xl:text-[38px]">
        Every order, traced to the rail that took the money.
      </p>
      <ul className="mt-6 divide-y divide-white/12 border-y border-white/12">
        {ROWS.map((r) => (
          <li key={r.verdict} className="flex items-center gap-7 py-[13px]">
            <Glyph shape={r.shape} />
            <div className="min-w-0">
              <p className="text-[18px] font-bold 2xl:text-[22px]">{r.verdict}</p>
              <p className="mt-0.5 text-[15px] text-white/75 2xl:text-[19.5px]">{r.note}</p>
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-8 inline-flex items-center gap-3.5 rounded-[18px] border border-white/10 bg-white/[0.07] px-[18px] py-[14px] text-[15px] text-white/90 2xl:text-[20px]">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden className="shrink-0">
          <circle cx="12" cy="12" r="9.2" /><path d="M12 11v5.4" /><path d="M12 7.6h.01" strokeWidth="2.6" />
        </svg>
        Read-only by construction: Okwan never moves money.
      </p>
    </aside>
  );
}

/** Each verdict as the owner drew it: white paths, yellow where the money
 *  landed, violet where a fee explains the gap. Decorative; the words say it. */
function Glyph({ shape }: { shape: Shape }) {
  const line = { stroke: "#FFFFFF", strokeWidth: 2.6, fill: "none", strokeLinecap: "round" as const };
  const node = (cx: number, cy: number, fill = "#FFFFFF") => <circle cx={cx} cy={cy} r="7" fill={fill} />;
  let body: React.ReactNode;
  switch (shape) {
    case "twice":
      body = (
        <>
          <path d="M9 24C40 24 54 7 88 7H128" {...line} />
          <path d="M9 24C40 24 54 41 88 41H128" {...line} />
          {node(9, 24)}{node(130, 7, "#F9D205")}{node(130, 41, "#F9D205")}
        </>
      );
      break;
    case "once":
      body = (<><path d="M9 24H128" {...line} />{node(9, 24)}{node(130, 24, "#F9D205")}</>);
      break;
    case "explained":
      body = (
        <>
          <path d="M9 24H128" {...line} />
          <circle cx="70" cy="24" r="10" fill="#4120A8" stroke="#FFFFFF" strokeWidth="2.6" />
          {node(9, 24)}{node(130, 24)}
        </>
      );
      break;
    case "unpaid":
      body = (
        <>
          <path d="M9 24H62" {...line} />
          <path d="M72 24H108" {...line} strokeWidth={2.2} strokeDasharray="3 6" stroke="#9aa0c4" />
          <circle cx="127" cy="24" r="11" {...line} strokeWidth={2.2} />
          <path d="m122.5 19.5 9 9m0-9-9 9" {...line} strokeWidth={2.2} />
          {node(9, 24)}
        </>
      );
      break;
  }
  return (
    <svg aria-hidden viewBox="0 0 140 48" className="h-12 w-[140px] shrink-0 overflow-visible">
      {body}
    </svg>
  );
}

/** The panel's corner light: a thin bright ribbon over a violet glow. */
function Ribbon() {
  return (
    <svg aria-hidden viewBox="0 0 300 160" preserveAspectRatio="none" className="pointer-events-none absolute right-0 bottom-0 -z-10 h-[160px] w-[300px]">
      <defs>
        <linearGradient id="login-ribbon" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0" />
          <stop offset="0.6" stopColor="#d7c6ff" stopOpacity="0.8" />
          <stop offset="1" stopColor="#ffffff" stopOpacity="0.95" />
        </linearGradient>
      </defs>
      <path d="M40 160C130 150 220 110 300 40V70C226 124 150 154 90 160Z" fill="#7b3ff0" fillOpacity="0.28" />
      <path d="M90 158C170 146 240 112 300 64" fill="none" stroke="url(#login-ribbon)" strokeWidth="2" />
      <path d="M120 160C190 148 250 120 300 84" fill="none" stroke="url(#login-ribbon)" strokeWidth="1.2" strokeOpacity="0.6" />
    </svg>
  );
}
