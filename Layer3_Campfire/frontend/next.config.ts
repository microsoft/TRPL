import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ['127.0.0.1'],

  // Output standalone build for Azure App Service deployment
  // This creates a self-contained server with minimal node_modules
  output: 'standalone',

  // Image optimization - gallery images are static artifacts that rarely change
  images: {
    // WebP only — AVIF decodes progressively (visible tile-by-tile loading)
    // which looks worse in a gallery that loads many images at once.
    formats: ['image/webp'],
    // Cache optimized images for 7 days (static gallery content)
    minimumCacheTTL: 604800,
  },

  // Enable React Compiler for automatic memoization (moved from experimental in Next.js 16)
  reactCompiler: true,

  // Turbopack configuration (Next.js 16+ default bundler)
  turbopack: {
    // Externalize ws native dependencies for server-side code
    resolveExtensions: ['.tsx', '.ts', '.jsx', '.js', '.json'],
  },

  // Keep webpack config for fallback compatibility
  webpack: (config, { isServer }) => {
    if (isServer) {
      // Externalize ws and its native dependencies for server-side code
      config.externals = config.externals || [];
      config.externals.push({
        'bufferutil': 'bufferutil',
        'utf-8-validate': 'utf-8-validate',
      });
    }
    return config;
  },
};

export default nextConfig;
