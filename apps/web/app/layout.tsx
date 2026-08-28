import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Okwan — Reconciliation as an API",
  description:
    "Define a match once. Okwan gives you a REST endpoint, a SQL view, and an MCP tool for your agents — across payment rails that disagree with the order ledger.",
  metadataBase: new URL("https://okwan.ai"),
  openGraph: {
    title: "Okwan — Reconciliation as an API",
    description:
      "An embeddable reconciliation engine for platforms. One declaration → REST, SQL, and MCP.",
    siteName: "Okwan",
  },
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300;9..144,400;9..144,500;9..144,600&family=Poppins:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
