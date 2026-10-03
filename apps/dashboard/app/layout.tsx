import type { Metadata } from "next";
import { tenantTree } from "@/lib/api";
import { Sidebar } from "./_components/sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Okwan dashboard",
  description: "Connect your payment rails and issue an API key.",
  robots: { index: false, follow: false },
};

export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const tree = await tenantTree();
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
        {tree ? (
          <div className="min-[900px]:flex">
            <Sidebar
              tenant={tree.self.name}
              merchants={tree.children.length}
            />
            <main className="min-w-0 flex-1">
              <div className="mx-auto max-w-[1120px] px-4 py-8 sm:px-8 sm:py-10">{children}</div>
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
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-[920px] items-center px-4 py-4 sm:px-6">
          <a href="/signup" className="flex min-h-11 items-center gap-2">
            <span className="inline-block h-6 w-6 rounded-md bg-volt ring-1 ring-ink" aria-hidden />
            <span className="font-display text-xl font-medium">Okwan</span>
          </a>
        </div>
      </header>
      <main className="mx-auto max-w-[920px] px-4 py-12 sm:px-6">{children}</main>
    </>
  );
}
