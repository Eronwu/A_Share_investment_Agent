import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  serverExternalPackages: ["better-sqlite3"],
  webpack: (config, { dev }) => {
    if (dev) {
      config.cache = { type: "memory" as const };
    }
    return config;
  },
};

export default nextConfig;
