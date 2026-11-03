import type { NextConfig } from "next";

// Not a static export, unlike apps/web: sign-in, credential entry and key
// issue all run in route handlers so the session token and every secret
// stay server-side. The browser holds an httpOnly cookie and nothing else.
const nextConfig: NextConfig = {
  poweredByHeader: false,
  // /results became /findings in v1.12; old links and bookmarks follow.
  // The one-home-per-object pages (§9 2026-10-05) redirect with 307 until
  // the owner signs off on the new URLs, then become permanent. The browser
  // keeps a #fragment across a redirect: /catalog#cat-paypal lands on the
  // PayPal tile, whose id is cat-paypal, and /mcp#prompts on the prompts.
  async redirects() {
    return [
      { source: "/results", destination: "/findings", permanent: true },
      { source: "/catalog", destination: "/integrations", permanent: false },
      // The query passes through: /mcp?client=cursor lands on /agents?client=cursor.
      { source: "/mcp", destination: "/agents", permanent: false },
      { source: "/key", destination: "/settings?tab=keys", permanent: false },
      // Workspace is Settings' default tab; ?connect=paypal passes through and opens that sheet.
      { source: "/connections", destination: "/settings", permanent: false },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          // HTTPS only, for two years, on app.okwan.ai and below it.
          { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
          // The policy covers what never needs scripts to be loosened: no
          // framing, no plugins, no foreign <base>, forms post only here.
          // Scripts and styles are left to Next's own handling rather than
          // a nonce scheme this app has no use for yet.
          {
            key: "Content-Security-Policy",
            value: "frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'",
          },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
          // An account's pages are nobody's search result.
          { key: "X-Robots-Tag", value: "noindex, nofollow" },
        ],
      },
    ];
  },
};

export default nextConfig;
