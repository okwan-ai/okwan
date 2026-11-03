import type { Metadata } from "next";
import { apiUrl, tenantTree } from "@/lib/api";
import { railLabel, type RunDigest } from "@/lib/finding";
import { digest, seenFindings, storedRuns } from "@/lib/runs";
import { connectors } from "@/lib/merchants";
import { myUsage } from "@/lib/usage";
import { Sidebar } from "./_components/sidebar";
import { Wordmark } from "./_components/ui/mark";
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
  const [usage, runs, catalog] = tree ? await Promise.all([myUsage(30), storedRuns(), connectors()]) : [null, null, null];
  const verdicts: Record<string, RunDigest> = {};
  for (const r of runs ?? []) {
    const d = digest(r);
    if (d) verdicts[r.merchant.tenant.id] = d;
  }
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400..700&family=JetBrains+Mono:wght@400;500&display=swap"
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
            />
            <main id="main" tabIndex={-1} className="min-w-0 flex-1 outline-none">
              <div className="mx-auto max-w-[1440px] px-4 py-8 sm:px-8 sm:py-10 xl:px-12">{children}</div>
            </main>
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
    <>
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-[1120px] items-center px-4 py-3 sm:px-8">
          <a href="https://okwan.ai" className="flex min-h-11 items-center">
            <Wordmark />
          </a>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto max-w-[1120px] px-4 py-12 outline-none sm:px-8 lg:py-16">{children}</main>
    </>
  );
}
