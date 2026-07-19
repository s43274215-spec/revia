import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    const backend = (
      process.env.REVIA_API_BASE_URL
      ?? process.env.NEXT_PUBLIC_API_BASE_URL
      ?? "http://127.0.0.1:8000/api/v1"
    ).replace(/\/$/, "");
    return [{ source: "/api/backend/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
