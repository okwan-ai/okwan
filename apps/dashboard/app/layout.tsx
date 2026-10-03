import type { Metadata } from "next";
import Link from "next/link";
import { me } from "@/lib/api";
import { SignOut } from "./_components/sign-out";
import "./globals.css";

export const metadata: Metadata = {
  title: "Okwan dashboard",
  description: "Connect your payment rails and issue an API key.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const tenant = await me();
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300;9..144,400;9..144,500;9..144,600&family=Poppins:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen">
        <header className="border-b border-line">
          <div className="mx-auto flex max-w-[920px] items-center justify-between gap-4 px-6 py-4">
            <Link href="/" className="flex items-center gap-2">
              <span className="inline-block h-6 w-6 rounded-md bg-volt ring-1 ring-ink" aria-hidden />
              <span className="font-display text-xl font-medium">Okwan</span>
            </Link>
            {tenant && (
              <nav className="flex items-center gap-5 text-sm text-ink-soft">
                <Link href="/connections" className="hover:text-ink">Connections</Link>
                <Link href="/merchants" className="hover:text-ink">Merchants</Link>
                <Link href="/key" className="hover:text-ink">API key</Link>
                <Link href="/results" className="hover:text-ink">Results</Link>
                <SignOut />
              </nav>
            )}
          </div>
        </header>
        <main className="mx-auto max-w-[920px] px-6 py-12">{children}</main>
      </body>
    </html>
  );
}
