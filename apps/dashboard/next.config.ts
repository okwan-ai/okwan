import type { NextConfig } from "next";

// Not a static export, unlike apps/web: sign-in, credential entry and key
// issue all run in route handlers so the session token and every secret
// stay server-side. The browser holds an httpOnly cookie and nothing else.
const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
        ],
      },
    ];
  },
};

export default nextConfig;
