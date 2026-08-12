import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Okwan — The data connectivity layer built for AI agents",
  description:
    "Define a connector once. Okwan auto-generates REST endpoints, SQL-queryable tables, and MCP servers so AI agents can read and act on live business data.",
  metadataBase: new URL("https://okwan.ai"),
  openGraph: {
    title: "Okwan — The data connectivity layer built for AI agents",
    description:
      "One connector definition → REST, SQL, and MCP. Open-source core, production-grade connectors.",
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
