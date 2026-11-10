import type { NextConfig } from 'next';

// Static export (out/) for GitHub Pages. Pages serves a project site under /<repo>, so the
// deploy workflow sets NEXT_PUBLIC_BASE_PATH (empty for a custom domain or local preview).
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? '';

const nextConfig: NextConfig = {
  output: 'export',
  basePath,
  trailingSlash: true,
  images: {
    // no image optimization server on a static host
    unoptimized: true,
    remotePatterns: [
      { protocol: 'https', hostname: 'github.com', pathname: '/**' },
      { protocol: 'https', hostname: 'avatars.githubusercontent.com', pathname: '/**' },
    ],
  },
};

export default nextConfig;
