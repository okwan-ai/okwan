import type { Metadata } from "next";
import "./globals.css";

const TITLE = "Okwan — Find where your systems disagree";
const DESCRIPTION =
  "Reconciliation infrastructure for retail and payments platforms. Okwan matches POS, processor, order and bank records and says what agrees, what differs and why, as an API, a SQL view and an agent tool from one definition.";

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  metadataBase: new URL("https://okwan.ai"),
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    siteName: "Okwan",
  },
  twitter: { card: "summary", title: TITLE, description: DESCRIPTION },
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
          href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400..700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
