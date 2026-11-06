import type { Metadata } from "next";
import { apiUrl, tenantTree } from "@/lib/api";
import { railLabel, type RunDigest } from "@/lib/finding";
import { digest, seenFindings, storedRuns } from "@/lib/runs";
import { connectors } from "@/lib/merchants";
import { operatorMe } from "@/lib/ops";
import { myUsage } from "@/lib/usage";
import { Sidebar } from "./_components/sidebar";
import { IdleGuard } from "./_components/idle-guard";
import { SupportPanel } from "./_components/support-panel";
import { TopBar } from "./_components/top-bar";
import { OkwanLogo } from "./_components/ui/mark";
import "./globals.css";

export const metadata: Metadata = {
  // Each page names itself, so client navigation is announced (WCAG 2.4.2).
  title: { template: "%s · Okwan", default: "Okwan" },
  description: "Read-only reconciliation across Shopify, PayPal and Stripe.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const tree = await tenantTree();
  // The meter, the verdicts and the connector names in the sidebar:
  // unmetered reads per render (the pages share them through React's
  // cache), never a run.
  const [usage, runs, catalog, op] = tree
    ? await Promise.all([myUsage(30), storedRuns(), connectors(), operatorMe()])
    : [null, null, null, null];
  const verdicts: Record<string, RunDigest> = {};
  for (const r of runs ?? []) {
    const d = digest(r);
    if (d) verdicts[r.merchant.tenant.id] = d;
  }
  const openFindings = Object.values(verdicts).filter((d) => d.ok).reduce((n, d) => n + d.open, 0);
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Figtree:wght@400..800&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen">
        <a
          href="#main"
          className="sr-only rounded-lg bg-ink px-4 py-3 text-canvas focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[70]"
        >
          Skip to content
        </a>
        {tree ? (
          <div className="min-[900px]:flex">
            <Sidebar
              tenant={tree.self.name}
              merchants={tree.children.map((c) => ({ id: c.id, name: c.name }))}
              connectors={catalog?.ok ? catalog.data.map((c) => ({ name: c.name, label: railLabel(c.name) })) : []}
              plan={usage?.plan ?? null}
              apiBase={apiUrl()}
              verdicts={verdicts}
              findings={runs ? seenFindings(runs) : []}
              operator={op !== null}
            />
            <main id="main" tabIndex={-1} className="min-w-0 flex-1 outline-none">
              {/* From 2xl the page sits between the sidebar and the docked
                  support column, its title level with the panel's, as drawn. */}
              <div className="mx-auto max-w-[1440px] px-4 pt-6 pb-12 sm:px-8 min-[900px]:pt-0 xl:px-10 2xl:max-w-none 2xl:px-0 2xl:pt-[104px] 2xl:pl-3">
                <TopBar account={tree.self.name} openFindings={openFindings} />
                {children}
              </div>
            </main>
            <SupportPanel topBar={<TopBar account={tree.self.name} openFindings={openFindings} docked />} />
            <IdleGuard />
          </div>
        ) : (
          <SignedOut>{children}</SignedOut>
        )}
      </body>
    </html>
  );
}

function SignedOut({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative isolate min-h-screen overflow-hidden">
      <SignedOutBackdrop />
      <header className="mx-auto max-w-[1440px] px-5 pt-8 sm:px-8 lg:pt-10 2xl:pr-0">
        <a href="https://okwan.ai" aria-label="Okwan home" className="inline-flex min-h-11 items-center">
          <span className="sm:hidden"><OkwanLogo width={120} /></span>
          <span className="hidden sm:block"><OkwanLogo width={160} /></span>
        </a>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto max-w-[1440px] px-5 pt-10 pb-16 outline-none sm:px-8 lg:pt-[43px] 2xl:pr-0">
        {children}
      </main>
    </div>
  );
}

/** Sign-in's own light, as the owner drew it: lavender glowing in from the
 *  top right, a soft wave along the bottom left, warm gold at the bottom
 *  right. Decoration only. */
function SignedOutBackdrop() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(520px 420px at 102% -4%, #c9b8fc 0%, rgba(201,184,252,0) 72%)," +
            "radial-gradient(640px 420px at -6% -8%, #fdf3e6 0%, rgba(253,243,230,0) 72%)," +
            "radial-gradient(620px 360px at 100% 104%, #fde2ad 0%, rgba(253,226,173,0) 70%)," +
            "radial-gradient(700px 420px at -4% 104%, #ddd4fd 0%, rgba(221,212,253,0) 70%)," +
            "#f5f3fd",
        }}
      />
      <svg viewBox="0 0 1672 941" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
        <defs>
          <linearGradient id="wave-lav" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#ffffff" stopOpacity="0.9" />
            <stop offset="0.5" stopColor="#d9cffd" stopOpacity="0.75" />
            <stop offset="1" stopColor="#b9a6fb" stopOpacity="0.55" />
          </linearGradient>
          <linearGradient id="wave-gold" x1="0" y1="1" x2="1" y2="0">
            <stop offset="0" stopColor="#ffffff" stopOpacity="0" />
            <stop offset="1" stopColor="#fbd88f" stopOpacity="0.7" />
          </linearGradient>
        </defs>
        <path d="M0 690C120 720 260 800 360 941H0Z" fill="url(#wave-lav)" />
        <path d="M0 750C150 770 330 850 430 941H360C270 860 130 790 0 770Z" fill="#ffffff" fillOpacity="0.6" />
        <path d="M1230 112C1300 80 1360 40 1410 0" fill="none" stroke="#ffffff" strokeWidth="2" strokeOpacity="0.9" />
        <path d="M1250 941C1390 900 1540 870 1672 862V941Z" fill="url(#wave-gold)" />
      </svg>
    </div>
  );
}
