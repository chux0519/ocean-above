import type { NextConfig } from "next";

const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  devIndicators: false, // keeps dev screenshots clean
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }];
  },
  // Google Identity Services opens a popup that must be able to post the credential back to this page.
  async headers() {
    return [{ source: "/:path*", headers: [{ key: "Cross-Origin-Opener-Policy", value: "same-origin-allow-popups" }] }];
  },
};
export default config;
